from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [

    # ── Public pages ──────────────────────────
    path('', views.home_view, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),

    # ── Forgot password (Django built-in views, custom templates) ──
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='stocks/password_reset_form.html',
        email_template_name='stocks/password_reset_email.txt',
        subject_template_name='stocks/password_reset_subject.txt',
    ), name='password_reset'),
    path('password-reset/sent/', auth_views.PasswordResetDoneView.as_view(
        template_name='stocks/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='stocks/password_reset_confirm.html',
    ), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='stocks/password_reset_complete.html',
    ), name='password_reset_complete'),

  

    # ── User dashboard ────────────────────────
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/', views.profile_view, name='profile'),
    path('api/live-price/', views.live_price, name='live_price'),

    # ── Stock API endpoints ───────────────────
    path('api/analyze/', views.analyze, name='analyze'),
    path('api/candles/', views.get_candles, name='get_candles'),
    path('api/watchlist/', views.toggle_watchlist, name='watchlist'),
    path('api/watchlist/signals/', views.watchlist_signals, name='watchlist_signals'),
    path('api/market/', views.market_overview, name='market_overview'),
    path('api/news/', views.market_news, name='market_news'),
    path('api/stock-news/', views.stock_news, name='stock_news'),
    path('api/upstox-token/', views.save_upstox_token, name='save_upstox_token'),

    path('trades/', views.trades_view, name='trades'),
    path('api/paper-trade/open/',  views.paper_trade_open,  name='paper_trade_open'),
    path('api/paper-trade/close/', views.paper_trade_close, name='paper_trade_close'),
    path('api/paper-trade/list/',  views.paper_trade_list,  name='paper_trade_list'),

    # ── News & Screener ───────────────────────────────────────
    path('news/', views.news_view, name='news'),
    path('screener/', views.screener_view, name='screener'),
    path('candles/', views.candles_view, name='candles'),
    path('api/screener/', views.screener_api, name='screener_api'),

    # ── Admin panel ───────────────────────────
    path('admin-panel/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-panel/users/', views.admin_users, name='admin_users'),
    path('admin-panel/users/<int:user_id>/', views.admin_user_detail, name='admin_user_detail'),
    path('admin-panel/users/<int:user_id>/approve/', views.admin_approve_user, name='admin_approve'),
    path('admin-panel/users/<int:user_id>/reject/', views.admin_reject_user, name='admin_reject'),
    path('admin-panel/users/<int:user_id>/deactivate/', views.admin_deactivate_user, name='admin_deactivate'),
    path('admin-panel/signals/', views.admin_signals, name='admin_signals'),
    path('admin-panel/activity/', views.admin_activity, name='admin_activity'),
    path('admin-panel/clear-cache/', views.admin_clear_cache, name='admin_clear_cache'),
]