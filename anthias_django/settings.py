"""
Django settings for anthias_django project.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.2/ref/settings/
"""

import secrets
from os import getenv
from pathlib import Path

import pytz

from settings import settings as device_settings

# django-stubs-ext is a dev/typing dep (shipped in the server image but
# not in viewer/wifi-connect). monkeypatch() makes Django generic
# classes subscriptable at runtime — only needed if we ever evaluate
# `QuerySet[Asset]`-style annotations at import time. No runtime code
# does today, so import optionally and skip the patch when absent.
try:
    import django_stubs_ext

    django_stubs_ext.monkeypatch()
except ImportError:
    pass

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/


# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = getenv('ENVIRONMENT', 'production') in ['development', 'test']

if not DEBUG:
    if not device_settings.get('django_secret_key'):
        # Modify the generated so that string interpolation
        # errors can be avoided.
        secret_key = secrets.token_urlsafe(50)
        device_settings['django_secret_key'] = secret_key
        device_settings.save()

    SECRET_KEY = device_settings.get('django_secret_key')
else:
    # SECURITY WARNING: keep the secret key used in production secret!
    SECRET_KEY = (
        'django-insecure-7rz*$)g6dk&=h-3imq2xw*iu!zuhfb&w6v482_vs!w@4_gha=j'  # noqa: E501
    )

# Anthias is a local-network signage device with no fixed public
# hostname — the device is reached by LAN IP, mDNS name, or the
# operator's chosen DNS entry. The default of '*' preserves that
# flexibility but is permissive against DNS-rebinding attacks where a
# malicious page rebinds an attacker-controlled hostname to the
# device's IP. Operators on hardened LANs can opt into a strict
# allowlist by setting the ALLOWED_HOSTS env var (comma-separated;
# include the LAN IP / mDNS name / etc.).
ALLOWED_HOSTS = [
    h.strip() for h in getenv('ALLOWED_HOSTS', '*').split(',') if h.strip()
]

# CSRF_TRUSTED_ORIGINS is intentionally not set. Django only honours
# subdomain wildcards there (e.g. https://*.example.com), so a leading
# 'http://*' / 'https://*' would be a no-op rather than the broad
# allowlist it appears to be. Same-origin POSTs pass without it via
# Django's built-in Host/Origin equality check.


# Application definition

# The viewer process only needs Django for ORM access to the Asset
# model — it never serves HTTP, runs migrations, or handles WebSocket
# traffic. Skip the apps that exist purely for the web UI/REST API so
# the viewer image doesn't have to ship channels, DRF, drf-spectacular,
# whitenoise, dbbackup, etc. Server/celery/test still get the full set.
if getenv('ANTHIAS_SERVICE') == 'viewer':
    INSTALLED_APPS = [
        'anthias_app.apps.AnthiasAppConfig',
        'django.contrib.contenttypes',
        'django.contrib.auth',
    ]
else:
    INSTALLED_APPS = [
        'channels',
        'anthias_app.apps.AnthiasAppConfig',
        'drf_spectacular',
        'rest_framework',
        'api.apps.ApiConfig',
        'django.contrib.admin',
        'django.contrib.auth',
        'django.contrib.contenttypes',
        'django.contrib.sessions',
        'django.contrib.messages',
        'django.contrib.staticfiles',
        'dbbackup',
    ]

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

ROOT_URLCONF = 'anthias_django.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
        ],
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

ASGI_APPLICATION = 'anthias_django.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [(getenv('REDIS_HOST', 'redis'), 6379)],
        },
    },
}


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': (
            '/data/.anthias/test.db'
            if getenv('ENVIRONMENT') == 'test'
            else '/data/.anthias/anthias.db'
        ),
    },
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators
AUTH_MODULE_PREFIX = 'django.contrib.auth.password_validation'
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': f'{AUTH_MODULE_PREFIX}.UserAttributeSimilarityValidator',
    },
    {
        'NAME': f'{AUTH_MODULE_PREFIX}.MinimumLengthValidator',
    },
    {
        'NAME': f'{AUTH_MODULE_PREFIX}.CommonPasswordValidator',
    },
    {
        'NAME': f'{AUTH_MODULE_PREFIX}.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

USE_I18N = True

USE_L10N = True

USE_TZ = True

try:
    with open('/etc/timezone', 'r') as f:
        TIME_ZONE = f.read().strip()
        pytz.timezone(TIME_ZONE)  # Checks if the timezone is valid.
except (pytz.exceptions.UnknownTimeZoneError, FileNotFoundError):
    TIME_ZONE = 'UTC'


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
STATIC_ROOT = '/data/anthias/staticfiles'

# Dev runs uvicorn (not runserver) and skips collectstatic, so files
# only exist in STATICFILES_DIRS — let WhiteNoise fall back to the
# finders and re-stat on each request so live-reloaded JS/CSS shows up.
if DEBUG:
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = True

# Backups can be multi-GB; preserve the 4 GB body capacity nginx provided.
DATA_UPLOAD_MAX_MEMORY_SIZE = None
FILE_UPLOAD_MAX_MEMORY_SIZE = 26_214_400

# Trust X-Forwarded-Proto from a TLS-terminating proxy (the Caddy
# sidecar that bin/enable_ssl.sh installs) only when uvicorn has been
# told to honour proxy headers via FORWARDED_ALLOW_IPS. Without the
# gate, any client could set X-Forwarded-Proto: https on a plain-HTTP
# deploy and flip request.is_secure() — secure-cookied sessions would
# then drop on the next plain-HTTP request, and redirects would point
# at https:// URLs that don't exist.
if getenv('FORWARDED_ALLOW_IPS'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'api.helpers.custom_exception_handler',
    # The project uses custom authentication classes,
    # so we need to disable the default ones.
    'DEFAULT_AUTHENTICATION_CLASSES': [],
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Anthias API',
    'VERSION': '2.0.0',
    'PREPROCESSING_HOOKS': [
        'api.api_docs_filter_spec.preprocessing_filter_spec'
    ],
}

# `django-dbbackup` settings
DBBACKUP_STORAGE = 'django.core.files.storage.FileSystemStorage'
DBBACKUP_STORAGE_OPTIONS = {'location': '/data/.anthias/backups'}
DBBACKUP_HOSTNAME = 'anthias'
