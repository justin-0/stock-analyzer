from django.urls import path
from . import views

urlpatterns = [

    # ── Public pages ──────────────────────────
    path('', views.home_view, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
    
  

    # ── User dashboard ────────────────────────
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/', views.profile_view, name='profile'),
    path('api/live-price/', views.live_price, name='live_price'),
    path('api/ws-token/', views.ws_token, name='ws_token'),

    # ── Stock API endpoints ───────────────────
    path('api/analyze/', views.analyze, name='analyze'),
    path('api/candles/', views.get_candles, name='get_candles'),
    path('api/watchlist/', views.toggle_watchlist, name='watchlist'),
    path('api/watchlist/signals/', views.watchlist_signals, name='watchlist_signals'),
    path('api/market/', views.market_overview, name='market_overview'),
    path('api/news/', views.market_news, name='market_news'),
    path('api/stock-news/', views.stock_news, name='stock_news'),
    path('api/upstox-token/', views.save_upstox_token, name='save_upstox_token'),

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