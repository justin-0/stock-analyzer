"""Upstox login (OAuth) flow and access-token status checks.

Upstox access tokens expire at 3:30 AM IST on the day after they are
generated, so users have to log in to Upstox once per trading day.
"""
import hashlib
import secrets
import time
from datetime import datetime, time as dtime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from django.conf import settings

IST = ZoneInfo('Asia/Kolkata')
AUTH_DIALOG_URL = 'https://api.upstox.com/v2/login/authorization/dialog'
TOKEN_URL = 'https://api.upstox.com/v2/login/authorization/token'
PROFILE_URL = 'https://api.upstox.com/v2/user/profile'
EXPIRY_TIME_IST = dtime(3, 30)

_STATUS_CACHE = {}          # sha256(token) -> (status, checked_at)
_STATUS_TTL = 300           # re-check a token with Upstox at most every 5 minutes


def oauth_enabled():
    return bool(getattr(settings, 'UPSTOX_API_KEY', '') and getattr(settings, 'UPSTOX_API_SECRET', ''))


def token_expiry(issued_at):
    """Next 3:30 AM IST after the token was issued."""
    local = issued_at.astimezone(IST)
    expiry = datetime.combine(local.date(), EXPIRY_TIME_IST, tzinfo=IST)
    if local >= expiry:
        expiry += timedelta(days=1)
    return expiry


def new_state():
    return secrets.token_urlsafe(24)


def build_auth_url(redirect_uri, state):
    return AUTH_DIALOG_URL + '?' + urlencode({
        'response_type': 'code',
        'client_id': settings.UPSTOX_API_KEY,
        'redirect_uri': redirect_uri,
        'state': state,
    })


def exchange_code(code, redirect_uri):
    """Swap the one-time ?code= from Upstox for an access token. Returns token or raises."""
    r = requests.post(TOKEN_URL, timeout=10, headers={
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded',
    }, data={
        'code': code,
        'client_id': settings.UPSTOX_API_KEY,
        'client_secret': settings.UPSTOX_API_SECRET,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    })
    if r.status_code != 200:
        raise ValueError(f'Upstox returned HTTP {r.status_code}')
    token = (r.json() or {}).get('access_token')
    if not token:
        raise ValueError('Upstox response had no access token')
    return token


def check_token(token):
    """'valid' | 'expired' | 'unknown' (network trouble). Cached per token."""
    key = hashlib.sha256(token.encode()).hexdigest()
    hit = _STATUS_CACHE.get(key)
    if hit and time.time() - hit[1] < _STATUS_TTL:
        return hit[0]
    try:
        r = requests.get(PROFILE_URL, timeout=6, headers={
            'Authorization': f'Bearer {token}', 'Accept': 'application/json',
        })
        status = 'valid' if r.status_code == 200 else 'expired' if r.status_code in (401, 403) else 'unknown'
    except requests.RequestException:
        status = 'unknown'
    if status != 'unknown':
        _STATUS_CACHE[key] = (status, time.time())
    return status


def forget_token(token):
    _STATUS_CACHE.pop(hashlib.sha256(token.encode()).hexdigest(), None)


def token_status(profile, now=None):
    """Status of the token this user's requests will actually use."""
    now = now or datetime.now(tz=IST)
    personal = (getattr(profile, 'upstox_token', '') or '').strip()
    if personal:
        updated = getattr(profile, 'upstox_token_updated', None)
        expires = token_expiry(updated) if updated else None
        if expires and now >= expires:
            status = 'expired'          # known from the clock, no API call needed
        else:
            status = check_token(personal)
        return {'status': status, 'source': 'personal',
                'expires_at': expires.isoformat() if expires else None}

    server = (getattr(settings, 'UPSTOX_ACCESS_TOKEN', '') or '').strip()
    if server:
        return {'status': check_token(server), 'source': 'server', 'expires_at': None}

    return {'status': 'missing', 'source': None, 'expires_at': None}
