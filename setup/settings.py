"""
Django settings for CyberSpark (setup project).

Single settings module driven entirely by environment variables so the
same file works locally (SQLite, DEBUG=True) and on Render (Postgres,
DEBUG=False). Do not create a second "settings_production.py" — add
new environment-dependent behaviour here, gated on env vars.
"""

import os
import sys
from pathlib import Path

import dj_database_url

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ------------------------------------------------------------------
# Core
# ------------------------------------------------------------------
DEBUG = env_bool("DEBUG", default=False)

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        # Fine for local dev only — never used when DEBUG=False.
        SECRET_KEY = "django-insecure-local-dev-key-do-not-use-in-production"
    else:
        raise RuntimeError(
            "SECRET_KEY environment variable is not set. "
            "Set it in your Render dashboard (Environment tab) before deploying."
        )

# Render exposes the app's hostname via RENDER_EXTERNAL_HOSTNAME automatically.
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if render_host and render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(render_host)
if not DEBUG:
    ALLOWED_HOSTS.append(".onrender.com")

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")
if render_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_host}")
if not DEBUG:
    CSRF_TRUSTED_ORIGINS.append("https://*.onrender.com")


# ------------------------------------------------------------------
# Applications
# ------------------------------------------------------------------
USE_CLOUDINARY = bool(os.environ.get("CLOUDINARY_URL"))

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.sitemaps',
]

if USE_CLOUDINARY:
    # Must be listed before django.contrib.staticfiles per cloudinary_storage docs.
    INSTALLED_APPS += ['cloudinary_storage']

INSTALLED_APPS += [
    'django.contrib.staticfiles',
    'whitenoise.runserver_nostatic',
    'api',
]

if USE_CLOUDINARY:
    INSTALLED_APPS += ['cloudinary']

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'setup.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'setup.wsgi.application'


# ------------------------------------------------------------------
# Database
# ------------------------------------------------------------------
# DB_CONN_MAX_AGE: how long (seconds) Django keeps a DB connection open
# and reuses it across requests. Set this to 0 if DATABASE_URL points at
# Supabase's "Transaction" pooler (port 6543) — that pooler doesn't
# support long-lived connections the way Django expects. It's safe to
# leave at the default (600s) for a direct connection or Supabase's
# "Session" pooler. See DEPLOYMENT.md for which one to use.
DB_CONN_MAX_AGE = int(os.environ.get("DB_CONN_MAX_AGE", "600"))

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=DB_CONN_MAX_AGE,
            ssl_require=not DEBUG,
        )
    }
    if DB_CONN_MAX_AGE == 0:
        # Pgbouncer in transaction-pooling mode doesn't support named
        # (server-side) cursors reliably — disable them so Django falls
        # back to fetching full result sets, which works fine at this
        # app's scale.
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# ------------------------------------------------------------------
# Passwords
# ------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ------------------------------------------------------------------
# I18N
# ------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get("TIME_ZONE", "Africa/Lagos")
USE_I18N = True
USE_TZ = True


# ------------------------------------------------------------------
# Static & media files
# ------------------------------------------------------------------
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
# No STATICFILES_DIRS needed: 'api' is a local app in INSTALLED_APPS, so its
# api/static/ directory is already picked up automatically by Django's
# AppDirectoriesFinder. Adding it again here would just cause duplicate-file
# warnings during collectstatic.

if DEBUG:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }

if USE_CLOUDINARY:
    # Persistent media storage — required in production if you rely on
    # bank-transfer proof uploads, since Render's local disk is ephemeral.
    STORAGES["default"] = {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
# NOTE: Render's default disk is ephemeral — files written to MEDIA_ROOT
# (e.g. bank-transfer payment proofs) will be LOST on every redeploy/restart
# unless you attach a Render Persistent Disk or set CLOUDINARY_URL (see
# DEPLOYMENT.md) to route uploads to Cloudinary instead.


# ------------------------------------------------------------------
# Auth redirects
# ------------------------------------------------------------------
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_URL = 'logout'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ------------------------------------------------------------------
# Security (applied automatically whenever DEBUG=False)
# ------------------------------------------------------------------
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30  # 30 days
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # must be readable by JS if you add AJAX forms later


# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {'format': '[{levelname}] {asctime} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose'},
    },
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
        'api': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}


# ------------------------------------------------------------------
# Email
# ------------------------------------------------------------------
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "CyberSpark Enroll <no-reply@cyberspark.example>")

if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=True)
else:
    # No SMTP configured — emails are printed to the console/logs instead
    # of sent. Fine for local dev; set EMAIL_HOST etc. in production.
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


# ------------------------------------------------------------------
# Rate limiting (login, signup, contact form — see api/views.py)
# ------------------------------------------------------------------
# Uses Django's default cache (LocMemCache) unless CACHES is configured
# elsewhere. LocMemCache is per-process, so on a multi-worker gunicorn
# deployment each worker enforces its own limit independently rather than
# a single shared one — fine at this app's scale, but for stricter/shared
# rate limiting under heavier traffic, point CACHES at Redis and this
# starts working across all workers automatically.
RATELIMIT_ENABLE = 'test' not in sys.argv and env_bool('RATELIMIT_ENABLE', default=True)


# ------------------------------------------------------------------
# Paystack
# ------------------------------------------------------------------
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "")

# ------------------------------------------------------------------
# Bank transfer details shown to users at checkout
# ------------------------------------------------------------------
BANK_TRANSFER_DETAILS = {
    "bank_name": os.environ.get("BANK_NAME", "Set BANK_NAME in your environment"),
    "account_name": os.environ.get("BANK_ACCOUNT_NAME", "Set BANK_ACCOUNT_NAME in your environment"),
    "account_number": os.environ.get("BANK_ACCOUNT_NUMBER", "Set BANK_ACCOUNT_NUMBER in your environment"),
}

# Where the "Contact Us" form sends its messages. Falls back to the
# from-address so the form still works with zero extra config.
CONTACT_RECIPIENT_EMAIL = os.environ.get("CONTACT_RECIPIENT_EMAIL", DEFAULT_FROM_EMAIL)
