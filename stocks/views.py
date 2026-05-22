from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings
from django.db.models import Count, Q
from .models import Watchlist, UserProfile, ActivityLog, SignalHistory, StockCache , PaperTrade
from .utils import (
    get_quote, get_candle_data, calculate_indicators, generate_signal,
    detect_candle_patterns, get_support_resistance, score_trade_types,
    get_upstox_quote, get_upstox_quotes_bulk, get_live_price_for_trade, get_market_overview,
)
import json


# ─── Helpers ──────────────────────────────────────────────

def get_client_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0]
    return request.META.get('REMOTE_ADDR')


def log_activity(user, action, detail='', request=None):
    ip = get_client_ip(request) if request else None
    ActivityLog.objects.create(user=user, action=action, detail=detail, ip_address=ip)


def is_admin_user(user):
    return user.is_authenticated and (
        user.is_superuser or
        (hasattr(user, 'profile') and user.profile.is_admin)
    )


# ─── Auth Views ───────────────────────────────────────────

def home_view(request):
    if request.user.is_authenticated:
        if is_admin_user(request.user):
            return redirect('admin_dashboard')
        return redirect('dashboard')
    return render(request, 'stocks/home.html')


def login_view(request):
    if request.user.is_authenticated:
        if is_admin_user(request.user):
            return redirect('admin_dashboard')
        return redirect('dashboard')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if not user.is_active:
                error = 'Your account is pending admin approval. Please wait.'
            else:
                login(request, user)
                if hasattr(user, 'profile'):
                    user.profile.last_seen = timezone.now()
                    user.profile.save()
                log_activity(user, 'login', request=request)
                if is_admin_user(user):
                    return redirect('admin_dashboard')
                return redirect('dashboard')
        else:
            error = 'Invalid username or password'

    return render(request, 'stocks/login.html', {'error': error})


def logout_view(request):
    if request.user.is_authenticated:
        log_activity(request.user, 'logout', request=request)
    logout(request)
    return redirect('home')



def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        experience = request.POST.get('trading_experience', 'beginner')
        trade_type = request.POST.get('preferred_trade_type', 'swing')
        capital = request.POST.get('capital', 0) or 0
        password = request.POST.get('password', '')
        confirm = request.POST.get('confirm_password', '')

        if password != confirm:
            error = 'Passwords do not match'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters'
        elif User.objects.filter(username=username).exists():
            error = 'Username already taken'
        elif User.objects.filter(email=email).exists():
            error = 'Email already registered'
        else:
            user = User.objects.create_user(
                username=username, email=email, password=password,
                first_name=first_name, last_name=last_name,
                is_active=True,
            )
            UserProfile.objects.create(
                user=user, phone=phone,
                trading_experience=experience,
                preferred_trade_type=trade_type,
                capital=capital,
            )
            log_activity(user, 'register', detail=f'New registration from {email}', request=request)
            return redirect('login')

    return render(request, 'stocks/register.html', {'error': error})


# ─── Main Dashboard ────────────────────────────────────────

@login_required(login_url='login')
def dashboard(request):
    if is_admin_user(request.user):
        return redirect('admin_dashboard')

    watchlist = Watchlist.objects.filter(user=request.user)
    recent_signals = SignalHistory.objects.filter(user=request.user)[:10]
    profile = getattr(request.user, 'profile', None)

    context = {
        'watchlist': watchlist,
        'recent_signals': recent_signals,
        'profile': profile,
    }
    return render(request, 'stocks/dashboard.html', context)


# ─── Stock API Endpoints ───────────────────────────────────

@login_required(login_url='login')
def analyze(request):
    symbol = request.GET.get('symbol', '').upper().strip()
    trade_type = request.GET.get('trade_type', 'swing')

    if not symbol:
        return JsonResponse({'error': 'No symbol provided'}, status=400)

    if trade_type == 'intraday':
        timeframe = '15'
    elif trade_type == 'swing':
        timeframe = '60'
    else:
        timeframe = 'D'

    # Use user's personal Upstox token if available, else fall back to .env token
    profile = getattr(request.user, 'profile', None)
    user_token = getattr(profile, 'upstox_token', '').strip() or None
    quote = get_quote(symbol, trade_type, upstox_token=user_token)
    if not quote:
        return JsonResponse({'error': f'Could not fetch data for {symbol}. Check symbol name.'}, status=404)

    candle_data = get_candle_data(symbol, timeframe)
    if not candle_data:
        return JsonResponse({'error': f'No candle data for {symbol}.'}, status=404)

    indicators = calculate_indicators(candle_data)
    if not indicators:
        return JsonResponse({'error': 'Could not calculate indicators.'}, status=500)

    patterns = detect_candle_patterns(candle_data)
    signal = generate_signal(indicators, trade_type, patterns=patterns)
    if not signal:
        return JsonResponse({'error': 'Could not generate signal.'}, status=500)

    # ── Smart Suggestion Generator ────────────────────────────────────────
    sr = get_support_resistance(candle_data)

    if patterns and signal:
        bearish = [p for p in patterns if p['type'] == 'bearish' and p.get('confidence', 0) >= 75]
        bullish = [p for p in patterns if p['type'] == 'bullish' and p.get('confidence', 0) >= 75]
        neutral = [p for p in patterns if p['type'] == 'neutral'  and p.get('confidence', 0) >= 75]

        sig_str   = signal['signal']
        price     = indicators['current_price']
        sl        = signal['stop_loss']
        t1        = signal['target1']
        t2        = signal['target2']
        res_list  = sr.get('resistance', [])
        sup_list  = sr.get('support', [])
        nearest_res = (res_list[0]['price'] if isinstance(res_list[0], dict) else res_list[0]) if res_list else None
        nearest_sup = (sup_list[0]['price'] if isinstance(sup_list[0], dict) else sup_list[0]) if sup_list else None

        def fmt_p(v): return f'₹{v:,.2f}' if v else '—'

        suggestion_short = []
        suggestion_full  = {}

        # ── INTRADAY ──────────────────────────────────────────────────────
        if trade_type == 'intraday':
            if sig_str in ['STRONG BUY', 'BUY'] and bearish:
                res_txt = fmt_p(nearest_res) if nearest_res else 'resistance'
                sup_txt = fmt_p(nearest_sup) if nearest_sup else 'support'
                suggestion_short = [
                    f'⏳ Wait for {res_txt} breakout before buying',
                    f'⚠️ Mixed candles — confirm with volume spike',
                ]
                suggestion_full = {
                    'title':    f'{sig_str} signal but mixed candle patterns detected',
                    'subtitle': 'Do not enter immediately — wait for confirmation',
                    'situation': [
                        f'Price {fmt_p(price)} is near resistance {res_txt}',
                        f'{bearish[0]["name"]} detected — bears are active short term',
                        f'{"+ " + bullish[0]["name"] + " also detected — bulls trying to recover" if bullish else "No bullish patterns to confirm entry"}',
                        'Mixed signals — blind entry is risky right now',
                    ],
                    'action': [
                        'DO NOT buy immediately',
                        f'Wait for price to break ABOVE {res_txt} with volume',
                        f'If volume spikes when crossing {res_txt} → BUY',
                        f'If price falls below {sup_txt} → AVOID today',
                    ],
                    'plan': {'entry': f'{res_txt}+' if nearest_res else fmt_p(price), 'sl': fmt_p(sl), 'target1': fmt_p(t1), 'target2': fmt_p(t2)},
                    'color': 'yellow',
                }
                signal['pattern_warning'] = f'⚠️ {bearish[0]["name"]} detected — bearish candles weaken this {sig_str} signal'

            elif sig_str in ['STRONG BUY', 'BUY'] and not bearish:
                suggestion_short = [
                    f'✅ Clean BUY signal — enter near {fmt_p(price)}',
                    f'🎯 Stop Loss: {fmt_p(sl)} | Target: {fmt_p(t1)}',
                ]
                suggestion_full = {
                    'title':    'Clean BUY signal — good intraday entry',
                    'subtitle': 'Indicators and candles are aligned bullish',
                    'situation': [
                        f'Price {fmt_p(price)} showing bullish momentum',
                        f'{"Bullish pattern: " + bullish[0]["name"] + " confirms entry" if bullish else "All indicators bullish — no conflicting candles"}',
                        'EMA Golden Cross active — short term uptrend',
                        'No bearish candle patterns to worry about',
                    ],
                    'action': [
                        f'BUY near current price {fmt_p(price)}',
                        f'Set stop loss at {fmt_p(sl)} immediately after buying',
                        f'Book 50% profit at Target 1 {fmt_p(t1)}',
                        f'Let rest run to Target 2 {fmt_p(t2)}',
                        'Exit ALL positions before 3:15 PM IST',
                    ],
                    'plan': {'entry': fmt_p(price), 'sl': fmt_p(sl), 'target1': fmt_p(t1), 'target2': fmt_p(t2)},
                    'color': 'green',
                }

            elif sig_str == 'WAIT':
                res_txt = fmt_p(nearest_res) if nearest_res else 'resistance'
                sup_txt = fmt_p(nearest_sup) if nearest_sup else 'support'
                suggestion_short = [
                    '⏳ No clear direction — stay out today',
                    f'📍 Watch {res_txt} for breakout signal',
                ]
                suggestion_full = {
                    'title':    'Mixed signals — no clear intraday trade today',
                    'subtitle': 'Patience is the best strategy right now',
                    'situation': [
                        'Indicators are neutral — bulls and bears balanced',
                        f'{"Neutral pattern " + neutral[0]["name"] + " detected — market undecided" if neutral else "No strong pattern detected"}',
                        f'Price between support {sup_txt} and resistance {res_txt}',
                    ],
                    'action': [
                        'DO NOT trade right now',
                        f'Set alert at {res_txt} — if price breaks above with volume → BUY',
                        f'Set alert at {sup_txt} — if price breaks below → SHORT',
                        'Wait for a clear candle pattern to form',
                        'Best time to re-check: after 1:00 PM IST',
                    ],
                    'plan': None,
                    'color': 'yellow',
                }

            elif sig_str in ['SELL', 'STRONG SELL']:
                suggestion_short = [
                    '🔴 Bearish — avoid buying today',
                    f'📉 Price may fall to {fmt_p(nearest_sup)}',
                ]
                suggestion_full = {
                    'title':    'Bearish signal — do not buy today',
                    'subtitle': 'Sellers are in control of this stock right now',
                    'situation': [
                        f'Price {fmt_p(price)} showing bearish momentum',
                        f'{"Bearish pattern: " + bearish[0]["name"] if bearish else "Multiple bearish indicators active"}',
                        'EMA Death Cross or price below key moving averages',
                    ],
                    'action': [
                        'DO NOT buy this stock today',
                        f'If you already hold — consider exiting at {fmt_p(price)}',
                        'Wait for signal to turn BUY before entering again',
                        f'Watch support at {fmt_p(nearest_sup)} for possible bounce',
                    ],
                    'plan': None,
                    'color': 'red',
                }

        # ── SWING ─────────────────────────────────────────────────────────
        elif trade_type == 'swing':
            if sig_str in ['STRONG BUY', 'BUY'] and not bearish:
                suggestion_short = [
                    f'✅ Good swing entry — buy on dip to {fmt_p(nearest_sup)}',
                    f'🎯 Target {fmt_p(t2)} in 5-10 days',
                ]
                suggestion_full = {
                    'title':    'Good swing trade setup',
                    'subtitle': 'Buy on the next dip for a better entry price',
                    'situation': [
                        'Strong bullish trend with clean indicators',
                        f'{"Pattern: " + bullish[0]["name"] + " confirms bullish reversal" if bullish else "All indicators aligned bullish"}',
                        f'Price {fmt_p(price)} above key moving averages',
                    ],
                    'action': [
                        f'Option 1: BUY NOW at {fmt_p(price)} — momentum entry',
                        f'Option 2: Wait for dip to {fmt_p(nearest_sup)} — safer entry',
                        f'Set stop loss at {fmt_p(sl)} — do not move it lower',
                        f'Hold for 5-10 days targeting {fmt_p(t1)} then {fmt_p(t2)}',
                        'Book 50% profit at Target 1, let rest run',
                    ],
                    'plan': {'entry': fmt_p(price), 'sl': fmt_p(sl), 'target1': fmt_p(t1), 'target2': fmt_p(t2)},
                    'color': 'green',
                }

            elif sig_str in ['STRONG BUY', 'BUY'] and bearish:
                suggestion_short = [
                    '⏳ Wait for bearish candles to resolve',
                    f'📍 Buy only if price holds above {fmt_p(nearest_sup)}',
                ]
                suggestion_full = {
                    'title':    'BUY signal but short-term bearish candles present',
                    'subtitle': 'Wait for candle confirmation before entering swing trade',
                    'situation': [
                        'Long term trend is bullish — indicators agree',
                        f'But {bearish[0]["name"]} detected — short term selling pressure',
                        'This is likely a pullback within an uptrend',
                    ],
                    'action': [
                        'DO NOT enter today — wait for pullback to finish',
                        f'Watch support at {fmt_p(nearest_sup)} — if price holds here → BUY',
                        'Wait for a bullish candle (green close) to confirm reversal',
                        'Enter tomorrow or day after if candles turn bullish',
                        f'Stop Loss: {fmt_p(sl)} | Target: {fmt_p(t1)} → {fmt_p(t2)}',
                    ],
                    'plan': {'entry': f'{fmt_p(nearest_sup)} area', 'sl': fmt_p(sl), 'target1': fmt_p(t1), 'target2': fmt_p(t2)},
                    'color': 'yellow',
                }
                signal['pattern_warning'] = f'⚠️ {bearish[0]["name"]} detected — bearish candles weaken this {sig_str} signal'

            elif sig_str == 'WAIT':
                suggestion_short = [
                    '⏳ No swing setup yet — monitor daily',
                    f'📍 Alert at {fmt_p(nearest_res)} for breakout',
                ]
                suggestion_full = {
                    'title':    'No clear swing trade setup currently',
                    'subtitle': 'Monitor and wait for a clear signal',
                    'situation': [
                        f'Stock is consolidating between {fmt_p(nearest_sup)} and {fmt_p(nearest_res)}',
                        'No strong pattern or indicator alignment',
                        'Market is undecided on this stock',
                    ],
                    'action': [
                        f'Set price alert at {fmt_p(nearest_res)} — breakout above = BUY',
                        f'Set price alert at {fmt_p(nearest_sup)} — breakdown below = SELL',
                        'Check again tomorrow — swing setups develop over days',
                        'Look at screener for better setups elsewhere',
                    ],
                    'plan': None,
                    'color': 'yellow',
                }

            elif sig_str in ['SELL', 'STRONG SELL']:
                suggestion_short = [
                    '🔴 Avoid swing trade — bearish trend',
                    '📉 Wait for reversal signal before entering',
                ]
                suggestion_full = {
                    'title':    'Bearish swing — avoid buying',
                    'subtitle': 'Wait for trend reversal before entering',
                    'situation': [
                        'Stock is in a downtrend — lower highs and lower lows',
                        f'{"Pattern: " + bearish[0]["name"] + " confirms selling pressure" if bearish else "Multiple indicators bearish"}',
                        f'Risk of further decline to {fmt_p(nearest_sup)}',
                    ],
                    'action': [
                        'DO NOT buy this stock for swing trade',
                        f'If holding — consider cutting losses at {fmt_p(price)}',
                        'Wait for MACD to cross above zero AND price above EMA21',
                        'Re-check after 1 week for reversal setup',
                    ],
                    'plan': None,
                    'color': 'red',
                }

        # ── LONG TERM ─────────────────────────────────────────────────────
        elif trade_type == 'longterm':
            if sig_str in ['STRONG BUY', 'BUY']:
                suggestion_short = [
                    '✅ Good long term entry — accumulate gradually',
                    f'🎯 Target {fmt_p(t2)}+ over months',
                ]
                suggestion_full = {
                    'title':    'Good long term investment opportunity',
                    'subtitle': 'Buy in multiple parts — do not invest all at once',
                    'situation': [
                        'Long term trend is BULLISH — price above SMA200',
                        'Strong setup based on technical indicators',
                        f'{"Short term volatility from " + bearish[0]["name"] + " is normal in long term" if bearish else "Clean bullish setup across all timeframes"}',
                    ],
                    'action': [
                        f'Part 1: Buy 30% of planned quantity NOW at {fmt_p(price)}',
                        f'Part 2: Buy 40% on next dip to {fmt_p(nearest_sup)}',
                        f'Part 3: Buy remaining 30% if price breaks {fmt_p(nearest_res)}',
                        'Hold for 3-12 months',
                        f'Stop Loss: {fmt_p(sl)} — exit only if this breaks on weekly chart',
                    ],
                    'plan': {'entry': f'{fmt_p(price)} in 3 parts', 'sl': fmt_p(sl), 'target1': fmt_p(t1), 'target2': fmt_p(t2)},
                    'color': 'green',
                }

            elif sig_str == 'WAIT':
                suggestion_short = [
                    '⏳ Not the best entry point — wait for dip',
                    f'📍 Better entry near {fmt_p(nearest_sup)}',
                ]
                suggestion_full = {
                    'title':    'Wait for a better long term entry point',
                    'subtitle': 'Good stock but not the right price to enter now',
                    'situation': [
                        'Stock may be in a consolidation or minor pullback',
                        'Long term outlook may still be positive',
                        f'Current price {fmt_p(price)} is not at a strong support level',
                    ],
                    'action': [
                        f'Wait for price to come to support {fmt_p(nearest_sup)}',
                        f'Set a price alert at {fmt_p(nearest_sup)}',
                        'Study the company fundamentals while waiting',
                        f'Breakout above {fmt_p(nearest_res)} with volume is also a valid entry',
                    ],
                    'plan': None,
                    'color': 'yellow',
                }

            elif sig_str in ['SELL', 'STRONG SELL']:
                suggestion_short = [
                    '🔴 Poor long term entry — stock in downtrend',
                    '⏳ Wait for monthly chart to turn bullish',
                ]
                suggestion_full = {
                    'title':    'Avoid long term investment right now',
                    'subtitle': 'Technical indicators suggest ongoing weakness',
                    'situation': [
                        'Stock below key long term moving averages',
                        f'{"Bearish pattern " + bearish[0]["name"] + " on recent candles" if bearish else "Bearish indicators across multiple timeframes"}',
                        'Risk of further downside before recovery',
                    ],
                    'action': [
                        'DO NOT invest for long term right now',
                        'Wait for monthly MACD to turn positive',
                        'Wait for price to reclaim SMA200',
                        'Re-evaluate after 1-3 months',
                        'Look for other stocks with better setups in the screener',
                    ],
                    'plan': None,
                    'color': 'red',
                }

        signal['suggestion_short'] = suggestion_short
        signal['suggestion_full']  = suggestion_full
    # ─────────────────────────────────────────────────────────────────────

    scores = score_trade_types(indicators, candle_data)

    try:
        SignalHistory.objects.create(
            user=request.user,
            symbol=symbol,
            trade_type=trade_type,
            signal=signal['signal'],
            entry_price=signal['entry_price'],
            stop_loss=signal['stop_loss'],
            target1=signal['target1'],
            target2=signal['target2'],
            risk_level=signal['risk'],
        )
    except Exception:
        pass

    log_activity(request.user, 'analyze', detail=f'{symbol} ({trade_type})', request=request)
    in_watchlist = Watchlist.objects.filter(user=request.user, symbol=symbol).exists()

    return JsonResponse({
        'symbol': symbol,
        'trade_type': trade_type,
        'quote': quote,
        'indicators': indicators,
        'signal': signal,
        'candle_data': candle_data,
        'in_watchlist': in_watchlist,
        'patterns': patterns,
        'support_resistance': sr,
        'trade_scores': scores,
    })


@login_required(login_url='login')
def get_candles(request):
    symbol = request.GET.get('symbol', '').upper().strip()
    timeframe = request.GET.get('timeframe', 'D')

    if not symbol:
        return JsonResponse({'error': 'No symbol provided'}, status=400)

    candle_data = get_candle_data(symbol, timeframe)
    if not candle_data:
        return JsonResponse({'error': 'Could not fetch candle data'}, status=404)

    return JsonResponse(candle_data)


@csrf_exempt
@login_required(login_url='login')
def toggle_watchlist(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    symbol = data.get('symbol', '').upper().strip()
    name = data.get('name', '')

    if not symbol:
        return JsonResponse({'error': 'No symbol provided'}, status=400)

    existing = Watchlist.objects.filter(user=request.user, symbol=symbol)
    if existing.exists():
        existing.delete()
        log_activity(request.user, 'watchlist_remove', detail=symbol, request=request)
        return JsonResponse({'status': 'removed', 'symbol': symbol})
    else:
        Watchlist.objects.create(user=request.user, symbol=symbol, name=name)
        log_activity(request.user, 'watchlist_add', detail=symbol, request=request)
        return JsonResponse({'status': 'added', 'symbol': symbol})


@login_required(login_url='login')
def watchlist_signals(request):
    watchlist = Watchlist.objects.filter(user=request.user)
    results = []

    for item in watchlist:
        quote = get_quote(item.symbol)
        if quote:
            candle_data = get_candle_data(item.symbol, 'D')
            if candle_data:
                indicators = calculate_indicators(candle_data)
                if indicators:
                    signal = generate_signal(indicators, 'swing')
                    if signal:
                        results.append({
                            'symbol': item.symbol,
                            'name': item.name,
                            'quote': quote,
                            'signal': signal['signal'],
                            'signal_color': signal['signal_color'],
                            'risk': signal['risk'],
                        })

    return JsonResponse({'watchlist': results})


@csrf_exempt
@login_required(login_url='login')
def save_upstox_token(request):
    """Save user's personal Upstox token to their profile"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)
    try:
        data = json.loads(request.body)
        token = data.get('token', '').strip()
        if not token:
            return JsonResponse({'error': 'No token provided'}, status=400)
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return JsonResponse({'error': 'No profile found'}, status=404)
        profile.upstox_token = token
        profile.upstox_token_updated = timezone.now()
        profile.save()
        log_activity(request.user, 'analyze', detail='Updated Upstox token', request=request)
        return JsonResponse({'status': 'saved', 'message': 'Upstox token saved successfully'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required(login_url='login')
def stock_news(request):
    symbol = request.GET.get('symbol', '').upper().strip()
    import requests as req
    import xml.etree.ElementTree as ET
    import re

    feeds = [
        ('Economic Times', 'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms'),
        ('ET Markets', 'https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms'),
        ('Mint Markets', 'https://www.livemint.com/rss/markets'),
        ('Business Std', 'https://www.business-standard.com/rss/markets-106.rss'),
    ]

    news_items = []
    symbol_clean = symbol.replace('.NS', '').replace('.BO', '').lower()

    company_names = {
        'RELIANCE': ['reliance', 'ril', 'jio'],
        'TCS': ['tcs', 'tata consultancy'],
        'INFY': ['infosys', 'infy'],
        'HDFCBANK': ['hdfc bank', 'hdfcbank'],
        'ICICIBANK': ['icici bank', 'icicibank'],
        'SBIN': ['sbi', 'state bank'],
        'WIPRO': ['wipro'],
        'BAJFINANCE': ['bajaj finance'],
        'TATAMOTORS': ['tata motors'],
        'MARUTI': ['maruti', 'suzuki'],
        'AXISBANK': ['axis bank'],
        'KOTAKBANK': ['kotak bank', 'kotak mahindra'],
        'HINDUNILVR': ['hindustan unilever', 'hul'],
        'BHARTIARTL': ['airtel', 'bharti'],
        'ITC': ['itc'],
        'SUNPHARMA': ['sun pharma', 'sun pharmaceutical'],
        'ADANIENT': ['adani enterprises'],
        'ADANIPORTS': ['adani ports'],
        'HCLTECH': ['hcl tech', 'hcltech'],
        'LT': ['larsen', 'l&t'],
        'TATASTEEL': ['tata steel'],
        'TITAN': ['titan'],
        'DRREDDY': ["dr reddy", "dr. reddy"],
        'CIPLA': ['cipla'],
        'NTPC': ['ntpc'],
        'ONGC': ['ongc'],
    }

    search_terms = [symbol_clean] + company_names.get(symbol, [])

    for source_name, feed_url in feeds:
        try:
            r = req.get(feed_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=7)
            root = ET.fromstring(r.content)
            channel = root.find('channel')
            items = channel.findall('item') if channel else []
            for item in items[:20]:
                title = item.findtext('title', '').strip()
                link = item.findtext('link', '').strip()
                if not title or not link:
                    continue
                title_clean = re.sub(r'&#\d+;|&amp;|&lt;|&gt;', '', title).strip()
                title_lower = title_clean.lower()
                if any(term in title_lower for term in search_terms):
                    news_items.append({
                        'title': title_clean,
                        'link': link,
                        'source': source_name,
                        'time': 'Recent',
                    })
        except Exception:
            continue

    return JsonResponse({'news': news_items[:8], 'symbol': symbol})


# ─── ADMIN PANEL VIEWS ─────────────────────────────────────

def admin_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not is_admin_user(request.user):
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


@admin_required
def admin_dashboard(request):
    total_users = User.objects.filter(is_superuser=False).count()
    pending_users = User.objects.filter(is_active=False, is_superuser=False).count()
    active_users = User.objects.filter(is_active=True, is_superuser=False).count()
    total_signals = SignalHistory.objects.count()
    total_analyses = ActivityLog.objects.filter(action='analyze').count()
    recent_activities = ActivityLog.objects.select_related('user').all()[:20]
    recent_signals = SignalHistory.objects.select_related('user').all()[:10]

    top_stocks = (
        SignalHistory.objects.values('symbol')
        .annotate(count=Count('symbol'))
        .order_by('-count')[:5]
    )

    context = {
        'total_users': total_users,
        'pending_users': pending_users,
        'active_users': active_users,
        'total_signals': total_signals,
        'total_analyses': total_analyses,
        'recent_activities': recent_activities,
        'recent_signals': recent_signals,
        'top_stocks': top_stocks,
    }
    return render(request, 'stocks/admin_dashboard.html', context)


@admin_required
def admin_users(request):
    filter_type = request.GET.get('filter', 'all')

    if filter_type == 'pending':
        users = User.objects.filter(is_active=False, is_superuser=False)
    elif filter_type == 'active':
        users = User.objects.filter(is_active=True, is_superuser=False)
    else:
        users = User.objects.filter(is_superuser=False)

    users = users.select_related('profile').order_by('-date_joined')
    pending_users = User.objects.filter(is_active=False, is_superuser=False).count()

    context = {
        'users': users,
        'filter_type': filter_type,
        'total': users.count(),
        'pending_users': pending_users,
    }
    return render(request, 'stocks/admin_users.html', context)


@admin_required
def admin_user_detail(request, user_id):
    target_user = get_object_or_404(User, id=user_id, is_superuser=False)
    profile = getattr(target_user, 'profile', None)
    activities = ActivityLog.objects.filter(user=target_user)[:30]
    signals = SignalHistory.objects.filter(user=target_user)[:20]
    watchlist = Watchlist.objects.filter(user=target_user)

    context = {
        'target_user': target_user,
        'profile': profile,
        'activities': activities,
        'signals': signals,
        'watchlist': watchlist,
    }
    return render(request, 'stocks/admin_user_detail.html', context)


@admin_required
def admin_approve_user(request, user_id):
    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        target_user.is_active = True
        target_user.save()
        log_activity(request.user, 'analyze', detail=f'Approved user {target_user.username}', request=request)
    return redirect('admin_users')


@admin_required
def admin_reject_user(request, user_id):
    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        username = target_user.username
        target_user.delete()
        log_activity(request.user, 'analyze', detail=f'Rejected/deleted user {username}', request=request)
    return redirect('admin_users')


@admin_required
def admin_deactivate_user(request, user_id):
    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        target_user.is_active = False
        target_user.save()
    return redirect('admin_users')


@admin_required
def admin_signals(request):
    signals = SignalHistory.objects.select_related('user').all()[:100]
    return render(request, 'stocks/admin_signals.html', {'signals': signals})


@admin_required
def admin_activity(request):
    activities = ActivityLog.objects.select_related('user').all()[:200]
    return render(request, 'stocks/admin_activity.html', {'activities': activities})


@admin_required
def admin_clear_cache(request):
    if request.method == 'POST':
        StockCache.objects.all().delete()
    return redirect('admin_dashboard')


# ─── Public Market API ─────────────────────────────────────

def market_overview(request):
    """Public endpoint — returns Nifty 50, Sensex and VIX via yfinance"""
    data = get_market_overview()
    return JsonResponse({
        'nifty':  data.get('nifty',  {'price': 0, 'change': 0, 'change_pct': 0}),
        'sensex': data.get('sensex', {'price': 0, 'change': 0, 'change_pct': 0}),
        'vix':    data.get('vix',    {'price': 0, 'change': 0, 'change_pct': 0}),
    })


def market_news(request):
    import requests as req
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone, timedelta
    import re

    feeds = [
        ('Economic Times', 'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms'),
        ('ET Markets', 'https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms'),
        ('Mint Markets', 'https://www.livemint.com/rss/markets'),
        ('Business Std', 'https://www.business-standard.com/rss/markets-106.rss'),
    ]

    def parse_pub_date(pub):
        if not pub:
            return None
        pub = re.sub(r'^[A-Za-z]+,\s*', '', pub.strip())
        for fmt in ['%d %b %Y %H:%M:%S %z', '%d %b %Y %H:%M:%S %Z', '%d %b %Y %H:%M:%S']:
            try:
                dt = datetime.strptime(pub[:30], fmt)
                if dt.tzinfo:
                    return dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            except Exception:
                continue
        return None

    def time_ago(dt):
        if not dt:
            return ''
        diff = datetime.utcnow() - dt
        mins = int(diff.total_seconds() / 60)
        if mins < 0:    return 'just now'
        if mins < 60:   return f'{mins}m ago'
        if mins < 1440: return f'{mins // 60}h ago'
        return f'{mins // 1440}d ago'

    news_items = []
    ONE_MONTH_AGO = datetime.utcnow() - timedelta(days=30)

    for source_name, feed_url in feeds:
        try:
            r = req.get(feed_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=7)
            root = ET.fromstring(r.content)
            channel = root.find('channel')
            items = channel.findall('item') if channel else []
            for item in items[:8]:
                title = item.findtext('title', '').strip()
                link = item.findtext('link', '').strip()
                pub = item.findtext('pubDate', '').strip()
                if not title or not link:
                    continue
                title = re.sub(r'&#\d+;|&amp;|&lt;|&gt;', '', title).strip()
                dt = parse_pub_date(pub)
                if dt and dt < ONE_MONTH_AGO:
                    continue
                news_items.append({
                    'title': title,
                    'link': link,
                    'time': time_ago(dt),
                    'source': source_name,
                    'timestamp': dt.isoformat() if dt else '',
                    'dt': dt,
                })
        except Exception:
            continue

    news_items.sort(key=lambda x: x['dt'] or datetime.min, reverse=True)
    seen, unique = set(), []
    for n in news_items:
        key = n['title'][:60].lower()
        if key not in seen:
            seen.add(key)
            n.pop('dt')
            unique.append(n)

    response = JsonResponse({'news': unique[:12]})
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response


# ─── News & Screener Pages ─────────────────────────────────

@login_required(login_url='login')
def news_view(request):
    return render(request, 'stocks/news.html')


@login_required(login_url='login')
def screener_view(request):
    return render(request, 'stocks/screener.html')


@login_required(login_url='login')
def candles_view(request):
    return render(request, 'stocks/candles.html')


@login_required(login_url='login')
def screener_api(request):
    SCREENER_STOCKS = [
        'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK',
        'SBIN', 'WIPRO', 'BAJFINANCE', 'MARUTI', 'TATAMOTORS',
        'AXISBANK', 'KOTAKBANK', 'HINDUNILVR', 'BHARTIARTL', 'ITC',
        'SUNPHARMA', 'HCLTECH', 'LT', 'TATASTEEL', 'TITAN',
    ]
    results = []
    for symbol in SCREENER_STOCKS:
        try:
            quote = get_quote(symbol)
            candles = get_candle_data(symbol, 'D')
            if quote and candles:
                ind = calculate_indicators(candles)
                if ind:
                    sig = generate_signal(ind, 'swing')
                    scores = score_trade_types(ind, candles)
                    if sig and scores:
                        results.append({
                            'symbol': symbol,
                            'quote': quote,
                            'signal': sig['signal'],
                            'signal_color': sig['signal_color'],
                            'risk': sig['risk'],
                            'rsi': ind['rsi'],
                            'volume_spike': ind['volume_spike'],
                            'intraday_score': scores['intraday'],
                            'swing_score': scores['swing'],
                            'longterm_score': scores['longterm'],
                            'best': scores['best_label'],
                        })
        except Exception:
            continue
    return JsonResponse({'results': results, 'count': len(results)})


# ─── User Profile ──────────────────────────────────────────

@login_required(login_url='login')
def profile_view(request):
    profile = getattr(request.user, 'profile', None)

    if request.method == 'POST':
        # Update user info
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name',  '').strip()
        email      = request.POST.get('email',      '').strip()

        if first_name:
            request.user.first_name = first_name
        if last_name:
            request.user.last_name = last_name
        if email:
            if not User.objects.filter(email=email).exclude(id=request.user.id).exists():
                request.user.email = email
        request.user.save()

        if profile:
            phone      = request.POST.get('phone',               '').strip()
            experience = request.POST.get('trading_experience',  '')
            trade_type = request.POST.get('preferred_trade_type','')
            capital    = request.POST.get('capital',             '')

            if phone is not None:
                profile.phone = phone
            if experience in ('beginner', 'intermediate', 'expert'):
                profile.trading_experience = experience
            if trade_type in ('intraday', 'swing', 'longterm'):
                profile.preferred_trade_type = trade_type
            if capital:
                try:
                    profile.capital = float(capital)
                except (ValueError, TypeError):
                    pass
            profile.save()

        log_activity(request.user, 'profile_update', detail='Updated profile', request=request)
        return redirect('/profile/?saved=1')

    # ── STATS ──────────────────────────────────────────────────
    total_analyses  = ActivityLog.objects.filter(user=request.user, action='analyze').count()
    total_signals   = SignalHistory.objects.filter(user=request.user).count()
    watchlist_count = Watchlist.objects.filter(user=request.user).count()
    recent_signals  = SignalHistory.objects.filter(user=request.user).order_by('-created_at')[:10]
    recent_activity = ActivityLog.objects.filter(user=request.user).order_by('-timestamp')[:10]

    # Most analyzed stocks with bar chart data
    top_stocks = (
        SignalHistory.objects.filter(user=request.user)
        .values('symbol')
        .annotate(count=Count('symbol'))
        .order_by('-count')[:5]
    )

    # Signal breakdown
    buy_signals  = SignalHistory.objects.filter(user=request.user, signal__in=['BUY', 'STRONG BUY']).count()
    sell_signals = SignalHistory.objects.filter(user=request.user, signal__in=['SELL', 'STRONG SELL']).count()
    wait_signals = SignalHistory.objects.filter(user=request.user, signal='WAIT').count()

    # Most active day of the week
    from django.db.models.functions import ExtractWeekDay
    most_active = (
        ActivityLog.objects.filter(user=request.user, action='analyze')
        .annotate(day=ExtractWeekDay('timestamp'))
        .values('day')
        .annotate(count=Count('day'))
        .order_by('-count')
        .first()
    )
    day_names  = {1:'Sunday',2:'Monday',3:'Tuesday',4:'Wednesday',5:'Thursday',6:'Friday',7:'Saturday'}
    active_day = day_names.get(most_active['day'], '—') if most_active else '—'

    # Watchlist items for profile display
    watchlist_items = Watchlist.objects.filter(user=request.user)[:8]

    context = {
        'profile':         profile,
        'total_analyses':  total_analyses,
        'total_signals':   total_signals,
        'watchlist_count': watchlist_count,
        'recent_signals':  recent_signals,
        'recent_activity': recent_activity,
        'top_stocks':      top_stocks,
        'buy_signals':     buy_signals,
        'sell_signals':    sell_signals,
        'wait_signals':    wait_signals,
        'active_day':      active_day,
        'watchlist_items': watchlist_items,
    }
    return render(request, 'stocks/profile.html', context)

# ─── Live Price Endpoint ────────────────────────────────────────
@login_required(login_url='login')
def live_price(request):
    symbol = request.GET.get('symbol', '').upper().strip()
    if not symbol:
        return JsonResponse({'error': 'No symbol'}, status=400)

    from .utils import get_upstox_quote, to_yf_symbol
    import yfinance as yf

    # ── 3-second server-side cache to reduce load ──
    cache_key = f'live_{symbol}'
    try:
        cache_obj = StockCache.objects.get(symbol=symbol, data_type=cache_key)
        age = (timezone.now() - cache_obj.cached_at).total_seconds()
        if age < 3 and cache_obj.data:
            return JsonResponse(cache_obj.data)
    except StockCache.DoesNotExist:
        pass

    # Use user's personal Upstox token if saved, else .env fallback
    profile    = getattr(request.user, 'profile', None)
    user_token = getattr(profile, 'upstox_token', '').strip() or None

    quote = get_upstox_quote(symbol, access_token=user_token)

    if not quote:
        try:
            info = yf.Ticker(to_yf_symbol(symbol)).fast_info
            cur  = round(float(info.last_price), 2)
            prev = round(float(info.previous_close), 2)
            if cur > 0:
                chg = round(cur - prev, 2)
                pct = round((chg / prev) * 100, 2) if prev else 0
                quote = {
                    'current': cur, 'change': chg, 'change_pct': pct,
                    'high': round(float(info.day_high), 2),
                    'low':  round(float(info.day_low),  2),
                    'source': 'yfinance',
                }
        except Exception:
            pass

    if not quote:
        return JsonResponse({'error': 'Could not fetch price'}, status=404)

    result = {
        'price':      quote['current'],
        'change':     quote['change'],
        'change_pct': quote['change_pct'],
        'high':       quote.get('high', 0),
        'low':        quote.get('low',  0),
        'source':     quote.get('source', 'unknown'),
        'symbol':     symbol,
    }

    # Save to cache
    StockCache.objects.update_or_create(
        symbol=symbol, data_type=cache_key,
        defaults={'data': result}
    )

    return JsonResponse(result)

@login_required(login_url='login')
def ws_token(request):
    """Return the Upstox WebSocket auth URL + access token for front-end streaming.
    Uses user's personal token if saved, otherwise falls back to .env token.
    """
    profile     = getattr(request.user, 'profile', None)
    user_token  = getattr(profile, 'upstox_token', '').strip()
    access_token = user_token or getattr(settings, 'UPSTOX_ACCESS_TOKEN', '')

    if not access_token:
        return JsonResponse({'error': 'No Upstox token configured'}, status=503)

    try:
        import requests as req
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        }
        r = req.get(
            'https://api.upstox.com/v2/feed/market-data-feed/authorize',
            headers=headers,
            timeout=5,
        )
        if r.status_code != 200:
            return JsonResponse({'error': f'Upstox auth failed: {r.status_code}'}, status=502)
        data = r.json()
        ws_url = data.get('data', {}).get('authorizedRedirectUri', '')
        if not ws_url:
            return JsonResponse({'error': 'No WebSocket URL from Upstox'}, status=502)
        return JsonResponse({'ws_url': ws_url, 'token': access_token})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    




@csrf_exempt
@login_required(login_url='login')
def paper_trade_open(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    symbol     = data.get('symbol', '').upper().strip()
    direction  = data.get('direction', 'buy')
    trade_type = data.get('trade_type', 'swing')
    entry      = float(data.get('entry_price', 0))
    sl         = float(data.get('stop_loss', 0))
    t1         = float(data.get('target1', 0))
    t2         = float(data.get('target2', 0))
    t3         = float(data.get('target3', 0)) if data.get('target3') else None
    qty        = int(data.get('quantity', 1))

    if not symbol or not entry:
        return JsonResponse({'error': 'Missing required fields'}, status=400)

    trade = PaperTrade.objects.create(
        user=request.user, symbol=symbol, direction=direction,
        trade_type=trade_type, entry_price=entry, stop_loss=sl,
        target1=t1, target2=t2, target3=t3, quantity=qty,
    )
    log_activity(request.user, 'paper_trade', detail=f'{direction.upper()} {qty} {symbol} @ ₹{entry}', request=request)
    return JsonResponse({'status': 'opened', 'trade_id': trade.id})


@csrf_exempt
@login_required(login_url='login')
def paper_trade_close(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    trade_id   = data.get('trade_id')
    exit_price = float(data.get('exit_price', 0))
    result     = data.get('result', 'manual')

    try:
        trade = PaperTrade.objects.get(id=trade_id, user=request.user, status='active')
    except PaperTrade.DoesNotExist:
        return JsonResponse({'error': 'Trade not found'}, status=404)

    pnl, pct        = trade.calculate_pnl(exit_price)
    trade.exit_price = exit_price
    trade.pnl        = pnl
    trade.pnl_pct    = pct
    trade.result     = result
    trade.status     = 'closed'
    trade.closed_at  = timezone.now()
    trade.save()

    return JsonResponse({'status': 'closed', 'pnl': pnl, 'pnl_pct': pct})


@login_required(login_url='login')
def paper_trade_list(request):
    active = PaperTrade.objects.filter(user=request.user, status='active')
    closed = PaperTrade.objects.filter(user=request.user, status='closed')[:30]

    # Use user's personal token if saved, else .env fallback
    profile    = getattr(request.user, 'profile', None)
    user_token = getattr(profile, 'upstox_token', '').strip() or None

    # ── ONE bulk Upstox call for ALL active trade symbols ──────────────────
    # Cost: 2 HTTP requests total, regardless of how many trades exist.
    active_symbols = list({t.symbol for t in active})
    bulk_quotes    = get_upstox_quotes_bulk(active_symbols, access_token=user_token)

    active_data = []
    for t in active:
        # Use bulk result; fallback to cached yfinance quote for non-Upstox symbols
        quote = get_live_price_for_trade(t.symbol, bulk_quotes=bulk_quotes)
        cur   = quote['current'] if quote else t.entry_price
        pnl, pct = t.calculate_pnl(cur)
        active_data.append({
            'id': t.id, 'symbol': t.symbol,
            'direction': t.direction, 'trade_type': t.trade_type,
            'entry_price': t.entry_price, 'stop_loss': t.stop_loss,
            'target1': t.target1, 'target2': t.target2,
            'target3': t.target3, 'quantity': t.quantity,
            'current_price': cur, 'pnl': pnl, 'pnl_pct': round(pct, 2),
            'created_at': t.created_at.strftime('%d %b %H:%M'),
        })

    closed_data = [{
        'id': t.id, 'symbol': t.symbol,
        'direction': t.direction, 'trade_type': t.trade_type,
        'entry_price': t.entry_price, 'exit_price': t.exit_price,
        'quantity': t.quantity, 'pnl': t.pnl,
        'pnl_pct': t.pnl_pct, 'result': t.result,
        'closed_at': t.closed_at.strftime('%d %b %H:%M') if t.closed_at else '',
    } for t in closed]

    all_closed = PaperTrade.objects.filter(user=request.user, status='closed')
    total_pnl  = sum(t.pnl or 0 for t in all_closed)
    wins       = all_closed.filter(pnl__gt=0).count()
    losses     = all_closed.filter(pnl__lte=0).count()
    total      = all_closed.count()
    win_rate   = round((wins / total) * 100) if total > 0 else 0

    return JsonResponse({
        'active':  active_data,
        'closed':  closed_data,
        'summary': {
            'total_pnl': round(total_pnl, 2),
            'wins': wins, 'losses': losses,
            'total': total, 'win_rate': win_rate,
        }
    })


@login_required(login_url='login')
def trades_view(request):
    return render(request, 'stocks/trades.html')