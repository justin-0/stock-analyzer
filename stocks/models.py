from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserProfile(models.Model):
    EXPERIENCE_CHOICES = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('expert', 'Expert'),
    ]
    TRADE_TYPE_CHOICES = [
        ('intraday', 'Intraday'),
        ('swing', 'Swing'),
        ('longterm', 'Long Term'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=15, blank=True)
    trading_experience = models.CharField(max_length=20, choices=EXPERIENCE_CHOICES, default='beginner')
    preferred_trade_type = models.CharField(max_length=20, choices=TRADE_TYPE_CHOICES, default='swing')
    capital = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    avatar_initials = models.CharField(max_length=3, blank=True)  # auto-generated from name
    is_admin = models.BooleanField(default=False)  # custom admin flag
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    upstox_token = models.TextField(blank=True, default='')
    upstox_token_updated = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - Profile"

    def save(self, *args, **kwargs):
        # Auto-generate initials from first/last name
        fn = self.user.first_name[:1].upper() if self.user.first_name else ''
        ln = self.user.last_name[:1].upper() if self.user.last_name else ''
        self.avatar_initials = fn + ln or self.user.username[:2].upper()
        super().save(*args, **kwargs)


class Watchlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='watchlist')
    symbol = models.CharField(max_length=20)
    name = models.CharField(max_length=100, blank=True)
    exchange = models.CharField(max_length=10, default='NSE')
    added_on = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.symbol}"

    class Meta:
        ordering = ['-added_on']
        unique_together = ['user', 'symbol']


class StockCache(models.Model):
    symbol = models.CharField(max_length=20)
    data_type = models.CharField(max_length=50)
    data = models.JSONField()
    cached_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.symbol} - {self.data_type}"

    class Meta:
        unique_together = ['symbol', 'data_type']


class ActivityLog(models.Model):
    """Tracks every user action for admin to see"""
    ACTION_CHOICES = [
        ('login', 'Logged In'),
        ('logout', 'Logged Out'),
        ('analyze', 'Analyzed Stock'),
        ('watchlist_add', 'Added to Watchlist'),
        ('watchlist_remove', 'Removed from Watchlist'),
        ('register', 'Registered'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activities')
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    detail = models.CharField(max_length=200, blank=True)  # e.g. stock symbol
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.timestamp}"

    class Meta:
        ordering = ['-timestamp']


class SignalHistory(models.Model):
    """Saves every signal generated so admin can see what users are analyzing"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='signals')
    symbol = models.CharField(max_length=20)
    trade_type = models.CharField(max_length=20)
    signal = models.CharField(max_length=20)  # BUY / SELL / WAIT
    entry_price = models.DecimalField(max_digits=12, decimal_places=2)
    stop_loss = models.DecimalField(max_digits=12, decimal_places=2)
    target1 = models.DecimalField(max_digits=12, decimal_places=2)
    target2 = models.DecimalField(max_digits=12, decimal_places=2)
    risk_level = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.symbol} - {self.signal}"

    class Meta:
        ordering = ['-created_at']