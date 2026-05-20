/**
 * StockAnalyzer — main.js
 * Complete dashboard logic: chart, signals, watchlist, alerts, screener
 */

'use strict';

// ─────────────────────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────────────────────

let currentSymbol    = null;
let currentTradeType = 'swing';
let currentTimeframe = '60';
let currentData      = null;   // last full API response
let priceAlerts      = JSON.parse(localStorage.getItem('sa_alerts') || '[]');
let signalHistory    = JSON.parse(localStorage.getItem('sa_history') || '[]');

// Chart objects
let chart            = null;
let candleSeries     = null;
let ema9Series       = null;
let ema21Series      = null;
let bbUpperSeries    = null;
let bbLowerSeries    = null;
let volumeSeries     = null;
let rsiSeries        = null;
let macdSeries       = null;
let macdSignalSeries = null;

// S/R price-line tracker — prevents stacking on re-analyze
let srPriceLines     = [];

// Timers
let marketStripTimer = null;
let autoRefreshTimer = null;
let alertCheckTimer  = null;
let clockTimer       = null;
let liveTickTimer    = null;
let livePriceLine    = null;

// ─────────────────────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // Wrap each init in try-catch so one failure doesn't stop the rest
    try { initChart(); } catch(e) { console.error('initChart error:', e); }
    try { initSearch(); } catch(e) { console.error('initSearch error:', e); }
    try { updateMarketStrip(); } catch(e) { console.error('updateMarketStrip error:', e); }
    try { startClock(); } catch(e) { console.error('startClock error:', e); }
    try { renderAlerts(); } catch(e) { console.error('renderAlerts error:', e); }
    try { loadWatchlistSignals(); } catch(e) { console.error('loadWatchlistSignals error:', e); }
    try { renderSignalHistory(); } catch(e) { console.error('renderSignalHistory error:', e); }
    try { renderCandleStudyEmpty(); } catch(e) { console.error('renderCandleStudyEmpty error:', e); }

    // Auto refresh market strip every 60s
    marketStripTimer = setInterval(updateMarketStrip, 120000);

    // Auto refresh analysis every 5 min
    autoRefreshTimer = setInterval(() => {
        if (currentSymbol) {
            analyzeStock(currentSymbol, true);
        }
    }, 300000);

    // Check price alerts every 60s
    alertCheckTimer = setInterval(checkPriceAlerts, 60000);
        // Start paper trade monitor
    startPaperTradeMonitor();

    // Keyboard shortcut: / → focus search
    document.addEventListener('keydown', e => {
        if (e.key === '/' && document.activeElement !== document.getElementById('searchInput')) {
            e.preventDefault();
            document.getElementById('searchInput').focus();
        }
        if (e.key === 'Escape') {
            hideUpstoxModal();
        }
    });
    // Auto-load stock from URL ?stock=SYMBOL (from screener/news links)
const urlStock = new URLSearchParams(window.location.search).get('stock');
if (urlStock) setTimeout(() => analyzeStock(urlStock.toUpperCase()), 300);
});

// ─────────────────────────────────────────────────────────────
// CHART INIT
// ─────────────────────────────────────────────────────────────

function initChart() {
    const container = document.getElementById('mainChart');
    if (!container || !window.LightweightCharts) return;

    chart = LightweightCharts.createChart(container, {
    layout: {
        background: { color: '#0a0e1a' },
        textColor:  '#ffffff80',
    },
    grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.04)' },
    },
    crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: 'rgba(0,255,136,0.3)', labelBackgroundColor: '#0a0e1a' },
        horzLine: { color: 'rgba(0,255,136,0.3)', labelBackgroundColor: '#0a0e1a' },
    },
    rightPriceScale: {
        borderColor: 'rgba(255,255,255,0.07)',
        textColor:   'rgba(255,255,255,0.4)',
    },
    timeScale: {
        borderColor:     'rgba(255,255,255,0.07)',
        textColor:       'rgba(255,255,255,0.4)',
        timeVisible:     true,
        secondsVisible:  false,
    },
    handleScroll: true,
    handleScale:  true,
    width:  container.clientWidth  || 800,
    height: 450,
});

    // Candlestick series
    candleSeries = chart.addCandlestickSeries({
        upColor:       '#00ff88',
        downColor:     '#ff4757',
        borderUpColor: '#00ff88',
        borderDownColor: '#ff4757',
        wickUpColor:   '#00ff88',
        wickDownColor: '#ff4757',
    });

    // EMA 9 — blue
    ema9Series = chart.addLineSeries({
        color:     '#00aaff',
        lineWidth: 1,
        title:     'EMA9',
        priceLineVisible: false,
        lastValueVisible: false,
    });

    // EMA 21 — orange
    ema21Series = chart.addLineSeries({
        color:     '#ff8c00',
        lineWidth: 1,
        title:     'EMA21',
        priceLineVisible: false,
        lastValueVisible: false,
    });

    // BB Upper — dotted red
    bbUpperSeries = chart.addLineSeries({
        color:          '#ff4757',
        lineWidth:      1,
        lineStyle:      LightweightCharts.LineStyle.Dashed,
        title:          'BB Upper',
        priceLineVisible: false,
        lastValueVisible: false,
    });

    // BB Lower — dotted green
    bbLowerSeries = chart.addLineSeries({
        color:          '#00ff88',
        lineWidth:      1,
        lineStyle:      LightweightCharts.LineStyle.Dashed,
        title:          'BB Lower',
        priceLineVisible: false,
        lastValueVisible: false,
    });

    // Volume — histogram (separate pane)
    volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        scaleMargins: { top: 0.85, bottom: 0 },
    });

    // Resize observer — properly create ResizeObserver
    const ro = new ResizeObserver(() => {
        if (chart && container) {
            chart.applyOptions({ width: container.clientWidth });
        }
    });
    ro.observe(container);

    // Fallback for older browsers
    window.addEventListener('resize', () => {
        if (chart && container) {
            chart.applyOptions({ width: container.clientWidth });
        }
    });
}

// ─────────────────────────────────────────────────────────────
// SEARCH
// ─────────────────────────────────────────────────────────────

function initSearch() {
    const inp = document.getElementById('searchInput');
    if (!inp) return;
    inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
            const sym = inp.value.trim().toUpperCase();
            if (sym) {
                analyzeStock(sym);
                inp.blur();
            }
        }
    });
}

// ─────────────────────────────────────────────────────────────
// MAIN ANALYZE FUNCTION
// ─────────────────────────────────────────────────────────────

function analyzeStock(symbol, silent = false) {
    if (!symbol) return;
    // Normalize: strip .NS/.BO suffix for internal use (backend adds it back)
    symbol = symbol.toUpperCase().trim().replace(/\.(NS|BO)$/, '');
    currentSymbol = symbol;
    stopLiveTicker();

    // Update search input
    const inp = document.getElementById('searchInput');
    if (inp && !silent) inp.value = symbol;

    // Show loading
    if (!silent) {
        showLoading(`Analyzing ${symbol}...`);
    }

    // Highlight active watchlist item
    document.querySelectorAll('.watchlist-item').forEach(el => {
        el.classList.toggle('active', el.dataset.symbol === symbol);
    });

    fetch(`/api/analyze/?symbol=${symbol}&trade_type=${currentTradeType}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                hideLoading();
                showToast('⚠ ' + data.error, 'red');
                return;
            }

            currentData = data;
            hideLoading();
            hideWelcome();

            // Update all panels
            updateStockInfoBar(data.quote, symbol);
            updateChart(data.candle_data, data.indicators, data.support_resistance);
            updateSignalPanel(data);
            fetchStockNews(symbol);
            updateWatchlistButton(data.in_watchlist);

            // Save to signal history
            saveToHistory(symbol, data.signal?.signal);

            startLiveTicker(symbol);
            if (!silent) showToast(`✓ ${symbol} analyzed`, 'green');
        })
        .catch(err => {
            hideLoading();
            showToast('Connection error. Try again.', 'red');
            console.error(err);
        });
}

// ─────────────────────────────────────────────────────────────
// CHART UPDATE
// ─────────────────────────────────────────────────────────────

function updateChart(candles, indicators, sr) {
    if (!chart || !candles || !candleSeries) return;

    const timestamps = candles.timestamps;
    const overlayMode = document.getElementById('overlaySelect')?.value || 'auto';

    // Candles
    const candleData = timestamps.map((t, i) => ({
        time:  t,
        open:  candles.open[i],
        high:  candles.high[i],
        low:   candles.low[i],
        close: candles.close[i],
    }));
    candleSeries.setData(candleData);

    // Volume
    const volumeData = timestamps.map((t, i) => ({
        time:  t,
        value: candles.volume[i],
        color: candles.close[i] >= candles.open[i]
            ? 'rgba(0,255,136,0.3)'
            : 'rgba(255,71,87,0.3)',
    }));
    volumeSeries.setData(volumeData);

    // Overlays
    const showEma = overlayMode === 'ema' || overlayMode === 'auto' || overlayMode === 'all';
    const showBb  = overlayMode === 'bb'  || overlayMode === 'auto' || overlayMode === 'all';
if (showEma && indicators) {
    const closes = candles.close;

    function calcEMA(arr, period) {
        const k = 2 / (period + 1);
        let ema = arr[0];
        return arr.map((v, i) => {
            if (i === 0) { ema = v; return Math.round(ema * 100) / 100; }
            ema = v * k + ema * (1 - k);
            return Math.round(ema * 100) / 100;
        });
    }

    const ema9vals  = calcEMA(closes, 9);
    const ema21vals = calcEMA(closes, 21);

    ema9Series.setData(timestamps.map((t, i) => ({ time: t, value: ema9vals[i] })));
    ema21Series.setData(timestamps.map((t, i) => ({ time: t, value: ema21vals[i] })));
} else {
    ema9Series.setData([]);
    ema21Series.setData([]);
}

    if (showBb && indicators) {
        const closes = candles.close;
        const period = 20;
        const stdMult = 2;
        const bbUpVals = [];
        const bbLoVals = [];
        for (let i = 0; i < closes.length; i++) {
            if (i < period - 1) {
                bbUpVals.push(null);
                bbLoVals.push(null);
            } else {
                const slice = closes.slice(i - period + 1, i + 1);
                const mean  = slice.reduce((a, b) => a + b, 0) / period;
                const std   = Math.sqrt(slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period);
                bbUpVals.push(Math.round((mean + stdMult * std) * 100) / 100);
                bbLoVals.push(Math.round((mean - stdMult * std) * 100) / 100);
            }
        }
        bbUpperSeries.setData(timestamps
            .map((t, i) => bbUpVals[i] !== null ? { time: t, value: bbUpVals[i] } : null)
            .filter(Boolean));
        bbLowerSeries.setData(timestamps
            .map((t, i) => bbLoVals[i] !== null ? { time: t, value: bbLoVals[i] } : null)
            .filter(Boolean));
    } else {
        bbUpperSeries.setData([]);
        bbLowerSeries.setData([]);
    }

    // Support & Resistance price lines — remove old ones first to prevent stacking
    srPriceLines.forEach(line => { try { candleSeries.removePriceLine(line); } catch(e) {} });
    srPriceLines = [];

    if (sr) {
        // Resistance lines — red
        (sr.resistance || []).forEach(r => {
            const price = r.price || r;
            const line = candleSeries.createPriceLine({
                price:            price,
                color:            'rgba(255,71,87,0.6)',
                lineWidth:        1,
                lineStyle:        LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title:            `R ₹${fmt(price)}`,
            });
            srPriceLines.push(line);
        });

        // Support lines — green
        (sr.support || []).forEach(s => {
            const price = s.price || s;
            const line = candleSeries.createPriceLine({
                price:            price,
                color:            'rgba(0,255,136,0.6)',
                lineWidth:        1,
                lineStyle:        LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title:            `S ₹${fmt(price)}`,
            });
            srPriceLines.push(line);
        });
    }

    // Pattern markers on latest candle
    if (currentData?.patterns?.length) {
        const lastTs = timestamps[timestamps.length - 1];
        const lastClose = candles.close[candles.close.length - 1];
        const markers = currentData.patterns.slice(0, 2).map(p => ({
            time:     lastTs,
            position: p.type === 'bullish' ? 'belowBar' : 'aboveBar',
            color:    p.type === 'bullish' ? '#00ff88' : p.type === 'bearish' ? '#ff4757' : '#ffc400',
            shape:    p.type === 'bullish' ? 'arrowUp' : p.type === 'bearish' ? 'arrowDown' : 'circle',
            text:     p.emoji + ' ' + p.name,
        }));
        candleSeries.setMarkers(markers);
    } else {
        candleSeries.setMarkers([]);
    }

    chart.timeScale().fitContent();
}

function updateOverlays() {
    if (!currentData) return;
    updateChart(currentData.candle_data, currentData.indicators, currentData.support_resistance);
}

// ─────────────────────────────────────────────────────────────
// STOCK INFO BAR
// ─────────────────────────────────────────────────────────────

function updateStockInfoBar(quote, symbol) {
    document.getElementById('sibEmpty')?.classList.add('hidden');

    const sym  = document.getElementById('sibSym');
    const pr   = document.getElementById('sibPrice');
    const chg  = document.getElementById('sibChg');
    const det  = document.getElementById('sibDetail');

    // Recalculate change if it came back as 0 but prev_close differs from current
    let change    = quote.change    || 0;
    let changePct = quote.change_pct || 0;
    const cur  = quote.current    || 0;
    const prev = quote.prev_close || cur;
    if (change === 0 && prev > 0 && Math.abs(cur - prev) > 0.005) {
        change    = round2(cur - prev);
        changePct = round2((change / prev) * 100);
    }

    if (sym)  { sym.textContent  = symbol; sym.classList.remove('hidden'); }
    if (pr)   {
        pr.textContent = `₹${fmt(cur)}`;
        pr.className   = 'sib-price ' + (change >= 0 ? 'up' : 'down');
        pr.classList.remove('hidden');
    }
    if (chg)  {
        const sign = change >= 0 ? '+' : '';
        chg.textContent = `${sign}${fmt(change)} (${sign}${changePct.toFixed(2)}%)`;
        chg.className   = 'sib-chg ' + (change >= 0 ? 'up' : 'down');
        chg.classList.remove('hidden');
    }
    if (det)  {
        det.innerHTML = `
            O: <span>₹${fmt(quote.open)}</span>
            &nbsp; H: <span>₹${fmt(quote.high)}</span>
            &nbsp; L: <span>₹${fmt(quote.low)}</span>
            &nbsp; Prev: <span>₹${fmt(prev)}</span>
            &nbsp; Src: <span>${quote.source || 'yfinance'}</span>
        `;
        det.classList.remove('hidden');
    }
}

// ─────────────────────────────────────────────────────────────
// FULL SIGNAL PANEL UPDATE
// ─────────────────────────────────────────────────────────────

function updateSignalPanel(data) {
    const { signal, indicators, trade_scores, patterns, support_resistance } = data;
    if (!signal || !indicators) return;

    updateIndicatorBar(indicators);
    updateVerdict(signal, indicators);
    updateTradeScores(trade_scores);
    updateSignalBox(signal);
    updatePriceLevels(signal);
    updatePositionCalc();
    updateIndicatorsDetail(indicators, signal);
    updatePatterns(patterns);
    updateSupportResistance(support_resistance, indicators.current_price);
    updateAnalysisDescription(data, currentSymbol);
    fillPaperTradeForm(data);

}

// ── INDICATOR BAR (bottom of chart) ──────────────────────────

function updateIndicatorBar(ind) {
    const rsi = ind.rsi;
    const rsiColor = rsi < 35 ? '#00ff88' : rsi > 65 ? '#ff4757' : '#ffc400';

    setEl('ibRsi',  `${rsi}`, rsiColor);
    setEl('ibMacd', `${ind.macd_diff}`, ind.macd_diff > 0 ? '#00ff88' : '#ff4757');
    setEl('ibBb',   `${ind.bb_width || '—'}%`);
    setEl('ibEma9', fmt(ind.ema9));
    setEl('ibEma21',fmt(ind.ema21));
    setEl('ibSma50',fmt(ind.sma50));
    setEl('ibAtr',  fmt(ind.atr));
    setEl('ibVol',  ind.volume_spike ? '⚡ SPIKE' : 'Normal', ind.volume_spike ? '#ffc400' : null);
}

// ── VERDICT ───────────────────────────────────────────────────

function updateVerdict(signal, ind) {
    const sec = document.getElementById('verdictSection');
    if (!sec) return;

    const sig = signal.signal;
    const rsi = ind.rsi;
    const md  = ind.macd_diff;
    const e9  = ind.ema9;
    const e21 = ind.ema21;

    let cls, icon, title, points;

    if (sig === 'STRONG BUY' || sig === 'BUY') {
        cls   = 'good';
        icon  = '✅';
        title = 'GOOD TO TRADE';
        points = buildVerdictPoints(ind, 'bull');
    } else if (sig === 'STRONG SELL' || sig === 'SELL') {
        cls   = 'bad';
        icon  = '❌';
        title = 'AVOID / EXIT';
        points = buildVerdictPoints(ind, 'bear');
    } else {
        cls   = 'caution';
        icon  = '⚠️';
        title = 'CAUTION — MIXED SIGNALS';
        points = buildVerdictPoints(ind, 'neutral');
    }

    sec.innerHTML = `
        <div class="rs-title">Today's Verdict</div>
        <div class="verdict-box ${cls}">
            <div class="verdict-title">${icon} ${title}</div>
            <div class="verdict-points">${points}</div>
        </div>
    `;
}

function buildVerdictPoints(ind, type) {
    const pts = [];
    if (ind.rsi < 35) pts.push(`RSI ${ind.rsi} — oversold zone`);
    else if (ind.rsi > 65) pts.push(`RSI ${ind.rsi} — overbought zone`);
    else pts.push(`RSI ${ind.rsi} — neutral zone`);

    if (ind.macd_diff > 0) pts.push('MACD bullish — upward momentum');
    else pts.push('MACD bearish — downward pressure');

    if (ind.ema9 > ind.ema21) pts.push('EMA golden cross — buy signal');
    else pts.push('EMA death cross — sell signal');

    if (ind.volume_spike) pts.push('⚡ Volume spike — strong conviction');

    return pts.map(p => `• ${p}`).join('<br>');
}

// ── TRADE SCORES ─────────────────────────────────────────────

function updateTradeScores(scores) {
    if (!scores) return;

    const i = scores.intraday || 0;
    const s = scores.swing    || 0;
    const l = scores.longterm || 0;
    const best = scores.best  || 'swing';

    setBarWidth('scoreBarI', i);
    setBarWidth('scoreBarS', s);
    setBarWidth('scoreBarL', l);

    setEl('scorePctI', `${i}%`);
    setEl('scorePctS', `${s}%`);
    setEl('scorePctL', `${l}%`);

    // Best badge — hide all, then show only the actual highest scorer by value
    ['scoreBestI', 'scoreBestS', 'scoreBestL'].forEach(id => {
        document.getElementById(id)?.classList.add('hidden');
    });
    const scoreMap = { intraday: i, swing: s, longterm: l };
    const maxScore = Math.max(i, s, l);
    if (maxScore > 0) {   // only badge when we have real scores
        const topKey   = Object.entries(scoreMap).reduce((a, b) => b[1] > a[1] ? b : a)[0];
        const badgeMap = { intraday: 'scoreBestI', swing: 'scoreBestS', longterm: 'scoreBestL' };
        document.getElementById(badgeMap[topKey])?.classList.remove('hidden');
    }

    // Auto-switch trade type to recommended
    if (!currentData || Object.keys(currentData).length === 0) {
        switchTradeType(best, false);
    }
}

// ── SIGNAL BOX ────────────────────────────────────────────────

function updateSignalBox(signal) {
    const sec = document.getElementById('signalSection');
    if (!sec) return;

    const sig      = signal.signal || 'WAIT';
    const bull     = signal.bull_pct || 50;
    const bear     = 100 - bull;
    const trend    = signal.trend_strength || '';
    const sigClass = sig.includes('BUY') ? 'buy' : sig.includes('SELL') ? 'sell' : 'wait';
    const wordClass = sigClass;

    let shortSellHtml = '';
    if (signal.short_sell && typeof signal.short_sell === 'object') {
        const ss = signal.short_sell;
        shortSellHtml = `
            <div style="margin-top:10px;padding:8px;background:rgba(255,71,87,0.06);border:1px solid rgba(255,71,87,0.15);border-radius:8px;font-size:10px;color:rgba(255,255,255,0.5);text-align:left;">
                <div style="font-weight:700;color:#ff4757;margin-bottom:4px;">📉 Short Sell Option</div>
                Sell at ₹${fmt(ss.sell_at)} → Buy back ₹${fmt(ss.buy_back)}<br>
                Profit: ₹${fmt(ss.profit)} (${ss.profit_pct}%)<br>
                <span style="color:rgba(255,255,255,0.3)">${ss.note}</span>
            </div>
        `;
    }

    sec.innerHTML = `
        <div class="rs-title">Signal</div>
        <div class="signal-box ${sigClass}">
            <div class="signal-word ${wordClass}">⚡ ${sig}</div>
            <div class="bull-bear-bar">
                <div class="bull-portion" style="width:${bull}%"></div>
            </div>
            <div class="bb-labels">
                <span>🐻 Bear ${bear}%</span>
                <span>🐂 Bull ${bull}%</span>
            </div>
            <div class="trend-badge">${trend}</div>
        </div>
        ${shortSellHtml}
    `;
}

// ── PRICE LEVELS ─────────────────────────────────────────────

function updatePriceLevels(signal) {
    const sec = document.getElementById('levelsSection');
    if (!sec) return;

    const entry = signal.entry_price;
    const sl    = signal.stop_loss;
    const t1    = signal.target1;
    const t2    = signal.target2;
    const t3    = signal.target2 ? round2(signal.target2 * 1.06) : null;
    const rr    = signal.risk_reward;

    const pct = (price) => {
        const p = ((price - entry) / entry * 100);
        return (p >= 0 ? '+' : '') + p.toFixed(1) + '%';
    };

    sec.innerHTML = `
        <div class="rs-title">Price Levels</div>
        <table class="levels-table">
            <thead>
                <tr>
                    <th>Level</th>
                    <th>Price</th>
                    <th>Change</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td class="level-name">Entry</td>
                    <td class="level-entry">₹${fmt(entry)}</td>
                    <td>—</td>
                </tr>
                <tr>
                    <td class="level-name">Stop Loss</td>
                    <td class="level-sl">₹${fmt(sl)}</td>
                    <td class="level-sl">${pct(sl)} 🔴</td>
                </tr>
                <tr>
                    <td class="level-name">Target 1</td>
                    <td class="level-t1">₹${fmt(t1)}</td>
                    <td class="level-t1">${pct(t1)} 🟡</td>
                </tr>
                <tr>
                    <td class="level-name">Target 2</td>
                    <td class="level-t2">₹${fmt(t2)}</td>
                    <td class="level-t2">${pct(t2)} 🟢</td>
                </tr>
                ${t3 ? `
                <tr>
                    <td class="level-name">Target 3</td>
                    <td class="level-t3">₹${fmt(t3)}</td>
                    <td class="level-t3">${pct(t3)} 🟢</td>
                </tr>` : ''}
            </tbody>
        </table>
        <div class="rr-row">
            <span class="rr-label">Risk : Reward</span>
            <span class="rr-val">1 : ${rr}</span>
        </div>
        <div class="rr-row" style="margin-top:4px;">
            <span class="rr-label">Risk Level</span>
            <span style="font-family:'Fira Code',monospace;font-weight:700;font-size:11px;color:${signal.risk === 'LOW' ? '#00ff88' : signal.risk === 'MEDIUM' ? '#ffc400' : '#ff4757'}">${signal.risk}</span>
        </div>
    `;
}

// ── POSITION CALCULATOR ───────────────────────────────────────

function updatePositionCalc() {
    if (!currentData?.signal) return;

    const capital = parseFloat(document.getElementById('capitalInput')?.value) || 50000;
    const entry   = currentData.signal.entry_price;
    const sl      = currentData.signal.stop_loss;
    const t1      = currentData.signal.target1;
    const t2      = currentData.signal.target2;
    const t3      = t2 ? round2(t2 * 1.06) : t2;

    const shares    = Math.floor((capital * 0.98) / entry);
    const totalCost = round2(shares * entry);
    const maxLoss   = round2(shares * (entry - sl));
    const profitT1  = round2(shares * (t1 - entry));
    const profitT2  = round2(shares * (t2 - entry));
    const profitT3  = t3 ? round2(shares * (t3 - entry)) : 0;

    setEl('posShares', shares.toLocaleString('en-IN'));
    setEl('posCost',   `₹${fmtINR(totalCost)}`);
    setEl('posLoss',   `-₹${fmtINR(maxLoss)}`);
    setEl('posT1',     `+₹${fmtINR(profitT1)}`);
    setEl('posT2',     `+₹${fmtINR(profitT2)}`);
    setEl('posT3',     t3 ? `+₹${fmtINR(profitT3)}` : '—');
}

// ── INDICATORS DETAIL ─────────────────────────────────────────

function updateIndicatorsDetail(ind, signal) {
    const sec = document.getElementById('indDetailContent');
    if (!sec) return;

    const rows = [
        {
            name: 'RSI',
            val:  ind.rsi,
            bar:  ind.rsi,
            max:  100,
            color: ind.rsi < 35 ? '#00ff88' : ind.rsi > 65 ? '#ff4757' : '#ffc400',
            desc:  signal.rsi_label || (ind.rsi < 35 ? 'Oversold — bullish' : ind.rsi > 65 ? 'Overbought — bearish' : 'Neutral zone'),
        },
        {
            name: 'MACD',
            val:  ind.macd_diff,
            bar:  null,
            desc: signal.macd_label || (ind.macd_diff > 0 ? 'Bullish crossover ▲' : 'Bearish ▼'),
        },
        {
            name: 'EMA',
            val:  '',
            bar:  null,
            desc: signal.ema_label || (ind.ema9 > ind.ema21 ? 'Golden Cross 🟢 EMA9 > EMA21' : 'Death Cross 🔴 EMA9 < EMA21'),
        },
        {
            name: 'BB',
            val:  '',
            bar:  null,
            desc: signal.bb_label || (ind.current_price <= ind.bb_lower ? 'Near lower band — support 🟢' : ind.current_price >= ind.bb_upper ? 'Near upper band — resistance 🔴' : 'Mid band ⚪'),
        },
        {
            name: 'SMA50',
            val:  ind.sma50 ? fmt(ind.sma50) : '—',
            bar:  null,
            desc: ind.sma50 ? (ind.current_price > ind.sma50 ? 'Above SMA50 — uptrend 🟢' : 'Below SMA50 — downtrend 🔴') : '—',
        },
        {
            name: 'SMA200',
            val:  ind.sma200 ? fmt(ind.sma200) : '—',
            bar:  null,
            desc: ind.sma200 ? (ind.current_price > ind.sma200 ? 'Above SMA200 — bull market 🟢' : 'Below SMA200 — bear market 🔴') : 'Insufficient data',
        },
        {
            name: 'Volume',
            val:  '',
            bar:  null,
            desc: ind.volume_spike ? `⚡ Spike! ${ind.volume_ratio}x avg volume` : 'Normal — no unusual activity',
        },
    ];

    sec.innerHTML = rows.map(r => `
        <div class="ind-detail-row">
            <span class="idr-name">${r.name}</span>
            <span class="idr-val" style="color:${r.color || 'rgba(255,255,255,0.7)'}">${r.val}</span>
            <span class="idr-desc">${r.desc}</span>
        </div>
    `).join('');
}

// ── CANDLE PATTERNS ───────────────────────────────────────────

function updatePatterns(patterns) {
    const sec = document.getElementById('patternsContent');
    if (!sec) return;

    if (!patterns || patterns.length === 0) {
        sec.innerHTML = '<div class="placeholder-text">No significant pattern on latest candles</div>';
        renderCandleStudy([]);
        return;
    }

    sec.innerHTML = patterns.map(p => `
        <div class="pattern-card">
            <div class="pattern-card-top">
                ${buildCandleVisual(p.candle_visual)}
                <div class="pattern-info">
                    <div class="pattern-header">
                        <span style="font-size:16px">${p.emoji}</span>
                        <span class="pattern-name pattern-type-${p.type}">${p.name}</span>
                    </div>
                    <div class="pattern-rel">${p.confidence || p.reliability}% confidence</div>
                </div>
            </div>
            <div class="pattern-meaning">${p.meaning}</div>
            <div class="pattern-action" style="color:${p.type === 'bullish' ? '#00ff88' : p.type === 'bearish' ? '#ff4757' : '#ffc400'}">${p.action}</div>
            ${p.detected_at ? `<div style="font-size:9px;color:rgba(255,255,255,0.3);margin-top:3px">📍 ${p.detected_at}</div>` : ''}
        </div>
    `).join('');

    renderCandleStudy(patterns);
}

// ── CANDLE VISUAL FROM API DATA ──────────────────────────

function buildCandleVisual(candleVisual) {
    if (!candleVisual || !candleVisual.length) return '';
    return '<div class="cv-group">' + candleVisual.map(c =>
        '<div class="cv-candle cv-' + c.t + '">' +
        '<div class="cv-wick" style="height:' + c.uw + 'px"></div>' +
        '<div class="cv-body" style="height:' + c.bh + 'px"></div>' +
        '<div class="cv-wick" style="height:' + c.lw + 'px"></div>' +
        '</div>'
    ).join('') + '</div>';
}

function renderCandleStudyEmpty() {
    const grid = document.getElementById('candleStudyGrid');
    if (!grid) return;
    grid.innerHTML = '<div class="cs-empty-msg" style="grid-column:1/-1;text-align:center;padding:20px 8px;color:rgba(255,255,255,0.3);font-size:12px;line-height:1.6">📊 Analyze a stock to detect candle patterns<br><span style="font-size:10px;opacity:0.7">Patterns are detected dynamically from live candle data</span></div>';
}

function renderCandleStudy(detectedPatterns) {
    const grid = document.getElementById('candleStudyGrid');
    if (!grid) return;

    if (!detectedPatterns || detectedPatterns.length === 0) {
        grid.innerHTML = '<div class="cs-empty-msg" style="grid-column:1/-1;text-align:center;padding:20px 8px;color:rgba(255,255,255,0.3);font-size:12px;line-height:1.6">No candle patterns detected<br><span style="font-size:10px;opacity:0.7">on current stock\'s recent 30 candles</span></div>';
        return;
    }

    grid.innerHTML = detectedPatterns.map(p => {
        const typeClass = p.type || 'neutral';
        const actionColor = p.type === 'bullish' ? '#00ff88' : p.type === 'bearish' ? '#ff4757' : '#ffc400';
        return '<div class="cs-card detected" data-pattern="' + p.name + '">' +
            buildCandleVisual(p.candle_visual) +
            '<div class="cs-card-name">' + p.emoji + ' ' + p.name + '</div>' +
            '<div class="cs-card-type ' + typeClass + '">' + typeClass.charAt(0).toUpperCase() + typeClass.slice(1) + ' — ' + (p.confidence || p.reliability) + '% confidence</div>' +
            '<div class="cs-card-desc">' + p.meaning + '</div>' +
            '<div style="font-size:10px;font-weight:600;margin-top:4px;color:' + actionColor + '">' + p.action + '</div>' +
            (p.detected_at ? '<div style="font-size:8px;color:rgba(255,255,255,0.25);margin-top:2px">📍 ' + p.detected_at + '</div>' : '') +
        '</div>';
    }).join('');

    // Auto-expand the section when patterns found
    if (grid.style.display === 'none') {
        toggleCandleStudy();
    }
}

function highlightDetectedPatterns(patterns) {
    // Handled by renderCandleStudy — no-op for backward compat
}

function toggleCandleStudy() {
    const grid = document.getElementById('candleStudyGrid');
    const btn = document.getElementById('csToggleBtn');
    if (!grid) return;
    const hidden = grid.style.display === 'none';
    grid.style.display = hidden ? '' : 'none';
    if (btn) btn.textContent = hidden ? '\u25B2' : '\u25BC';
}

// ── SUPPORT & RESISTANCE ──────────────────────────────────────


function updateSupportResistance(sr, currentPrice) {
    const sec = document.getElementById('srContent');
    if (!sec) return;

    if (!sr || (!sr.resistance?.length && !sr.support?.length)) {
        sec.innerHTML = '<div class="placeholder-text">Could not calculate S/R levels</div>';
        return;
    }

    const pctAway = (price) => {
        const p = ((price - currentPrice) / currentPrice * 100);
        return (p >= 0 ? '+' : '') + p.toFixed(1) + '%';
    };

    const resRows = (sr.resistance || []).map(r => {
        const price = r.price || r;
        const dist  = pctAway(price);
        return `
            <div class="sr-row">
                <span class="sr-label" style="color:#ff4757">🔴 Resistance</span>
                <span class="sr-price">₹${fmt(price)}</span>
                <span class="sr-dist">${dist}</span>
            </div>
        `;
    }).join('');

    const supRows = (sr.support || []).map(s => {
        const price = s.price || s;
        const dist  = pctAway(price);
        return `
            <div class="sr-row">
                <span class="sr-label" style="color:#00ff88">🟢 Support</span>
                <span class="sr-price">₹${fmt(price)}</span>
                <span class="sr-dist">${dist}</span>
            </div>
        `;
    }).join('');

    sec.innerHTML = `
        ${resRows}
        <div class="sr-current-marker">── Current ₹${fmt(currentPrice)} ──</div>
        ${supRows}
    `;
}

// ─────────────────────────────────────────────────────────────
// TIMEFRAME SWITCH
// ─────────────────────────────────────────────────────────────

function switchTimeframe(tf) {
    if (!currentSymbol) return;
    const prevTf   = currentTimeframe;
    currentTimeframe = tf;

    // Update tab UI
    document.querySelectorAll('.tf-tab').forEach(btn => {
        btn.classList.toggle('active', btn.textContent.trim().toLowerCase() === tfLabel(tf));
    });

    showLoading(`Loading ${tfLabel(tf)} candles...`);

    fetch(`/api/candles/?symbol=${currentSymbol}&timeframe=${tf}`)
        .then(r => r.json())
        .then(data => {
            hideLoading();
            if (data.error || !data.timestamps || data.timestamps.length === 0) {
                // Revert tab to previous timeframe
                currentTimeframe = prevTf;
                document.querySelectorAll('.tf-tab').forEach(btn => {
                    btn.classList.toggle('active', btn.textContent.trim().toLowerCase() === tfLabel(prevTf));
                });
                showToast(data.error || `No ${tfLabel(tf)} data available`, 'red');
                return;
            }
            if (currentData) {
                currentData.candle_data = data;
                updateChart(data, currentData.indicators, currentData.support_resistance);
            }
        })
        .catch(() => {
            hideLoading();
            currentTimeframe = prevTf;
            document.querySelectorAll('.tf-tab').forEach(btn => {
                btn.classList.toggle('active', btn.textContent.trim().toLowerCase() === tfLabel(prevTf));
            });
            showToast('Failed to load candles', 'red');
        });
}

function tfLabel(tf) {
    const map = { '1': '1m', '5': '5m', '15': '15m', '60': '1h', 'D': '1D', '1wk': '1W' };
    return (map[tf] || tf).toLowerCase();
}

// ─────────────────────────────────────────────────────────────
// TRADE TYPE SWITCH
// ─────────────────────────────────────────────────────────────

function switchTradeType(type, reanalyze = true) {
    stopLiveTicker();
    currentTradeType = type;

    document.getElementById('btnIntraday')?.classList.toggle('active', type === 'intraday');
    document.getElementById('btnSwing')?.classList.toggle('active',    type === 'swing');
    document.getElementById('btnLongterm')?.classList.toggle('active', type === 'longterm');

    // Auto-set timeframe
    if (type === 'intraday') {
        currentTimeframe = '15';
        switchTimeframe('15');
    } else if (type === 'swing') {
        currentTimeframe = '60';
    } else {
        currentTimeframe = 'D';
    }

    if (reanalyze && currentSymbol) {
        analyzeStock(currentSymbol, true);
    }
}

// ─────────────────────────────────────────────────────────────
// WATCHLIST
// ─────────────────────────────────────────────────────────────

function loadWatchlistSignals() {
    fetch('/api/watchlist/signals/')
        .then(r => r.json())
        .then(data => {
            if (!data.watchlist) return;
            data.watchlist.forEach(item => {
                const priceEl  = document.getElementById(`wl-price-${item.symbol}`);
                const chgEl    = document.getElementById(`wl-chg-${item.symbol}`);
                const sigEl    = document.getElementById(`wl-sig-${item.symbol}`);

                const q = item.quote || {};
                const cur  = q.current    || 0;
                const prev = q.prev_close || cur;

                // Recalculate change_pct if it's zero but prices differ
                let rawPct = q.change_pct || 0;
                if (rawPct === 0 && prev > 0 && Math.abs(cur - prev) > 0.005) {
                    rawPct = round2(((cur - prev) / prev) * 100);
                }

                if (priceEl) priceEl.textContent = `₹${fmt(cur)}`;
                if (chgEl) {
                    chgEl.textContent = (rawPct >= 0 ? '+' : '') + rawPct.toFixed(2) + '%';
                    chgEl.className   = 'wi-chg ' + (rawPct >= 0 ? 'up' : 'down');
                }
                if (sigEl) {
                    const s   = item.signal || 'WAIT';
                    const cls = s.includes('BUY') ? 'sig-buy' : s.includes('SELL') ? 'sig-sell' : 'sig-wait';
                    sigEl.textContent = s.replace('STRONG ', 'S.');
                    sigEl.className   = `wi-signal ${cls}`;
                }
            });

            // Update count badge
            const count = data.watchlist.length;
            setEl('watchlistCount', count);
            setEl('wlCount', count);
        })
        .catch(() => {});
}

function toggleWatchlist() {
    if (!currentSymbol) { showToast('Search a stock first', 'red'); return; }

    const btn = document.getElementById('watchlistToggleBtn');
    const inWl = btn?.classList.contains('added');

    fetch('/api/watchlist/', {
        method:  'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken':  getCsrf(),
        },
        body: JSON.stringify({ symbol: currentSymbol, name: currentSymbol }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'added') {
            updateWatchlistButton(true);
            showToast(`★ ${currentSymbol} added to watchlist`, 'green');
            // Add to sidebar
            addToWatchlistSidebar(currentSymbol);
        } else {
            updateWatchlistButton(false);
            showToast(`${currentSymbol} removed from watchlist`, 'red');
            removeFromWatchlistSidebar(currentSymbol);
        }
        loadWatchlistSignals();
    })
    .catch(() => showToast('Watchlist error', 'red'));
}

function removeFromWatchlist(symbol) {
    fetch('/api/watchlist/', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
        body:    JSON.stringify({ symbol }),
    })
    .then(r => r.json())
    .then(() => {
        removeFromWatchlistSidebar(symbol);
        if (symbol === currentSymbol) updateWatchlistButton(false);
        showToast(`${symbol} removed`, 'red');
    });
}

function updateWatchlistButton(inWatchlist) {
    const btn = document.getElementById('watchlistToggleBtn');
    if (!btn) return;
    if (inWatchlist) {
        btn.textContent = '★ Remove Watchlist';
        btn.classList.add('added');
    } else {
        btn.textContent = '★ Add to Watchlist';
        btn.classList.remove('added');
    }
}

function addToWatchlistSidebar(symbol) {
    const container = document.getElementById('watchlistContainer');
    const empty     = document.getElementById('wlEmpty');
    if (empty) empty.remove();

    // Check if already exists
    if (container?.querySelector(`[data-symbol="${symbol}"]`)) return;

    const div = document.createElement('div');
    div.className = 'watchlist-item';
    div.dataset.symbol = symbol;
    div.onclick = () => analyzeStock(symbol);
    div.innerHTML = `
        <span class="wi-sym">${symbol}</span>
        <span class="wi-price" id="wl-price-${symbol}">—</span>
        <span class="wi-chg" id="wl-chg-${symbol}">—</span>
        <span class="wi-signal" id="wl-sig-${symbol}">—</span>
        <button class="wi-remove" onclick="event.stopPropagation();removeFromWatchlist('${symbol}')" title="Remove">✕</button>
    `;
    container?.appendChild(div);
}

function removeFromWatchlistSidebar(symbol) {
    const el = document.querySelector(`.watchlist-item[data-symbol="${symbol}"]`);
    if (el) el.remove();

    // Show empty state if none left
    const container = document.getElementById('watchlistContainer');
    if (container && !container.querySelector('.watchlist-item')) {
        const div = document.createElement('div');
        div.className = 'watchlist-empty';
        div.id = 'wlEmpty';
        div.textContent = 'No stocks saved. Search a stock and add to watchlist.';
        container.appendChild(div);
    }
}

// ─────────────────────────────────────────────────────────────
// STOCK NEWS
// ─────────────────────────────────────────────────────────────

function fetchStockNews(symbol) {
    const sec = document.getElementById('newsContent');
    if (sec) sec.innerHTML = '<div class="placeholder-text">Loading news...</div>';

    fetch(`/api/stock-news/?symbol=${symbol}`)
        .then(r => r.json())
        .then(data => {
            if (!sec) return;
            const news = data.news || [];
            if (!news.length) {
                sec.innerHTML = `<div class="placeholder-text">No news found for ${symbol}</div>`;
                return;
            }
            sec.innerHTML = news.map(n => `
                <a class="news-mini-item" href="${n.link}" target="_blank" rel="noopener">
                    <div class="nmi-meta">
                        <span class="nmi-source">${n.source}</span>
                        <span class="nmi-time">${n.time || ''}</span>
                    </div>
                    <div class="nmi-title">${n.title}</div>
                </a>
            `).join('');
        })
        .catch(() => {
            if (sec) sec.innerHTML = '<div class="placeholder-text">News unavailable</div>';
        });
}

// ─────────────────────────────────────────────────────────────
// MARKET STRIP
// ─────────────────────────────────────────────────────────────
function updateMarketStrip() {
    fetch('/api/market/')
        .then(r => r.json())
        .then(data => {
            // Nifty
            if (data.nifty && data.nifty.price > 0) {
                const n = data.nifty;
                const nUp = n.change >= 0;
                document.getElementById('stripNifty').textContent = fmtINR(n.price);
                const nChg = document.getElementById('stripNiftyChg');
                if (nChg) {
                    nChg.textContent = `${nUp ? '+' : ''}${fmt(n.change)} (${nUp ? '+' : ''}${n.change_pct}%)`;
                    nChg.className = `strip-chg ${nUp ? 'up' : 'down'}`;
                }
            } else {
                document.getElementById('stripNifty').textContent = 'Closed';
            }

            // Sensex
            if (data.sensex && data.sensex.price > 0) {
                const s = data.sensex;
                const sUp = s.change >= 0;
                document.getElementById('stripSensex').textContent = fmtINR(s.price);
                const sChg = document.getElementById('stripSensexChg');
                if (sChg) {
                    sChg.textContent = `${sUp ? '+' : ''}${fmt(s.change)} (${sUp ? '+' : ''}${s.change_pct}%)`;
                    sChg.className = `strip-chg ${sUp ? 'up' : 'down'}`;
                }
            } else {
                document.getElementById('stripSensex').textContent = 'Closed';
            }

            // VIX
            if (data.vix && data.vix.price > 0) {
                document.getElementById('stripVix').textContent = fmt(data.vix.price);
            }

            // Market open/closed — IST 9:15 AM to 3:30 PM Mon-Fri
            updateMarketOpenStatus();
        })
        .catch(err => {
            console.error('Market strip error:', err);
            updateMarketOpenStatus();
            ['stripNifty','stripSensex','stripVix'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.textContent = '—';
            });
        });
}

function updateStripItem(priceId, chgId, data) {
    if (!data) return;
    const prEl = document.getElementById(priceId);
    const chEl = document.getElementById(chgId);
    if (prEl) prEl.textContent = fmtINR(data.price);
    if (chEl) {
        const up   = data.change >= 0;
        const sign = up ? '+' : '';
        chEl.textContent = `${sign}${fmt(data.change)} (${sign}${data.change_pct}%)`;
        chEl.className   = `strip-chg ${up ? 'up' : 'down'}`;
    }
}

// ─────────────────────────────────────────────────────────────
// IST TIME HELPER (reliable across all browsers/OS timezones)
// ─────────────────────────────────────────────────────────────

function getISTComponents() {
    // Use Intl.DateTimeFormat to reliably get IST time parts
    // This avoids the bug where new Date(toLocaleString()) re-parses incorrectly
    const fmt = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Kolkata',
        hour: 'numeric', minute: 'numeric', second: 'numeric',
        weekday: 'short', hour12: false,
    });
    const parts = {};
    fmt.formatToParts(new Date()).forEach(p => { parts[p.type] = p.value; });
    const hour   = parseInt(parts.hour, 10);
    const minute = parseInt(parts.minute, 10);
    const second = parseInt(parts.second, 10);
    const dayMap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
    const day    = dayMap[parts.weekday] ?? new Date().getDay();
    return { hour, minute, second, day };
}

function isMarketOpen() {
    const { hour, minute, day } = getISTComponents();
    const mins = hour * 60 + minute;
    // NSE trading hours: Mon-Fri 9:15 AM - 3:30 PM IST
    return day >= 1 && day <= 5 && mins >= 555 && mins < 930;
}

function updateMarketOpenStatus() {
    const { hour, minute, day } = getISTComponents();
    const mins = hour * 60 + minute;
    const dayNames = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
    const dayName = dayNames[day] || '';
    const isWeekday = day >= 1 && day <= 5;
    const isOpen = isWeekday && mins >= 555 && mins < 930;

    let statusText, statusColor;
    if (isOpen) {
        statusText = '🟢 Market OPEN';
        statusColor = '#00ff88';
    } else if (!isWeekday) {
        statusText = `🔴 CLOSED · ${dayName}`;
        statusColor = '#ff4757';
    } else if (mins < 555) {
        statusText = '🟡 Pre-Market · Opens 9:15';
        statusColor = '#ffc400';
    } else {
        statusText = '🔴 CLOSED · After Hours';
        statusColor = '#ff4757';
    }

    const dot = document.getElementById('marketDot');
    const lbl = document.getElementById('marketStatus');
    if (dot) dot.className = `status-dot ${isOpen ? 'open' : 'closed'}`;
    if (lbl) {
        lbl.textContent = statusText;
        lbl.style.color = statusColor;
    }
}

// ─────────────────────────────────────────────────────────────
// CLOCK
// ─────────────────────────────────────────────────────────────

function startClock() {
    function tick() {
        const { hour, minute, second } = getISTComponents();
        const ap  = hour >= 12 ? 'PM' : 'AM';
        const h12 = hour % 12 || 12;
        const mm  = String(minute).padStart(2, '0');
        const ss  = String(second).padStart(2, '0');
        setEl('stripClock', `${h12}:${mm}:${ss} ${ap} IST`);
    }
    tick();
    clockTimer = setInterval(tick, 1000);
}

// ─────────────────────────────────────────────────────────────
// PRICE ALERTS
// ─────────────────────────────────────────────────────────────

function setAlert() {
    if (!currentSymbol) { showToast('Search a stock first', 'red'); return; }
    const priceInp = document.getElementById('alertPrice');
    const price    = parseFloat(priceInp?.value);
    if (!price || isNaN(price)) { showToast('Enter a valid price', 'red'); return; }

    const alert = { symbol: currentSymbol, price, direction: price > (currentData?.quote?.current || 0) ? 'above' : 'below' };
    priceAlerts.push(alert);
    localStorage.setItem('sa_alerts', JSON.stringify(priceAlerts));
    if (priceInp) priceInp.value = '';
    renderAlerts();
    showToast(`Alert set: ${currentSymbol} ${alert.direction} ₹${fmt(price)}`, 'green');
}

function removeAlert(idx) {
    priceAlerts.splice(idx, 1);
    localStorage.setItem('sa_alerts', JSON.stringify(priceAlerts));
    renderAlerts();
}

function renderAlerts() {
    const list = document.getElementById('alertList');
    if (!list) return;
    if (!priceAlerts.length) { list.innerHTML = ''; return; }
    list.innerHTML = priceAlerts.map((a, i) => `
        <div class="alert-item">
            <span class="alert-item-text">${a.symbol} ${a.direction} ₹${fmt(a.price)}</span>
            <button class="alert-remove" onclick="removeAlert(${i})">✕</button>
        </div>
    `).join('');
}

function checkPriceAlerts() {
    if (!priceAlerts.length || !currentData?.quote) return;
    const currentPrice = currentData.quote.current;

    priceAlerts.forEach((alert, idx) => {
        if (alert.symbol !== currentSymbol) return;
        const triggered = (alert.direction === 'above' && currentPrice >= alert.price) ||
                          (alert.direction === 'below' && currentPrice <= alert.price);
        if (triggered) {
            showToast(`🔔 ALERT: ${alert.symbol} is ${alert.direction} ₹${fmt(alert.price)}!`, 'green');
            if ('Notification' in window && Notification.permission === 'granted') {
                new Notification(`StockAnalyzer Alert`, {
                    body: `${alert.symbol} is ${alert.direction} ₹${fmt(alert.price)}`,
                    icon: '/static/icon.png',
                });
            }
            priceAlerts.splice(idx, 1);
            localStorage.setItem('sa_alerts', JSON.stringify(priceAlerts));
            renderAlerts();
        }
    });
}

// Request notification permission
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

// ─────────────────────────────────────────────────────────────
// SIGNAL HISTORY (localStorage)
// ─────────────────────────────────────────────────────────────

function saveToHistory(symbol, signal) {
    // Remove duplicate
    signalHistory = signalHistory.filter(h => h.symbol !== symbol);
    signalHistory.unshift({ symbol, signal, time: new Date().toISOString() });
    signalHistory = signalHistory.slice(0, 10);
    localStorage.setItem('sa_history', JSON.stringify(signalHistory));
    renderSignalHistory();
}

function renderSignalHistory() {
    const container = document.getElementById('signalHistoryContainer');
    if (!container || !signalHistory.length) return;

    // Only prepend localStorage history to existing Django-rendered items
    const existing = container.querySelectorAll('.sh-item');
    if (signalHistory.length === 0) return;

    // Clear and re-render from localStorage
    container.innerHTML = signalHistory.map(h => {
        const cls = (h.signal || '').includes('BUY') ? 'sig-buy' : (h.signal || '').includes('SELL') ? 'sig-sell' : 'sig-wait';
        const ago = timeAgo(h.time);
        return `
            <div class="sh-item" onclick="analyzeStock('${h.symbol}')">
                <span class="sh-sym">${h.symbol}</span>
                <span class="wi-signal ${cls}">${h.signal || '—'}</span>
                <span class="sh-time">${ago}</span>
            </div>
        `;
    }).join('');
}

// ─────────────────────────────────────────────────────────────
// UPSTOX TOKEN MODAL
// ─────────────────────────────────────────────────────────────

function showUpstoxModal() {
    document.getElementById('upstoxModal')?.classList.remove('hidden');
}

function hideUpstoxModal() {
    document.getElementById('upstoxModal')?.classList.add('hidden');
}

function saveUpstoxToken() {
    const token = document.getElementById('upstoxTokenInput')?.value.trim();
    if (!token) { showToast('Paste a token first', 'red'); return; }

    fetch('/api/upstox-token/', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
        body:    JSON.stringify({ token }),
    })
    .then(r => r.json())
    .then(data => {
        hideUpstoxModal();
        showToast('✓ Upstox token saved — real-time data active', 'green');
        if (currentSymbol && currentTradeType === 'intraday') {
            analyzeStock(currentSymbol, true);
        }
    })
    .catch(() => {
        // Even if endpoint fails, save locally
        localStorage.setItem('sa_upstox_token', token);
        hideUpstoxModal();
        showToast('Token saved locally', 'green');
    });
}

// ─────────────────────────────────────────────────────────────
// OPEN IN UPSTOX
// ─────────────────────────────────────────────────────────────

function openInUpstox() {
    if (!currentSymbol) { showToast('Search a stock first', 'red'); return; }
    const url = `https://pro.upstox.com/stocks/${currentSymbol}/NSE`;
    window.open(url, '_blank');
}

// ─────────────────────────────────────────────────────────────
// LOADING STATES
// ─────────────────────────────────────────────────────────────

function showLoading(msg) {
    const el = document.getElementById('chartLoading');
    const tx = document.getElementById('loadingText');
    if (el) el.classList.remove('hidden');
    if (tx) tx.textContent = msg || 'Loading...';
    document.getElementById('chartWelcome')?.classList.add('hidden');
}

function hideLoading() {
    document.getElementById('chartLoading')?.classList.add('hidden');
}

function hideWelcome() {
    document.getElementById('chartWelcome')?.classList.add('hidden');
}

// ─────────────────────────────────────────────────────────────
// TOAST
// ─────────────────────────────────────────────────────────────

let toastTimer = null;
function showToast(msg, type = '') {
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.className   = `toast ${type} show`;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 3000);
}

// ─────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────

function setEl(id, text, color) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (color) el.style.color = color;
    else el.style.color = '';
}

function setBarWidth(id, pct) {
    const el = document.getElementById(id);
    if (el) el.style.width = `${Math.min(100, Math.max(0, pct))}%`;
}

function fmt(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtINR(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function round2(n) {
    return Math.round(n * 100) / 100;
}

function getCsrf() {
    const el = document.cookie.split(';').find(c => c.trim().startsWith('csrftoken='));
    return el ? el.split('=')[1] : '';
}

function timeAgo(isoString) {
    if (!isoString) return '';
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1)  return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24)  return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
}

// ─────────────────────────────────────────────────────────────
// WATCHLIST DROPDOWN TOGGLE (nav badge)
// ─────────────────────────────────────────────────────────────

function toggleWatchlistDropdown() {
    let dd = document.getElementById('wlDropdown');
    if (!dd) {
        dd = document.createElement('div');
        dd.id = 'wlDropdown';
        dd.style.cssText = `
            position:absolute; top:calc(56px + 4px); right:0; width:280px;
            background:#0d1224; border:1px solid rgba(255,255,255,0.1);
            border-radius:14px; box-shadow:0 20px 60px rgba(0,0,0,0.6);
            z-index:300; overflow:hidden; animation:dropIn 0.15s ease;
        `;
        dd.innerHTML = `
            <div style="padding:12px 14px;border-bottom:1px solid rgba(255,255,255,0.07);font-size:11px;font-weight:700;color:rgba(255,255,255,0.4);text-transform:uppercase;letter-spacing:0.08em;display:flex;align-items:center;justify-content:space-between;">
                My Watchlist
                <button onclick="document.getElementById('wlDropdown').remove()" style="background:none;border:none;color:rgba(255,255,255,0.3);cursor:pointer;font-size:18px;line-height:1;">×</button>
            </div>
            <div id="wlDdList" style="max-height:320px;overflow-y:auto;">
                <div style="padding:20px;text-align:center;font-size:12px;color:rgba(255,255,255,0.3)">Loading...</div>
            </div>
            <div style="padding:10px 14px;border-top:1px solid rgba(255,255,255,0.07);">
                <a href="/dashboard/" style="display:block;text-align:center;font-size:11px;color:#00ff88;text-decoration:none;font-weight:600;padding:6px;border-radius:6px;background:rgba(0,255,136,0.06);">Open Dashboard →</a>
            </div>`;

        const btn = document.getElementById('watchlistNavBtn');
        btn.parentElement.style.position = 'relative';
        btn.parentElement.appendChild(dd);

        // Add keyframe if not already present
        if (!document.getElementById('wlDropAnim')) {
            const s = document.createElement('style');
            s.id = 'wlDropAnim';
            s.textContent = '@keyframes dropIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}';
            document.head.appendChild(s);
        }

        // Close on outside click
        setTimeout(() => {
            document.addEventListener('click', function handler(e) {
                const d = document.getElementById('wlDropdown');
                if (d && !d.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
                    d.remove();
                    document.removeEventListener('click', handler);
                }
            });
        }, 10);

        // Load watchlist data
        fetch('/api/watchlist/signals/')
            .then(r => r.json())
            .then(data => {
                const list = document.getElementById('wlDdList');
                if (!list) return;
                const wl = data.watchlist || [];
                if (!wl.length) {
                    list.innerHTML = '<div style="padding:20px;text-align:center;font-size:12px;color:rgba(255,255,255,0.3)">No stocks in watchlist</div>';
                    return;
                }
                list.innerHTML = wl.map(item => {
                    const s   = item.signal || 'WAIT';
                    const cls = s.includes('BUY') ? '#00ff88' : s.includes('SELL') ? '#ff4757' : '#ffc400';
                    const bg  = s.includes('BUY') ? 'rgba(0,255,136,0.12)' : s.includes('SELL') ? 'rgba(255,71,87,0.12)' : 'rgba(255,196,0,0.12)';
                    const pct = item.quote?.change_pct ?? 0;
                    const cur = item.quote?.current ?? 0;
                    const up  = pct >= 0;
                    return `<div onclick="wlDdGo('${item.symbol}')" style="display:flex;align-items:center;padding:10px 14px;gap:10px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,0.03);transition:background 0.15s;" onmouseover="this.style.background='rgba(255,255,255,0.04)'" onmouseout="this.style.background=''">
                        <span style="font-family:'Fira Code',monospace;font-size:12px;font-weight:700;flex:1">${item.symbol}</span>
                        <span style="font-family:'Fira Code',monospace;font-size:11px;color:${up?'#00ff88':'#ff4757'};text-align:right">
                            ₹${cur.toLocaleString('en-IN',{maximumFractionDigits:2})}<br>
                            <small>${up?'+':''}${pct}%</small>
                        </span>
                        <span style="font-size:9px;font-weight:700;padding:2px 6px;border-radius:4px;background:${bg};color:${cls}">${s.replace('STRONG ','S.')}</span>
                    </div>`;
                }).join('');
            })
            .catch(() => {
                const list = document.getElementById('wlDdList');
                if (list) list.innerHTML = '<div style="padding:20px;text-align:center;font-size:12px;color:rgba(255,255,255,0.3)">Could not load</div>';
            });

    } else {
        dd.remove(); // toggle off
    }
}

function wlDdGo(symbol) {
    document.getElementById('wlDropdown')?.remove();
    if (typeof analyzeStock === 'function') analyzeStock(symbol);
    else window.location.href = '/dashboard/?stock=' + symbol;
}

// ─────────────────────────────────────────────────────────────
// LIVE PRICE — UPSTOX REAL-TIME (1-second polling)
// Polls /api/live-price/ every 1s — uses Upstox LTP REST API
// which is the same data source as the Upstox app.
// ─────────────────────────────────────────────────────────────

let _liveWsSymbol = null; // currently tracked symbol

function startLiveTicker(symbol) {
    stopLiveTicker();
    _liveWsSymbol = symbol;

    function poll() {
        if (_liveWsSymbol !== symbol) return; // stopped or symbol changed
        fetch(`/api/live-price/?symbol=${symbol}`)
            .then(r => r.json())
            .then(d => {
                if (d.error || !d.price) return;
                _applyLivePrice(symbol, d.price, d.change, d.change_pct);
            })
            .catch(() => {});
    }

    poll(); // fire immediately
    liveTickTimer = setInterval(poll, 3000); // every 3 seconds
    _showLiveIndicator(true);
}

function _applyLivePrice(symbol, price, change, change_pct) {
    const up   = change >= 0;
    const sign = up ? '+' : '';

    // ── 1. Stock info bar ──────────────────────────────────────
    const prEl  = document.getElementById('sibPrice');
    const chgEl = document.getElementById('sibChg');
    if (prEl) {
        const prev = prEl.textContent;
        prEl.textContent = `₹${fmt(price)}`;
        prEl.className   = 'sib-price ' + (up ? 'up' : 'down');

        // Flash green/red depending on direction vs previous displayed value
        if (prev !== prEl.textContent) {
            const flashColor = up ? 'rgba(0,255,136,0.25)' : 'rgba(255,71,87,0.25)';
            prEl.style.background  = flashColor;
            prEl.style.borderRadius = '4px';
            prEl.style.transition  = 'background 0.4s';
            setTimeout(() => { prEl.style.background = 'transparent'; }, 400);
        }
    }
    if (chgEl) {
        chgEl.textContent = `${sign}${fmt(change)} (${sign}${change_pct}%)`;
        chgEl.className   = 'sib-chg ' + (up ? 'up' : 'down');
    }

    // ── 2. Live line on chart ──────────────────────────────────
    if (candleSeries) {
        if (livePriceLine) {
            try { candleSeries.removePriceLine(livePriceLine); } catch(e) {}
        }
        livePriceLine = candleSeries.createPriceLine({
            price:            price,
            color:            up ? '#00ff88' : '#ff4757',
            lineWidth:        2,
            lineStyle:        LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title:            `● LIVE ${fmt(price)}`,
        });
    }

    // ── 3. Watchlist sidebar ────────────────────────────────────
    const wlPr = document.getElementById(`wl-price-${symbol}`);
    const wlCh = document.getElementById(`wl-chg-${symbol}`);
    if (wlPr) wlPr.textContent = `₹${fmt(price)}`;
    if (wlCh) {
        wlCh.textContent = `${sign}${change_pct}%`;
        wlCh.className   = 'wi-chg ' + (up ? 'up' : 'down');
    }

    // ── 4. Update page title with live price ───────────────────
    document.title = `${up ? '▲' : '▼'} ₹${fmt(price)} ${symbol} — StockAnalyzer`;
}

function _showLiveIndicator(active) {
    // Show/hide a pulsing LIVE dot in the stock info bar
    let dot = document.getElementById('liveDot');
    if (!dot) {
        dot = document.createElement('span');
        dot.id = 'liveDot';
        dot.style.cssText = `
            display:inline-flex;align-items:center;gap:5px;
            font-size:9px;font-weight:800;color:#00ff88;
            text-transform:uppercase;letter-spacing:0.08em;margin-left:8px;
        `;
        dot.innerHTML = '<span style="width:6px;height:6px;border-radius:50%;background:#00ff88;animation:pulse 1s infinite;display:inline-block"></span>LIVE';
        const sib = document.getElementById('sibSym');
        if (sib) sib.after(dot);
    }
    dot.style.display = active ? 'inline-flex' : 'none';
}

function stopLiveTicker() {
    _liveWsSymbol = null;
    if (liveTickTimer) { clearInterval(liveTickTimer); liveTickTimer = null; }
    if (livePriceLine && candleSeries) {
        try { candleSeries.removePriceLine(livePriceLine); } catch(e) {}
        livePriceLine = null;
    }
    const dot = document.getElementById('liveDot');
    if (dot) dot.style.display = 'none';
    document.title = 'StockAnalyzer — Dashboard';
}

// ─────────────────────────────────────────────────────────────
// PLAIN ENGLISH DESCRIPTION
// ─────────────────────────────────────────────────────────────

function toggleAnalysisDesc() {
    const body = document.getElementById('analysisDescBody');
    const btn  = document.getElementById('adsToggleBtn');
    if (!body) return;
    const hidden = body.style.display === 'none';
    body.style.display = hidden ? '' : 'none';
    if (btn) btn.textContent = hidden ? '▲' : '▼';
}

function updateAnalysisDescription(data, symbol) {
    const section = document.getElementById('analysisDescSection');
    const body    = document.getElementById('analysisDescBody');
    if (!section || !body || !data) return;

    const ind    = data.indicators;
    const sig    = data.signal;
    const sr     = data.support_resistance;
    const pats   = data.patterns || [];
    const scores = data.trade_scores;
    const quote  = data.quote;
    if (!ind || !sig) return;

    section.style.display = '';

    const price   = ind.current_price;
    const rsi     = ind.rsi;
    const macd    = ind.macd_diff;
    const ema9    = ind.ema9;
    const ema21   = ind.ema21;
    const sma50   = ind.sma50;
    const sma200  = ind.sma200;
    const bbU     = ind.bb_upper;
    const bbL     = ind.bb_lower;
    const atr     = ind.atr;
    const vs      = ind.volume_spike;
    const signal  = sig.signal;
    const bullPct = sig.bull_pct;
    const bearPct = 100 - bullPct;
    const best    = scores?.best_label || 'Swing Trade';

    // ── BLOCK 1: CHART READING ──────────────────────────────
    const trendDir   = ema9 > ema21 ? 'UPTREND' : 'DOWNTREND';
    const trendCls   = ema9 > ema21 ? 'ads-bull' : 'ads-bear';
    const priceVsE21 = price > ema21
        ? `Price ₹${fmt(price)} is <span class="ads-bull">ABOVE EMA21 ₹${fmt(ema21)}</span> — buyers are in control of the short-term trend.`
        : `Price ₹${fmt(price)} is <span class="ads-bear">BELOW EMA21 ₹${fmt(ema21)}</span> — sellers are in control of the short-term trend.`;
    const priceVsS50 = price > sma50
        ? `Price is <span class="ads-bull">above SMA50 ₹${fmt(sma50)}</span> — medium-term trend is UP.`
        : `Price is <span class="ads-bear">below SMA50 ₹${fmt(sma50)}</span> — medium-term trend is DOWN.`;
    const priceVsS200 = sma200
        ? (price > sma200
            ? `Price is <span class="ads-bull">above SMA200 ₹${fmt(sma200)}</span> — long-term trend is BULLISH.`
            : `Price is <span class="ads-bear">below SMA200 ₹${fmt(sma200)}</span> — long-term trend is BEARISH.`)
        : '';

    // ── BLOCK 2: INDICATORS ────────────────────────────────
    const rsiText = rsi < 30
        ? `<span class="ads-bull">RSI ${rsi} — OVERSOLD zone</span>. This means the stock has been sold too much. Buyers often step in here. This is a potential BUY zone.`
        : rsi > 70
        ? `<span class="ads-bear">RSI ${rsi} — OVERBOUGHT zone</span>. This means the stock has been bought too much. Sellers often push back here. Be careful buying now.`
        : `<span class="ads-neut">RSI ${rsi} — NEUTRAL zone</span>. Neither buyers nor sellers have extreme control. Watch other indicators for direction.`;

    const macdText = macd > 0
        ? `<span class="ads-bull">MACD is positive (+${fmt(macd)})</span> — buying momentum is building. Bulls are pushing the price up.`
        : `<span class="ads-bear">MACD is negative (${fmt(macd)})</span> — selling momentum is present. Bears are pressing the price down.`;

    const emaText = ema9 > ema21
        ? `<span class="ads-bull">EMA Golden Cross</span> — the fast EMA9 (₹${fmt(ema9)}) is above the slow EMA21 (₹${fmt(ema21)}). This is a classic BUY signal. Short-term momentum is turning bullish.`
        : `<span class="ads-bear">EMA Death Cross</span> — the fast EMA9 (₹${fmt(ema9)}) is below the slow EMA21 (₹${fmt(ema21)}). This is a SELL signal. Short-term momentum is bearish.`;

    const bbText = (bbL && price <= bbL)
        ? `<span class="ads-bull">Price is near the LOWER Bollinger Band (₹${fmt(bbL)})</span> — this is a support zone. Stock may bounce up from here.`
        : (bbU && price >= bbU)
        ? `<span class="ads-bear">Price is near the UPPER Bollinger Band (₹${fmt(bbU)})</span> — this is a resistance zone. Stock may pull back from here.`
        : `Price is between Bollinger Bands (₹${fmt(bbL || '—')} — ₹${fmt(bbU || '—')}) — <span class="ads-neut">mid-band zone</span>. No extreme condition right now.`;

    const volText = vs
        ? `<span class="ads-bull">⚡ Volume Spike detected</span> — more shares than usual are being traded. This confirms the current move has strong conviction behind it.`
        : `Volume is normal — no unusual trading activity. The current move may lack strong conviction.`;

    const atrPct = atr && price ? ((atr / price) * 100).toFixed(2) : '0';
    const volText2 = parseFloat(atrPct) > 3
        ? `ATR (volatility) is <span class="ads-bear">HIGH at ${atrPct}%</span> — this stock is moving a lot. Risk is higher. Use wider stop loss.`
        : parseFloat(atrPct) > 1.5
        ? `ATR (volatility) is <span class="ads-neut">MEDIUM at ${atrPct}%</span> — normal movement. Standard stop loss is fine.`
        : `ATR (volatility) is <span class="ads-bull">LOW at ${atrPct}%</span> — stock is moving slowly. Tight stop loss possible.`;

    // ── BLOCK 3: PATTERNS ──────────────────────────────────
    let patternHTML = '';
    if (pats.length === 0) {
        patternHTML = `<div class="ads-line">No significant candle patterns detected on recent candles. Wait for a clear pattern before entering.</div>`;
    } else {
        patternHTML = pats.map(p => {
            const cls   = p.type === 'bullish' ? 'ads-bull' : p.type === 'bearish' ? 'ads-bear' : 'ads-neut';
            const arrow = p.type === 'bullish' ? '🟢' : p.type === 'bearish' ? '🔴' : '🟡';
            return `<div class="ads-point">
                <span class="ads-dot">${arrow}</span>
                <span class="ads-text">
                    <b>${p.name}</b> (${p.confidence || p.reliability}% confidence) — ${p.meaning}
                    <br><span class="${cls}">${p.action}</span>
                    ${p.detected_at ? `<br><span style="font-size:10px;color:rgba(255,255,255,0.3)">📍 ${p.detected_at}</span>` : ''}
                </span>
            </div>`;
        }).join('');
    }

    // ── BLOCK 4: SUPPORT & RESISTANCE ──────────────────────
    let srHTML = '';
    const res = sr?.resistance || [];
    const sup = sr?.support   || [];
    if (res.length === 0 && sup.length === 0) {
        srHTML = `<div class="ads-line">Could not calculate support and resistance levels for this stock.</div>`;
    } else {
        if (res.length > 0) {
            const nearest = res[0].price || res[0];
            const pctAway = (((nearest - price) / price) * 100).toFixed(1);
            const next    = res[1] ? (res[1].price || res[1]) : null;
            srHTML += `<div class="ads-point">
                <span class="ads-dot">🔴</span>
                <span class="ads-text">
                    <b>Nearest Resistance: ₹${fmt(nearest)}</b> (+${pctAway}% away)<br>
                    This is a price level where sellers have previously stopped the stock from going higher.
                    ${next ? `If price breaks above this level with volume, it could move to ₹${fmt(next)}.` : ''}
                </span>
            </div>`;
        }
        if (sup.length > 0) {
            const nearest = sup[0].price || sup[0];
            const pctAway = (((price - nearest) / price) * 100).toFixed(1);
            srHTML += `<div class="ads-point">
                <span class="ads-dot">🟢</span>
                <span class="ads-text">
                    <b>Nearest Support: ₹${fmt(nearest)}</b> (-${pctAway}% away)<br>
                    This is a price level where buyers have previously defended the stock from falling further.
                    A good stop loss zone is just below ₹${fmt(nearest)}.
                </span>
            </div>`;
        }
    }

    // ── BLOCK 5: VERDICT ───────────────────────────────────
    const sigCls   = signal.includes('BUY') ? 'ads-bull' : signal.includes('SELL') ? 'ads-bear' : 'ads-neut';
    const sigEmoji = signal.includes('BUY') ? '🟢' : signal.includes('SELL') ? '🔴' : '🟡';
    const verdictLine = signal === 'STRONG BUY'
        ? `Most indicators are strongly bullish. This is a high-confidence buy opportunity.`
        : signal === 'BUY'
        ? `More indicators are bullish than bearish. A moderate buy opportunity exists.`
        : signal === 'STRONG SELL'
        ? `Most indicators are strongly bearish. Avoid buying. Consider exiting existing positions.`
        : signal === 'SELL'
        ? `More indicators are bearish. Avoid buying. Wait for reversal signals.`
        : `Indicators are mixed — bulls at ${bullPct}%, bears at ${bearPct}%. No clear direction. Best to wait and not trade right now.`;

    const entryLine = signal.includes('BUY')
        ? `Entry: <span class="ads-white">₹${fmt(sig.entry_price)}</span> &nbsp;|&nbsp; Stop Loss: <span class="ads-bear">₹${fmt(sig.stop_loss)}</span> &nbsp;|&nbsp; Target 1: <span class="ads-bull">₹${fmt(sig.target1)}</span> &nbsp;|&nbsp; Target 2: <span class="ads-bull">₹${fmt(sig.target2)}</span>`
        : signal.includes('SELL')
        ? `Current Price: <span class="ads-white">₹${fmt(price)}</span> — Do not enter a buy position here. Wait for signal to turn BUY.`
        : `Current Price: <span class="ads-white">₹${fmt(price)}</span> — Wait for a clearer signal before trading.`;

    // ── BUILD HTML ─────────────────────────────────────────
    body.innerHTML = `

        <div class="ads-block">
            <div class="ads-block-title">📈 Chart Reading — What the chart is showing</div>
            <div class="ads-line">The chart shows <span class="${trendCls}">${symbol} is in a ${trendDir}</span> based on the EMA lines.</div>
            <div class="ads-line">${priceVsE21}</div>
            <div class="ads-line">${priceVsS50}</div>
            ${sma200 ? `<div class="ads-line">${priceVsS200}</div>` : ''}
        </div>

        <div class="ads-block">
            <div class="ads-block-title">⚡ What Indicators Are Saying</div>
            <div class="ads-point"><span class="ads-dot">📊</span><span class="ads-text">${rsiText}</span></div>
            <div class="ads-point"><span class="ads-dot">📉</span><span class="ads-text">${macdText}</span></div>
            <div class="ads-point"><span class="ads-dot">〰️</span><span class="ads-text">${emaText}</span></div>
            <div class="ads-point"><span class="ads-dot">🎯</span><span class="ads-text">${bbText}</span></div>
            <div class="ads-point"><span class="ads-dot">📦</span><span class="ads-text">${volText}</span></div>
            <div class="ads-point"><span class="ads-dot">⚠️</span><span class="ads-text">${volText2}</span></div>
        </div>

        <div class="ads-block">
            <div class="ads-block-title">🕯 Candle Patterns Detected</div>
            ${patternHTML}
        </div>

        <div class="ads-block">
            <div class="ads-block-title">📊 Support & Resistance — Key Price Levels</div>
            ${srHTML}
        </div>

        <div class="ads-block">
            <div class="ads-block-title">${sigEmoji} Final Verdict — What to Do</div>
            <div class="ads-line">Signal: <span class="${sigCls}">${signal}</span> — Bulls ${bullPct}% vs Bears ${bearPct}%</div>
            <div class="ads-line">${verdictLine}</div>
            <div class="ads-line">Best trade style right now: <span class="ads-white">${best}</span></div>
            <div class="ads-line" style="margin-top:6px">${entryLine}</div>
        </div>

    `;
}

// ─────────────────────────────────────────────────────────────
// PAPER TRADE
// ─────────────────────────────────────────────────────────────

let paperTradeCheckTimer = null;

function fillPaperTradeForm(data) {
    const sig = data.signal;
    if (!sig) return;
    document.getElementById('ptNoStock').style.display = 'none';
    document.getElementById('ptForm').style.display    = '';
    const t3 = sig.target2 ? Math.round(sig.target2 * 1.06 * 100) / 100 : '';
    document.getElementById('ptEntry').value = sig.entry_price || '';
    document.getElementById('ptSL').value    = sig.stop_loss   || '';
    document.getElementById('ptT1').value    = sig.target1     || '';
    document.getElementById('ptT2').value    = sig.target2     || '';
    document.getElementById('ptT3').value    = t3;
    const capital = parseFloat(document.getElementById('capitalInput')?.value) || 50000;
    const qty     = Math.max(1, Math.floor((capital * 0.98) / sig.entry_price));
    document.getElementById('ptQty').value = qty;
}

function openPaperTrade(direction) {
    if (!currentSymbol) { showToast('Analyze a stock first', 'red'); return; }
    const entry = parseFloat(document.getElementById('ptEntry').value);
    const sl    = parseFloat(document.getElementById('ptSL').value);
    const t1    = parseFloat(document.getElementById('ptT1').value);
    const t2    = parseFloat(document.getElementById('ptT2').value);
    const t3    = parseFloat(document.getElementById('ptT3').value) || null;
    const qty   = parseInt(document.getElementById('ptQty').value) || 1;
    if (!entry || !sl || !t1) { showToast('Fill Entry, SL and T1 first', 'red'); return; }

    // ── Confirmation dialog ────────────────────────────────────────────────
    const emoji    = direction === 'buy' ? '🟢 BUY' : '🔴 SHORT';
    const typeLabel = { intraday: 'Intraday', swing: 'Swing', longterm: 'Long Term' }[currentTradeType] || currentTradeType;
    const confirmed = window.confirm(
        `${emoji} — ${currentSymbol}\n` +
        `─────────────────────────\n` +
        `Type   : ${typeLabel}\n` +
        `Entry  : ₹${entry}\n` +
        `Stop Loss : ₹${sl}\n` +
        `Target 1  : ₹${t1}\n` +
        (t2 ? `Target 2  : ₹${t2}\n` : '') +
        (t3 ? `Target 3  : ₹${t3}\n` : '') +
        `Qty    : ${qty} shares\n` +
        `─────────────────────────\n` +
        `Click OK to open this trade.`
    );
    if (!confirmed) return;   // user clicked Cancel — do nothing
    // ──────────────────────────────────────────────────────────────────────

    fetch('/api/paper-trade/open/', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
        body:    JSON.stringify({
            symbol: currentSymbol, direction, trade_type: currentTradeType,
            entry_price: entry, stop_loss: sl,
            target1: t1, target2: t2, target3: t3, quantity: qty,
        }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'opened') {
            const emj = direction === 'buy' ? '🟢' : '🔴';
            showToast(`${emj} Paper ${direction.toUpperCase()} opened — ${currentSymbol}`, 'green');
            startPaperTradeMonitor();
        }
    })
    .catch(() => showToast('Failed to open trade', 'red'));
}

function closePaperTrade(tradeId, exitPrice, result) {
    fetch('/api/paper-trade/close/', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
        body:    JSON.stringify({ trade_id: tradeId, exit_price: exitPrice, result }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'closed') {
            const up   = data.pnl >= 0;
            const sign = up ? '+' : '';
            showToast(`${up?'🟢':'🔴'} Trade closed — P&L: ${sign}₹${Math.abs(data.pnl)} (${sign}${data.pnl_pct}%)`, up ? 'green' : 'red');
        }
    })
    .catch(() => {});
}

function startPaperTradeMonitor() {
    if (paperTradeCheckTimer) clearInterval(paperTradeCheckTimer);
    checkPaperTrades();
    paperTradeCheckTimer = setInterval(checkPaperTrades, 7000);
}

function checkPaperTrades() {
    fetch('/api/paper-trade/list/')
        .then(r => r.json())
        .then(data => {
            const trades = data.active || [];
            if (!trades.length) return;
            const istOffset = 5.5 * 60 * 60 * 1000;
            const ist       = new Date(Date.now() + (new Date().getTimezoneOffset() * 60000) + istOffset);
            const mins      = ist.getHours() * 60 + ist.getMinutes();
            const isClose   = mins >= 930; // 3:30 PM
            trades.forEach(t => {
                const cur   = t.current_price;
                const isBuy = t.direction === 'buy';
                // Intraday market close
                if (t.trade_type === 'intraday' && isClose) {
                    showToast(`⏰ Market closed — auto closing ${t.symbol} @ ₹${fmt(cur)}`, 'red');
                    closePaperTrade(t.id, cur, 'market_close');
                    return;
                }
                // SL hit
                const slHit = isBuy ? cur <= t.stop_loss : cur >= t.stop_loss;
                if (slHit && t.trade_type !== 'longterm') {
                    showToast(`🔴 SL HIT — ${t.symbol} @ ₹${fmt(cur)}`, 'red');
                    closePaperTrade(t.id, cur, 'sl');
                    return;
                }
                // T3 hit — auto close all
                if (t.target3) {
                    const t3Hit = isBuy ? cur >= t.target3 : cur <= t.target3;
                    if (t3Hit) {
                        showToast(`🎯 TARGET 3 HIT — ${t.symbol} @ ₹${fmt(cur)} | +₹${fmt(t.pnl)}`, 'green');
                        closePaperTrade(t.id, cur, 't3');
                        return;
                    }
                }
                // T1 notification only
                const t1Hit = isBuy ? cur >= t.target1 : cur <= t.target1;
                if (t1Hit) showToast(`🟡 T1 HIT — ${t.symbol} ₹${fmt(cur)} — consider partial profit`, 'green');
                // T2 notification only
                const t2Hit = isBuy ? cur >= t.target2 : cur <= t.target2;
                if (t2Hit) showToast(`🟢 T2 HIT — ${t.symbol} ₹${fmt(cur)} — strong profit zone`, 'green');
            });
        })
        .catch(() => {});
}

function getCsrf() {
    const el = document.cookie.split(';').find(c => c.trim().startsWith('csrftoken='));
    return el ? el.split('=')[1].trim() : '';
}


function showPaperTradeModal(direction) {
    const isBuy  = direction === 'buy';
    const emoji  = isBuy ? '🟢' : '🔴';
    const action = isBuy ? 'BUY' : 'SHORT SELL';
    const color  = isBuy ? 'rgba(0,255,136,0.15)' : 'rgba(255,71,87,0.15)';
    const border = isBuy ? 'rgba(0,255,136,0.3)' : 'rgba(255,71,87,0.3)';
    const grad   = isBuy
        ? 'linear-gradient(135deg,rgba(0,255,136,0.1),rgba(0,170,255,0.1))'
        : 'linear-gradient(135deg,rgba(255,71,87,0.1),rgba(255,196,0,0.08))';

    // Update modal content
    document.getElementById('ptModalIcon').textContent       = emoji;
    document.getElementById('ptModalIcon').style.background  = color;
    document.getElementById('ptModalIcon').style.borderColor = border;
    document.getElementById('ptModalHeader').style.background = grad;
    document.getElementById('ptModalTitle').textContent = `Paper ${action} Opened!`;
    document.getElementById('ptModalSub').textContent   = `${currentSymbol} trade saved successfully`;
    document.getElementById('ptModalStock').textContent = currentSymbol;
    document.getElementById('ptModalQty').textContent   = document.getElementById('ptQty').value;
    document.getElementById('ptModalEntry').textContent = '₹' + document.getElementById('ptEntry').value;
    document.getElementById('ptModalSL').textContent    = '₹' + document.getElementById('ptSL').value;
    document.getElementById('ptModalT1').textContent    = '₹' + document.getElementById('ptT1').value;
    document.getElementById('ptModalT3').textContent    = document.getElementById('ptT3').value ? '₹' + document.getElementById('ptT3').value : '—';

    // Show modal
    document.getElementById('ptConfirmModal').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

function ptModalClose() {
    document.getElementById('ptConfirmModal').classList.add('hidden');
    document.body.style.overflow = '';
}

function ptModalGo() {
    window.location.href = '/trades/';
}