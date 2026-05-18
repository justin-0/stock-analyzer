from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings
from django.db.models import Count, Q
from .models import Watchlist, UserProfile, ActivityLog, SignalHistory, StockCache
from .utils import (
    get_quote, get_candle_data, calculate_indicators, generate_signal,
    detect_candle_patterns, get_support_resistance, score_trade_types,
    get_upstox_quote, get_market_overview,
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

def create_admin(request):
    from django.contrib.auth.models import User
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', 'admin@admin.com', 'admin123')
        return JsonResponse({'status': 'created'})
    return JsonResponse({'status': 'exists'})


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

    signal = generate_signal(indicators, trade_type)
    if not signal:
        return JsonResponse({'error': 'Could not generate signal.'}, status=500)

    patterns = detect_candle_patterns(candle_data)
    sr = get_support_resistance(candle_data)
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

    # Use user's personal Upstox token if saved, else .env fallback (handled inside get_upstox_quote)
    profile    = getattr(request.user, 'profile', None)
    user_token = getattr(profile, 'upstox_token', '').strip() or None

    quote = get_upstox_quote(symbol, access_token=user_token)

    if not quote:
        # Try yfinance directly — no cache
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

    return JsonResponse({
        'price':      quote['current'],
        'change':     quote['change'],
        'change_pct': quote['change_pct'],
        'high':       quote.get('high', 0),
        'low':        quote.get('low',  0),
        'source':     quote.get('source', 'unknown'),
        'symbol':     symbol,
    })
# ─── WebSocket Token Endpoint ───────────────────────────────────
# Provides Upstox WebSocket auth URL so the browser can open a
# direct ws:// connection for true tick-by-tick streaming data.

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