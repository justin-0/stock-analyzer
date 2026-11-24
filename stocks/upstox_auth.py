"""Upstox login (OAuth) flow and access-token status checks.

Two kinds of token work with this app:
- daily access token (login / OAuth): expires 3:30 AM IST the next day
- Analytics Token: read-only market data, valid for 1 year
Both are JWTs carrying an `exp` claim, which is preferred when present.
"""
import base64
import hashlib
import json
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
# Check with market-quote LTP: it is what the app actually uses, and it works for
# Analytics Tokens (account endpoints like /user/profile reject them without a static IP).
CHECK_URL = 'https://api.upstox.com/v2/market-quote/ltp'
CHECK_SYMBOL = 'NSE_EQ|INE002A01018'   # RELIANCE
EXPIRY_TIME_IST = dtime(3, 30)

_STATUS_CACHE = {}          # sha256(token) -> (status, checked_at)
_STATUS_TTL = 300           # re-check a token with Upstox at most every 5 minutes


def jwt_expiry(token):
    """Read the token's own `exp` claim (no signature check; used for display/expiry only)."""
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get('exp')
        return datetime.fromtimestamp(int(exp), tz=IST) if exp else None
    except Exception:
        return None


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
        r = requests.get(CHECK_URL, params={'symbol': CHECK_SYMBOL}, timeout=6, headers={
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


def describe_token(token, issued_at=None, now=None):
    """{'status', 'expires_at'} for one token. A known past expiry skips the API call."""
    now = now or datetime.now(tz=IST)
    expires = jwt_expiry(token) or (token_expiry(issued_at) if issued_at else None)
    status = 'expired' if expires and now >= expires else check_token(token)
    return {'status': status, 'expires_at': expires.isoformat() if expires else None}


def personal_token(profile, now=None):
    """The user's own token, or None if they have none or it has expired, so an
    expired personal token never hides the working shared (Railway) token."""
    tok = (getattr(profile, 'upstox_token', '') or '').strip()
    if not tok:
        return None
    info = describe_token(tok, getattr(profile, 'upstox_token_updated', None), now)
    return None if info['status'] == 'expired' else tok


def token_status(profile, now=None):
    """Status of the token this user's requests will actually use."""
    personal = (getattr(profile, 'upstox_token', '') or '').strip()
    if personal:
        info = describe_token(personal, getattr(profile, 'upstox_token_updated', None), now)
        if info['status'] != 'expired':
            return {**info, 'source': 'personal'}

    server = (getattr(settings, 'UPSTOX_ACCESS_TOKEN', '') or '').strip()
    if server:
        return {**describe_token(server, now=now), 'source': 'server'}
    if personal:
        return {'status': 'expired', 'source': 'personal', 'expires_at': None}
    return {'status': 'missing', 'source': None, 'expires_at': None}
