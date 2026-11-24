from pathlib import Path
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured
import dj_database_url
import os

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Railway sets RAILWAY_ENVIRONMENT on every deploy. On Railway we default to
# production-safe settings; locally we default to DEBUG for development.
ON_RAILWAY = bool(os.getenv('RAILWAY_ENVIRONMENT'))

DEBUG = os.getenv('DEBUG', 'False' if ON_RAILWAY else 'True').lower() in ('true', '1', 'yes')

SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('SECRET_KEY environment variable must be set in production.')
    SECRET_KEY = 'django-insecure-local-dev-only-key-do-not-use-in-production'

ALLOWED_HOSTS = [
    'localhost', '127.0.0.1',
    '.railway.app', '.up.railway.app',
    'healthcheck.railway.app',   # Railway health checks
] + [h.strip() for h in os.getenv('ALLOWED_HOSTS', '').split(',') if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'stocks',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'stockanalyzer.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'stockanalyzer.wsgi.application'

# ── DATABASE ──────────────────────────────────────────────────
# Uses PostgreSQL on Railway, SQLite locally
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(default=DATABASE_URL, conn_max_age=600)
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# ── STATIC FILES ──────────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
# STORAGES replaces STATICFILES_STORAGE (removed in Django 5.1 — the old
# setting was silently ignored on Django 6, so WhiteNoise compression was off).
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── CORS ──────────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = True

# ── API KEYS ──────────────────────────────────────────────────
FINNHUB_API_KEY     = os.getenv('FINNHUB_API_KEY', '')
ALPHA_VANTAGE_KEY   = os.getenv('ALPHA_VANTAGE_KEY', '')
UPSTOX_ACCESS_TOKEN = os.getenv('UPSTOX_ACCESS_TOKEN', '')
# One-click "Connect Upstox" login. Create an app at account.upstox.com/developer/apps
# and register the redirect URL https://<your-domain>/upstox/callback/
UPSTOX_API_KEY      = os.getenv('UPSTOX_API_KEY', '')
UPSTOX_API_SECRET   = os.getenv('UPSTOX_API_SECRET', '')
UPSTOX_REDIRECT_URI = os.getenv('UPSTOX_REDIRECT_URI', '')   # optional; defaults to this site's /upstox/callback/

CACHE_TIMEOUT = int(os.getenv('CACHE_TIMEOUT', '300'))

# ── EMAIL (password reset) ────────────────────────────────────
# Set EMAIL_HOST etc. in the environment to send real mail (e.g. Gmail SMTP
# with an app password). Without EMAIL_HOST, emails are printed to the console.
EMAIL_HOST          = os.getenv('EMAIL_HOST', '')
EMAIL_PORT          = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER     = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS       = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_BACKEND = (
    'django.core.mail.backends.smtp.EmailBackend' if EMAIL_HOST
    else 'django.core.mail.backends.console.EmailBackend'
)
DEFAULT_FROM_EMAIL  = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'noreply@stockanalyzer.local')
PASSWORD_RESET_TIMEOUT = 60 * 60  # reset links expire after 1 hour

# Railway terminates HTTPS at its proxy; trust its header so reset links use https://
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ── PRODUCTION SECURITY ───────────────────────────────────────
if not DEBUG:
    SESSION_COOKIE_SECURE = True      # cookies only sent over HTTPS
    CSRF_COOKIE_SECURE    = True
    SECURE_HSTS_SECONDS   = int(os.getenv('SECURE_HSTS_SECONDS', '3600'))  # start small; raise once stable
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'
    # Off by default: Railway already serves HTTPS, and redirecting can break its health checks.
    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'False').lower() in ('true', '1', 'yes')

# ── LOGGING ───────────────────────────────────────────────────
# Send warnings (CSRF failures, 4xx/5xx details) to stdout so they show in Railway logs.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {
        'django.request':  {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'stocks':          {'handlers': ['console'], 'level': 'INFO',    'propagate': False},
    },
}

# ── CSRF ──────────────────────────────────────────────────────
# Stale forms redirect back with a friendly "page expired" message instead of a bare 403.
CSRF_FAILURE_VIEW = 'stocks.views.csrf_failure'
CSRF_TRUSTED_ORIGINS = [
    'https://*.railway.app',
    'https://*.up.railway.app',
    'https://niftyanalyzer.up.railway.app',
]