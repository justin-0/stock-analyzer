import yfinance as yf
import pandas as pd
import numpy as np
from ta.trend import MACD, EMAIndicator, SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from django.conf import settings
from django.utils import timezone
from .models import StockCache
from datetime import datetime, timedelta
import requests
import logging
import warnings

# Suppress yfinance noisy warnings
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.filterwarnings('ignore', category=FutureWarning)


def get_cached_data(symbol, data_type):
    try:
        cache = StockCache.objects.get(symbol=symbol, data_type=data_type)
        age = (timezone.now() - cache.cached_at).total_seconds()
        if age < settings.CACHE_TIMEOUT:
            return cache.data
        return None
    except StockCache.DoesNotExist:
        return None


def set_cache(symbol, data_type, data):
    StockCache.objects.update_or_create(
        symbol=symbol, data_type=data_type,
        defaults={'data': data}
    )


def _clean_symbol(symbol):
    """Strip unwanted chars like $ prefix from symbols."""
    return symbol.strip().lstrip('$').upper()


def to_yf_symbol(symbol):
    symbol = _clean_symbol(symbol)
    if symbol.endswith('.NS') or symbol.endswith('.BO'):
        return symbol
    if symbol in ['^NSEI', '^BSESN', 'NIFTY', 'SENSEX']:
        return '^NSEI' if 'NIFTY' in symbol else '^BSESN'
    return symbol + '.NS'


UPSTOX_TOKENS = {
    'RELIANCE':'NSE_EQ|INE002A01018','TCS':'NSE_EQ|INE467B01029',
    'INFY':'NSE_EQ|INE009A01021','HDFCBANK':'NSE_EQ|INE040A01034',
    'ICICIBANK':'NSE_EQ|INE090A01021','SBIN':'NSE_EQ|INE062A01020',
    'WIPRO':'NSE_EQ|INE075A01022','BAJFINANCE':'NSE_EQ|INE296A01024',
    'MARUTI':'NSE_EQ|INE585B01010','TATAMOTORS':'NSE_EQ|INE155A01022',
    'AXISBANK':'NSE_EQ|INE238A01034','KOTAKBANK':'NSE_EQ|INE237A01028',
    'HINDUNILVR':'NSE_EQ|INE030A01027','BHARTIARTL':'NSE_EQ|INE397D01024',
    'ITC':'NSE_EQ|INE154A01025','ONGC':'NSE_EQ|INE213A01029',
    'POWERGRID':'NSE_EQ|INE752E01010','NTPC':'NSE_EQ|INE733E01010',
    'ULTRACEMCO':'NSE_EQ|INE481G01011','SUNPHARMA':'NSE_EQ|INE044A01036',
    'ADANIENT':'NSE_EQ|INE423A01024','ADANIPORTS':'NSE_EQ|INE742F01042',
    'ASIANPAINT':'NSE_EQ|INE021A01026','BAJAJFINSV':'NSE_EQ|INE918I01026',
    'HCLTECH':'NSE_EQ|INE860A01027','INDUSINDBK':'NSE_EQ|INE095A01012',
    'JSWSTEEL':'NSE_EQ|INE019A01038','LT':'NSE_EQ|INE018A01030',
    'NESTLEIND':'NSE_EQ|INE239A01024','TATASTEEL':'NSE_EQ|INE081A01020',
    'TECHM':'NSE_EQ|INE669C01036','TITAN':'NSE_EQ|INE280A01028',
    'IRCTC':'NSE_EQ|INE335Y01012','DRREDDY':'NSE_EQ|INE089A01023',
    'CIPLA':'NSE_EQ|INE059A01026','DIVISLAB':'NSE_EQ|INE361B01024',
    'APOLLOHOSP':'NSE_EQ|INE437A01024','PIDILITIND':'NSE_EQ|INE318A01026',
    'HAVELLS':'NSE_EQ|INE176B01034','DABUR':'NSE_EQ|INE016A01026',
    'MARICO':'NSE_EQ|INE196A01026','MUTHOOTFIN':'NSE_EQ|INE414G01012',
    'CHOLAFIN':'NSE_EQ|INE121A01024','BALKRISIND':'NSE_EQ|INE787D01026',
    'ZYDUSLIFE':'NSE_EQ|INE010B01027','TATACONSUM':'NSE_EQ|INE192A01025',
    'SIEMENS':'NSE_EQ|INE003A01024','BERGEPAINT':'NSE_EQ|INE463A01038',
    'GODREJCP':'NSE_EQ|INE102D01028','MOTHERSON':'NSE_EQ|INE775A01035',
}



# ── 30-second in-memory price cache ──────────────────────────────────────────
# Shared across all requests in the same worker process.
# Prevents 10 users hitting /trades/ from each firing their own Upstox API calls.
import time as _time
_PRICE_CACHE: dict = {}          # { symbol: (price_dict, timestamp) }
_PRICE_CACHE_TTL  = 8            # seconds — fast enough to feel live, slow enough to protect API


def _price_cache_get(symbol):
    entry = _PRICE_CACHE.get(symbol)
    if entry and (_time.time() - entry[1]) < _PRICE_CACHE_TTL:
        return entry[0]
    return None


def _price_cache_set(symbol, data):
    _PRICE_CACHE[symbol] = (data, _time.time())


def get_upstox_quotes_bulk(symbols, access_token=None):
    """
    Fetch live LTP + OHLC for a LIST of symbols in exactly 2 HTTP calls total.
    Returns dict: { symbol: quote_dict }
    Symbols not in UPSTOX_TOKENS are skipped (caller should fall back to yfinance).
    Results are cached for 30s — safe for multi-user concurrent access.
    """
    if not symbols:
        return {}

    access_token = access_token or getattr(settings, 'UPSTOX_ACCESS_TOKEN', '')
    if not access_token:
        return {}

    # Split into cached vs needs-fetch
    results   = {}
    to_fetch  = []
    for sym in symbols:
        sym = sym.upper().strip()
        cached = _price_cache_get(sym)
        if cached:
            results[sym] = cached
        elif UPSTOX_TOKENS.get(sym):
            to_fetch.append(sym)

    if not to_fetch:
        return results

    # Build comma-separated token key string for all symbols needing fetch
    token_keys = ','.join(UPSTOX_TOKENS[s] for s in to_fetch)
    headers    = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}
    params     = {'symbol': token_keys}

    try:
        # ONE LTP call + ONE OHLC call — regardless of how many symbols
        ltp_r  = requests.get('https://api.upstox.com/v2/market-quote/ltp',
                               headers=headers, params=params, timeout=6)
        ohlc_r = requests.get('https://api.upstox.com/v2/market-quote/ohlc',
                               headers=headers, params=params, timeout=6)

        ltp_data  = ltp_r.json().get('data', {})  if ltp_r.status_code  == 200 else {}
        ohlc_data = ohlc_r.json().get('data', {}) if ohlc_r.status_code == 200 else {}

        # Build reverse map: upstox_key → symbol
        key_to_sym = {v: k for k, v in UPSTOX_TOKENS.items()}

        for upstox_key, ltp_entry in ltp_data.items():
            sym = key_to_sym.get(upstox_key)
            if not sym or sym not in to_fetch:
                continue
            try:
                current  = round(float(ltp_entry['last_price']), 2)
                ohlc_sym = ohlc_data.get(upstox_key, {})
                ohlc     = ohlc_sym.get('ohlc', {})
                open_p   = round(float(ohlc.get('open',  current)), 2)
                high_p   = round(float(ohlc.get('high',  current)), 2)
                low_p    = round(float(ohlc.get('low',   current)), 2)
                raw_close = ohlc.get('close')

                # prev_close: use OHLC close if it differs from LTP, else yfinance
                if raw_close is not None and abs(float(raw_close) - current) > 0.005:
                    prev_close = round(float(raw_close), 2)
                else:
                    try:
                        fi = yf.Ticker(to_yf_symbol(sym)).fast_info
                        yf_prev = getattr(fi, 'previous_close', None)
                        prev_close = round(float(yf_prev), 2) if yf_prev else current
                    except Exception:
                        prev_close = round(float(raw_close), 2) if raw_close else current

                change     = round(current - prev_close, 2)
                change_pct = round((change / prev_close) * 100, 2) if prev_close else 0
                quote = {
                    'current': current, 'high': high_p, 'low': low_p, 'open': open_p,
                    'prev_close': prev_close, 'change': change, 'change_pct': change_pct,
                    'volume': 0, 'source': 'upstox_realtime',
                }
                _price_cache_set(sym, quote)
                results[sym] = quote
            except Exception:
                continue

    except Exception as e:
        print(f'Upstox bulk quote error: {e}')

    return results


def get_upstox_quote(symbol, access_token=None):
    """Single-symbol wrapper around the bulk fetch. Checks 30s cache first."""
    symbol = symbol.upper().strip()
    cached = _price_cache_get(symbol)
    if cached:
        return cached
    res = get_upstox_quotes_bulk([symbol], access_token=access_token)
    return res.get(symbol)



def _yf_quote_from_ticker(yf_symbol):
    """Try to get quote from a yfinance ticker symbol. Returns dict or None."""
    try:
        ticker = yf.Ticker(yf_symbol)
        info = ticker.fast_info
        cur = round(float(info.last_price), 2)
        prev = round(float(info.previous_close), 2)
        if cur <= 0:
            return None
        try:
            vol = int(info.three_month_average_volume) if info.three_month_average_volume else 0
        except Exception:
            vol = 0
        try:
            hi = round(float(info.day_high), 2)
            lo = round(float(info.day_low), 2)
            op = round(float(info.open), 2)
        except Exception:
            hi = lo = op = cur
        return {
            'current': cur, 'high': hi, 'low': lo, 'open': op,
            'prev_close': prev, 'change': round(cur - prev, 2),
            'change_pct': round(((cur - prev) / prev) * 100, 2) if prev else 0,
            'volume': vol, 'source': 'yfinance',
        }
    except Exception:
        pass
    # Fallback: use history() if fast_info fails
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period='5d', auto_adjust=True)
        if df is not None and len(df) >= 1:
            last = df.iloc[-1]
            cur = round(float(last['Close']), 2)
            prev = round(float(df.iloc[-2]['Close']), 2) if len(df) >= 2 else cur
            return {
                'current': cur, 'high': round(float(last['High']), 2),
                'low': round(float(last['Low']), 2), 'open': round(float(last['Open']), 2),
                'prev_close': prev, 'change': round(cur - prev, 2),
                'change_pct': round(((cur - prev) / prev) * 100, 2) if prev else 0,
                'volume': int(last.get('Volume', 0)), 'source': 'yfinance_history',
            }
    except Exception:
        pass
    return None


def get_quote(symbol, trade_type='swing', upstox_token=None):
    symbol = _clean_symbol(symbol)
    cache_key = 'quote'
    # Don't use cache for intraday — needs live price
    if trade_type != 'intraday':
        cached = get_cached_data(symbol, cache_key)
        if cached:
            return cached

    # Intraday uses Upstox real-time
    if trade_type == 'intraday':
        result = get_upstox_quote(symbol, access_token=upstox_token)
        if result:
            set_cache(symbol, cache_key, result)
            return result

    # yfinance — try .NS first, then .BO fallback
    yf_sym = to_yf_symbol(symbol)
    result = _yf_quote_from_ticker(yf_sym)
    if not result and yf_sym.endswith('.NS'):
        result = _yf_quote_from_ticker(symbol + '.BO')
        if result:
            result['source'] = 'yfinance_bse'
    if result:
        set_cache(symbol, cache_key, result)
        return result

    # Finnhub fallback
    try:
        r = requests.get("https://finnhub.io/api/v1/quote",
            params={'symbol': symbol, 'token': settings.FINNHUB_API_KEY}, timeout=8)
        d = r.json()
        if d.get('c', 0) > 0:
            result = {
                'current': d['c'], 'high': d['h'], 'low': d['l'], 'open': d['o'],
                'prev_close': d['pc'], 'change': round(d['c']-d['pc'], 2),
                'change_pct': round(((d['c']-d['pc'])/d['pc'])*100, 2),
                'volume': 0, 'source': 'finnhub',
            }
            set_cache(symbol, cache_key, result)
            return result
    except Exception as e:
        print(f"Finnhub quote error: {e}")
    return None


TIMEFRAME_MAP = {
    '1':   ('1m',  '7d'),
    '5':   ('5m',  '60d'),
    '15':  ('15m', '60d'),
    '60':  ('60m', '730d'),   # max yfinance allows for 1h
    'D':   ('1d',  '5y'),     # 5 years daily
    '1wk': ('1wk', '10y'),    # 10 years weekly
}

def _yf_candle_history(yf_symbol, period, interval):
    """Try to fetch candle history from yfinance. Returns dict or None."""
    try:
        df = yf.Ticker(yf_symbol).history(period=period, interval=interval, auto_adjust=True)
        if df is not None and len(df) > 10:
            df = df.dropna()
            return {
                'timestamps': [int(t.timestamp()) for t in df.index],
                'open':   [round(float(x), 2) for x in df['Open']],
                'high':   [round(float(x), 2) for x in df['High']],
                'low':    [round(float(x), 2) for x in df['Low']],
                'close':  [round(float(x), 2) for x in df['Close']],
                'volume': [int(x) for x in df['Volume']],
                'source': 'yfinance',
            }
    except Exception:
        pass
    return None


def get_candle_data(symbol, timeframe='D'):
    symbol = _clean_symbol(symbol)
    cache_key = f'candle_{timeframe}'
    cached = get_cached_data(symbol, cache_key)
    if cached:
        return cached
    interval, period = TIMEFRAME_MAP.get(timeframe, ('1d', '2y'))

    # Try .NS first, then .BO fallback
    yf_sym = to_yf_symbol(symbol)
    result = _yf_candle_history(yf_sym, period, interval)
    if not result and yf_sym.endswith('.NS'):
        result = _yf_candle_history(symbol + '.BO', period, interval)
    if result:
        set_cache(symbol, cache_key, result)
        return result

    try:
        tf_map  = {'1':'1','5':'5','15':'15','60':'60','D':'D','1wk':'W'}
        days    = {'1':7,'5':60,'15':60,'60':180,'D':365,'1wk':1825}
        end     = int(datetime.now().timestamp())
        start   = int((datetime.now() - timedelta(days=days.get(timeframe,365))).timestamp())
        r = requests.get("https://finnhub.io/api/v1/stock/candle", timeout=10, params={
            'symbol': symbol, 'resolution': tf_map.get(timeframe,'D'),
            'from': start, 'to': end, 'token': settings.FINNHUB_API_KEY
        })
        d = r.json()
        if d.get('s') == 'ok':
            result = {
                'timestamps': d['t'], 'open': d['o'], 'high': d['h'],
                'low': d['l'], 'close': d['c'], 'volume': d['v'], 'source': 'finnhub',
            }
            set_cache(symbol, cache_key, result)
            return result
    except Exception as e:
        print(f"Finnhub candle error: {e}")
    return None


def calculate_indicators(candle_data):
    try:
        close  = pd.Series(candle_data['close'])
        high   = pd.Series(candle_data['high'])
        low    = pd.Series(candle_data['low'])
        volume = pd.Series(candle_data['volume'])
        if len(close) < 30:
            return None
        rsi_val  = round(RSIIndicator(close=close, window=14).rsi().iloc[-1], 2)
        m        = MACD(close=close)
        macd_val = round(m.macd().iloc[-1], 2)
        macd_sig = round(m.macd_signal().iloc[-1], 2)
        macd_diff= round(m.macd_diff().iloc[-1], 2)
        bb       = BollingerBands(close=close)
        bb_upper = round(bb.bollinger_hband().iloc[-1], 2)
        bb_lower = round(bb.bollinger_lband().iloc[-1], 2)
        bb_mid   = round(bb.bollinger_mavg().iloc[-1], 2)
        bb_width = round(((bb_upper-bb_lower)/bb_mid)*100, 2)
        ema9     = round(EMAIndicator(close=close, window=9).ema_indicator().iloc[-1], 2)
        ema21    = round(EMAIndicator(close=close, window=21).ema_indicator().iloc[-1], 2)
        ema50    = round(EMAIndicator(close=close, window=50).ema_indicator().iloc[-1], 2)
        sma50    = round(SMAIndicator(close=close, window=min(50,len(close))).sma_indicator().iloc[-1], 2)
        sma200   = round(SMAIndicator(close=close, window=min(200,len(close))).sma_indicator().iloc[-1], 2)
        atr_val  = round(AverageTrueRange(high=high,low=low,close=close).average_true_range().iloc[-1], 2)
        avg_vol  = round(volume.rolling(20).mean().iloc[-1], 0)
        cur_vol  = int(volume.iloc[-1])
        cur_price= round(float(close.iloc[-1]), 2)
        return {
            'rsi': rsi_val, 'macd': macd_val, 'macd_signal': macd_sig, 'macd_diff': macd_diff,
            'bb_upper': bb_upper, 'bb_lower': bb_lower, 'bb_mid': bb_mid, 'bb_width': bb_width,
            'ema9': ema9, 'ema21': ema21, 'ema50': ema50, 'sma50': sma50, 'sma200': sma200,
            'atr': atr_val, 'volume_spike': bool(cur_vol > avg_vol * 1.5),
            'avg_volume': int(avg_vol), 'current_volume': cur_vol, 'current_price': cur_price,
            'prev_close': round(float(close.iloc[-2]), 2) if len(close) > 2 else cur_price,
            'prev_open':  round(float(pd.Series(candle_data['open']).iloc[-2]), 2) if len(close) > 2 else cur_price,
        }
    except Exception as e:
        print(f"Indicator error: {e}")
        return None


def detect_candle_patterns(candle_data):
    """
    Scan recent candle data for candlestick patterns.
    Scans the last 30 positions, returns detected patterns with confidence
    scores and visual data so the frontend needs no hardcoded pattern info.
    """
    VISUALS = {
        'Doji':              [{'t':'neutral','uw':18,'bh':2,'lw':18}],
        'Hammer':            [{'t':'bull','uw':3,'bh':10,'lw':28}],
        'Inverted Hammer':   [{'t':'bull','uw':28,'bh':10,'lw':3}],
        'Shooting Star':     [{'t':'bear','uw':28,'bh':10,'lw':3}],
        'Hanging Man':       [{'t':'bear','uw':3,'bh':10,'lw':28}],
        'Bullish Engulfing': [{'t':'bear','uw':5,'bh':14,'lw':5},{'t':'bull','uw':3,'bh':24,'lw':3}],
        'Bearish Engulfing': [{'t':'bull','uw':5,'bh':14,'lw':5},{'t':'bear','uw':3,'bh':24,'lw':3}],
        'Morning Star':      [{'t':'bear','uw':4,'bh':20,'lw':4},{'t':'neutral','uw':6,'bh':4,'lw':6},{'t':'bull','uw':4,'bh':20,'lw':4}],
        'Evening Star':      [{'t':'bull','uw':4,'bh':20,'lw':4},{'t':'neutral','uw':6,'bh':4,'lw':6},{'t':'bear','uw':4,'bh':20,'lw':4}],
        'Bullish Marubozu':  [{'t':'bull','uw':0,'bh':34,'lw':0}],
        'Bearish Marubozu':  [{'t':'bear','uw':0,'bh':34,'lw':0}],
        'Three White Soldiers':[{'t':'bull','uw':2,'bh':12,'lw':2},{'t':'bull','uw':2,'bh':15,'lw':2},{'t':'bull','uw':2,'bh':18,'lw':2}],
        'Three Black Crows':  [{'t':'bear','uw':2,'bh':12,'lw':2},{'t':'bear','uw':2,'bh':15,'lw':2},{'t':'bear','uw':2,'bh':18,'lw':2}],
    }
    META = {
        'Doji':{'emoji':'➕','type':'neutral','meaning':'Market indecision — equal buying and selling pressure.','action':'WAIT — watch next candle for direction','base':55},
        'Hammer':{'emoji':'🔨','type':'bullish','meaning':'Sellers pushed price down but buyers recovered. Bottom reversal.','action':'BUY — bullish reversal signal','base':72},
        'Inverted Hammer':{'emoji':'🔨','type':'bullish','meaning':'Buying pressure emerging after downtrend. Needs confirmation.','action':'BUY on confirmation','base':60},
        'Shooting Star':{'emoji':'💫','type':'bearish','meaning':'Buyers pushed up but sellers brought price back down. Top reversal.','action':'SELL — bearish reversal signal','base':72},
        'Hanging Man':{'emoji':'⚠️','type':'bearish','meaning':'Long lower shadow at top of uptrend. Selling pressure emerging.','action':'SELL — bearish warning','base':65},
        'Bullish Engulfing':{'emoji':'🟢','type':'bullish','meaning':'Green candle completely covers previous red. Bulls dominate.','action':'STRONG BUY — bulls in control','base':82},
        'Bearish Engulfing':{'emoji':'🔴','type':'bearish','meaning':'Red candle completely covers previous green. Bears dominate.','action':'STRONG SELL — bears in control','base':82},
        'Morning Star':{'emoji':'⭐','type':'bullish','meaning':'Three-candle bullish reversal. Strong bottom signal.','action':'STRONG BUY — high reliability','base':88},
        'Evening Star':{'emoji':'🌙','type':'bearish','meaning':'Three-candle bearish reversal. Strong top signal.','action':'STRONG SELL — high reliability','base':88},
        'Bullish Marubozu':{'emoji':'💚','type':'bullish','meaning':'Pure buying — no wicks. Very strong bullish momentum.','action':'BUY — continuation','base':70},
        'Bearish Marubozu':{'emoji':'🔴','type':'bearish','meaning':'Pure selling — no wicks. Very strong bearish momentum.','action':'SELL — continuation','base':70},
        'Three White Soldiers':{'emoji':'🟩','type':'bullish','meaning':'Three consecutive strong green candles. Powerful uptrend.','action':'STRONG BUY — trend confirmation','base':85},
        'Three Black Crows':{'emoji':'⬛','type':'bearish','meaning':'Three consecutive strong red candles. Powerful downtrend.','action':'STRONG SELL — trend confirmation','base':85},
    }

    def _add(found, name, idx, bars_ago, conf):
        m = META[name]
        if name not in found or found[name]['confidence'] < conf:
            found[name] = {
                'name': name, 'emoji': m['emoji'], 'type': m['type'],
                'meaning': m['meaning'], 'action': m['action'],
                'reliability': m['base'], 'confidence': conf,
                'bars_ago': bars_ago,
                'detected_at': 'Latest candle' if bars_ago == 0 else f'{bars_ago} candle{"s" if bars_ago > 1 else ""} ago',
                'candle_visual': VISUALS.get(name, []),
            }

    try:
        o, h, l, c = candle_data['open'], candle_data['high'], candle_data['low'], candle_data['close']
        n = len(c)
        if n < 3:
            return []

        scan = min(30, n - 2)
        found = {}

        for idx in range(n - 1, n - 1 - scan, -1):
            if idx < 2:
                break
            oi, hi, li, ci = o[idx], h[idx], l[idx], c[idx]
            bi = abs(ci - oi)
            ri = hi - li
            if ri == 0:
                continue

            op, hp, lp, cp = o[idx-1], h[idx-1], l[idx-1], c[idx-1]
            bp = abs(cp - op)
            o2, h2, l2, c2 = o[idx-2], h[idx-2], l[idx-2], c[idx-2]
            b2 = abs(c2 - o2)

            bars_ago = n - 1 - idx
            rec = max(0.55, 1.0 - bars_ago * 0.015)

            # ── Single-candle patterns ──

            # Doji
            if bi / ri < 0.1:
                _add(found, 'Doji', idx, bars_ago, round(min(95, 55 + (1 - bi/ri) * 35) * rec))

            lower_w = min(oi, ci) - li
            upper_w = hi - max(oi, ci)

            # Hammer  (long lower wick, tiny upper, bullish body or small body)
            if bi > 0 and lower_w > bi * 2 and upper_w < bi * 0.5 and ci >= oi:
                _add(found, 'Hammer', idx, bars_ago, round(min(95, 72 + min(lower_w/bi, 5)*3) * rec))

            # Hanging Man (same shape as hammer but bearish close)
            if bi > 0 and lower_w > bi * 2 and upper_w < bi * 0.5 and ci < oi:
                _add(found, 'Hanging Man', idx, bars_ago, round(min(95, 65 + min(lower_w/bi, 5)*3) * rec))

            # Inverted Hammer (long upper wick, bullish)
            if bi > 0 and upper_w > bi * 2 and lower_w < bi * 0.5 and ci > oi:
                _add(found, 'Inverted Hammer', idx, bars_ago, round(min(95, 60 + min(upper_w/bi, 5)*3) * rec))

            # Shooting Star (long upper wick, bearish)
            if bi > 0 and upper_w > bi * 2 and lower_w < bi * 0.5 and ci < oi:
                _add(found, 'Shooting Star', idx, bars_ago, round(min(95, 72 + min(upper_w/bi, 5)*3) * rec))

            # Marubozu (no wicks)
            if bi / ri > 0.9:
                if ci > oi:
                    _add(found, 'Bullish Marubozu', idx, bars_ago, round(min(95, 70 + (bi/ri - 0.9)*200) * rec))
                else:
                    _add(found, 'Bearish Marubozu', idx, bars_ago, round(min(95, 70 + (bi/ri - 0.9)*200) * rec))

            # ── Two-candle patterns ──

            # Bullish Engulfing
            if op > cp and ci > oi and ci > op and oi < cp:
                _add(found, 'Bullish Engulfing', idx, bars_ago, round(min(95, 82 + min(bi/bp, 3)*3) * rec))

            # Bearish Engulfing
            if cp > op and oi > ci and oi > cp and ci < op:
                _add(found, 'Bearish Engulfing', idx, bars_ago, round(min(95, 82 + min(bi/bp, 3)*3) * rec))

            # ── Three-candle patterns ──

            # Morning Star
            if o2 > c2 and bp < bi * 0.3 and ci > oi and ci > (o2 + c2) / 2:
                _add(found, 'Morning Star', idx, bars_ago, round(88 * rec))

            # Evening Star
            if c2 > o2 and bp < bi * 0.3 and oi > ci and ci < (o2 + c2) / 2:
                _add(found, 'Evening Star', idx, bars_ago, round(88 * rec))

            # Three White Soldiers
            if idx >= 2:
                if (c2 > o2 and cp > op and ci > oi and
                    cp > c2 and ci > cp and
                    abs(c2-o2) > 0 and abs(cp-op) > 0 and abs(ci-oi) > 0):
                    _add(found, 'Three White Soldiers', idx, bars_ago, round(85 * rec))

            # Three Black Crows
            if idx >= 2:
                if (c2 < o2 and cp < op and ci < oi and
                    cp < c2 and ci < cp and
                    abs(c2-o2) > 0 and abs(cp-op) > 0 and abs(ci-oi) > 0):
                    _add(found, 'Three Black Crows', idx, bars_ago, round(85 * rec))

        result = sorted(found.values(), key=lambda x: (-x['confidence'], x['bars_ago']))
        return result[:5]

    except Exception as e:
        print(f"Pattern detection error: {e}")
        return []


def get_support_resistance(candle_data, n=5):
    try:
        highs  = pd.Series(candle_data['high'])
        lows   = pd.Series(candle_data['low'])
        price  = float(pd.Series(candle_data['close']).iloc[-1])
        res = [round(float(highs.iloc[i]),2) for i in range(n,len(highs)-n) if highs.iloc[i]==highs.iloc[i-n:i+n+1].max()]
        sup = [round(float(lows.iloc[i]),2)  for i in range(n,len(lows)-n)  if lows.iloc[i]==lows.iloc[i-n:i+n+1].min()]
        res = sorted([r for r in res if r > price])[:3]
        sup = sorted([s for s in sup if s < price], reverse=True)[:3]
        def dedup(lv):
            r=[]
            for x in lv:
                if not r or abs(x-r[-1])/r[-1]>0.005: r.append(x)
            return r
        return {'resistance': dedup(res), 'support': dedup(sup)}
    except Exception as e:
        print(f"S/R error: {e}")
        return {'resistance': [], 'support': []}


def score_trade_types(indicators, candle_data):
    try:
        rsi,md,e9,e21 = indicators['rsi'],indicators['macd_diff'],indicators['ema9'],indicators['ema21']
        s50,s200,price,bw = indicators['sma50'],indicators['sma200'],indicators['current_price'],indicators['bb_width']
        vs = indicators['volume_spike']

        i = 50
        if vs:              i += 20
        if 40<rsi<65:       i += 15
        if abs(md)>0:       i += 10
        if e9!=e21:         i += 5
        if bw>3:            i -= 10
        if rsi>75 or rsi<25:i -= 20
        i = max(0,min(100,i))

        s = 50
        if md>0:            s += 15
        if e9>e21:          s += 15
        if price>s50:       s += 10
        if 30<rsi<70:       s += 10
        if rsi<30:          s += 5
        if rsi>70:          s -= 10
        s = max(0,min(100,s))

        lt = 50
        if price>s200:      lt += 20
        if price>s50:       lt += 10
        if e9>e21:          lt += 10
        if md>0:            lt += 10
        if rsi<60:          lt += 10
        if rsi>70:          lt -= 10
        lt = max(0,min(100,lt))

        scores = {'intraday':i,'swing':s,'longterm':lt}
        best = max(scores, key=scores.get)
        return {**scores,'best':best,'best_label':{'intraday':'Intraday','swing':'Swing Trade','longterm':'Long Term'}[best],'best_score':scores[best]}
    except Exception as e:
        print(f"Score error: {e}")
        return {'intraday':50,'swing':50,'longterm':50,'best':'swing','best_label':'Swing Trade','best_score':50}


def generate_signal(indicators, trade_type='swing'):
    try:
        price,rsi,md = indicators['current_price'],indicators['rsi'],indicators['macd_diff']
        e9,e21,s50,s200 = indicators['ema9'],indicators['ema21'],indicators['sma50'],indicators['sma200']
        atr,bbu,bbl = indicators['atr'],indicators['bb_upper'],indicators['bb_lower']
        bull = bear = 0
        if rsi<30:    bull+=3
        elif rsi<45:  bull+=1
        elif rsi>70:  bear+=3
        elif rsi>55:  bear+=1
        if md>0:      bull+=2
        else:         bear+=2
        if e9>e21:    bull+=2
        else:         bear+=2
        if price>s50: bull+=1
        else:         bear+=1
        if price>s200:bull+=1
        else:         bear+=1
        if price<=bbl:bull+=2
        elif price>=bbu:bear+=2
        if indicators['volume_spike']:
            if bull>bear: bull+=1
            else: bear+=1
        total = bull+bear
        bull_pct = round((bull/total)*100) if total>0 else 50
        if bull>=8:   sig,col='STRONG BUY','success'
        elif bull>=5: sig,col='BUY','success'
        elif bear>=8: sig,col='STRONG SELL','danger'
        elif bear>=5: sig,col='SELL','danger'
        else:         sig,col='WAIT','warning'
        ap = atr/price*100
        if ap>3:     risk,rc='HIGH','danger'
        elif ap>1.5: risk,rc='MEDIUM','warning'
        else:        risk,rc='LOW','success'
        if trade_type=='intraday':
            sl=round(price-atr*0.5,2); t1=round(price+atr*0.75,2); t2=round(price+atr*1.25,2)
        elif trade_type=='swing':
            sl=round(price-atr*1.5,2); t1=round(price+atr*2,2); t2=round(price+atr*3.5,2)
        else:
            sl=round(price-atr*3,2); t1=round(price+atr*5,2); t2=round(price+atr*10,2)
        rr = round((t1-price)/(price-sl),2) if price!=sl else 0
        trend = 'STRONG' if bull_pct>=75 else 'MODERATE' if bull_pct>=55 else 'WEAK'
        return {
            'signal':sig,'signal_color':col,'bull_points':bull,'bear_points':bear,
            'bull_pct':bull_pct,'risk':risk,'risk_color':rc,'entry_price':price,
            'stop_loss':sl,'target1':t1,'target2':t2,'risk_reward':rr,
            'trend_strength':trend,'short_sell':sig in ['SELL','STRONG SELL'],
        }
    except Exception as e:
        print(f"Signal error: {e}")
        return None


def get_market_overview():
    result = {}
    for key,sym in {'nifty':'^NSEI','sensex':'^BSESN','vix':'^INDIAVIX'}.items():
        try:
            info = yf.Ticker(sym).fast_info
            cur  = round(float(info.last_price),2)
            prev = round(float(info.previous_close),2)
            chg  = round(cur-prev,2)
            pct  = round((chg/prev)*100,2) if prev else 0
            result[key] = {'price':cur,'change':chg,'change_pct':pct}
        except Exception:
            result[key] = {'price':0,'change':0,'change_pct':0}
    return result