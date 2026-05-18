from django.contrib import admin
from .models import UserProfile, Watchlist, ActivityLog, SignalHistory, StockCache

admin.site.register(UserProfile)
admin.site.register(Watchlist)
admin.site.register(ActivityLog)
admin.site.register(SignalHistory)
admin.site.register(StockCache)