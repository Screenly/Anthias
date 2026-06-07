"""
Django settings for anthias_server.django_project project.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.2/ref/settings/
"""

import secrets
import sys
import zoneinfo
from os import getenv
from pathlib import Path
from typing import Any

import sentry_sdk

from anthias_common.version import get_anthias_release
from anthias_server.settings import settings as device_settings

# django_stubs_ext.monkeypatch() makes Django generic classes
# subscriptable at runtime, and the server side of this repo relies on
# that — anthias_server.app/admin.py defines `class AssetAdmin(admin.ModelAdmin
# [Asset])` at import time, which raises TypeError without the patch.
# Keep the import optional so the viewer image (and any future service
# that doesn't ship django-stubs-ext) can still load this settings
# module; do not remove the patch as a no-op.
try:
    import django_stubs_ext

    django_stubs_ext.monkeypatch()
except ModuleNotFoundError as exc:
    if exc.name != 'django_stubs_ext':
        raise

# Repo root: src/anthias_server/django_project/settings.py → up 3 to repo root.
BASE_DIR = Path(__file__).resolve().parents[3]

# Detect "running under tests" without depending solely on the
# ENVIRONMENT env var. The root conftest.py sets ENVIRONMENT=test via
# os.environ.setdefault, but pytest-django's plugin-time settings load
# can fire before that conftest executes — leaving getenv() blank and
# this module pointed at the production /data path on local pytest
# runs. Detect pytest itself by inspecting argv (covers `pytest ...`,
# `python -m pytest ...`, and `uv run pytest ...`) so the test branch
# is taken regardless of import order. Used by both the Sentry DSN
# default here and the DATABASES test branch further down.
_running_under_pytest = any('pytest' in (a or '') for a in sys.argv)

# Operators can point crash reporting at their own Sentry project by
# setting SENTRY_DSN, or disable it entirely by setting it to an
# empty string (sentry_sdk treats a falsy DSN as "don't send").
#
# Test runs default to the empty DSN: the unit suite is built to run
# with no external network dependencies (conftest.py force-mocks
# Redis for the same reason), and exceptions raised on purpose by
# failing tests must not land in the production Sentry project. An
# explicit SENTRY_DSN still wins so the integration stack can opt in
# deliberately.
_default_sentry_dsn = (
    ''
    if getenv('ENVIRONMENT') == 'test' or _running_under_pytest
    else (
        'https://da18c7bdab65c9adc4afcd311f5b6f09'
        '@o4511522371534848.ingest.us.sentry.io/4511522375794688'
    )
)
sentry_sdk.init(
    dsn=getenv('SENTRY_DSN', _default_sentry_dsn),
    # Same value the DEBUG flag below keys off: 'development', 'test',
    # or 'production' (the default) — lets dev events be filtered out
    # in Sentry. (Test runs don't send at all — see the DSN default
    # above.)
    environment=getenv('ENVIRONMENT', 'production'),
    # CalVer release from pyproject.toml's [project].version, via the
    # same helper the System Info page and v2 info API use (handles
    # the `uv sync --no-install-project` Docker layout). Empty string
    # means both sources failed — pass None so Sentry doesn't record
    # a bogus '' release.
    release=get_anthias_release() or None,
    # Request headers and user IPs are PII. Attach them only when the
    # operator hasn't opted out of analytics in anthias.conf — the
    # same knob that gates GA telemetry (see lib/telemetry.py). Crash
    # events themselves still flow; this gates only the PII
    # enrichment. See
    # https://docs.sentry.io/platforms/python/data-management/data-collected/
    send_default_pii=not device_settings['analytics_opt_out'],
)


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/


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

# CSRF_TRUSTED_ORIGINS handles the case where a reverse proxy serves
# Anthias under a hostname different from the upstream Host the device
# sees — IIS ARR with preserveHostHeader=false is the canonical
# example (issue #2900): the browser sends
# ``Origin: https://signage.example.com`` while uvicorn sees
# ``Host: anthias.localdomain``, so neither Django's stock
# host-and-scheme check nor the same-host fallback in
# SameHostOriginCsrfMiddleware can recognise the request as
# same-origin. The operator opts in by listing the public origins they
# actually serve Anthias under, e.g.
# ``CSRF_TRUSTED_ORIGINS=https://signage.example.com``.
#
# A wildcard scheme like ``https://*`` is not accepted by Django and
# isn't useful here — operators who proxy under a single fixed
# hostname per device just list that one origin. Default is empty;
# the same-host fallback continues to cover plain LAN / Caddy-sidecar
# deployments where the proxy preserves Host upstream.
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in getenv('CSRF_TRUSTED_ORIGINS', '').split(',')
    if o.strip()
]


# Application definition

# Apps every Django consumer needs: ORM access to the Asset model,
# plus the contenttypes + auth tables those models implicitly depend
# on. Loaded by every service that calls django.setup() — server,
# celery, viewer, test.
INSTALLED_APPS = [
    'anthias_server.app.apps.AnthiasAppConfig',
    'django.contrib.contenttypes',
    'django.contrib.auth',
]

# Apps only the HTTP-serving services need (REST API, OpenAPI schema,
# Channels for WebSockets, the admin UI, sessions/messages, static
# files, DB backups). The viewer never serves HTTP, so it skips these
# at django.setup() time and the viewer image doesn't have to ship
# the packages they live in. Server/celery/test images are unaffected.
if getenv('ANTHIAS_SERVICE') != 'viewer':
    INSTALLED_APPS += [
        'channels',
        'drf_spectacular',
        'rest_framework',
        'anthias_server.api.apps.ApiConfig',
        'django.contrib.admin',
        'django.contrib.humanize',
        'django.contrib.sessions',
        'django.contrib.messages',
        'django.contrib.staticfiles',
        'dbbackup',
    ]

# Sonar's S4502 ("disabling CSRF protection") fires on the MIDDLEWARE
# list because it pattern-matches the literal ``CsrfViewMiddleware``
# class name and doesn't see one. SameHostOriginCsrfMiddleware (see
# src/anthias_server/lib/csrf.py) is a subclass of
# ``django.middleware.csrf.CsrfViewMiddleware``, so CSRF protection
# is still wired in — the rule's a false positive. NOSONAR on the
# closing bracket where S4502 actually raises its issue.
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'anthias_server.lib.csrf.SameHostOriginCsrfMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]  # NOSONAR

ROOT_URLCONF = 'anthias_server.django_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
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

ASGI_APPLICATION = 'anthias_server.django_project.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [(getenv('REDIS_HOST', 'redis'), 6379)],
        },
    },
}


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# In test mode the DB path defaults to a repo-local file so the suite
# runs without Docker / without writable `/data`. CI containers can
# preserve their existing layout by exporting
# `ANTHIAS_TEST_DB_PATH=/data/.anthias/test.db` (see
# docker-compose.test.yml).
if getenv('ENVIRONMENT') == 'test' or _running_under_pytest:
    db_path = getenv('ANTHIAS_TEST_DB_PATH') or str(
        BASE_DIR / '.anthias-test.db'
    )
else:
    db_path = '/data/.anthias/anthias.db'

# Integration tests drive the live anthias-server container in
# docker-compose.test.yml. The test process and the server live in
# different containers but must share DB state — the test asserts on
# `Asset.objects.all()` after the server persists an uploaded file.
# Without TEST.NAME, pytest-django defaults to an in-memory SQLite, so
# writes from the server never reach the test process. Pinning
# TEST.NAME to the same path keeps both ends on one DB; the
# `transaction=True` marker truncates between tests, which is safe
# because the test DB is throwaway.
#
# ONLY set TEST.NAME for the integration step. The unit step runs
# under `pytest -n auto`; pinning every worker to the same SQLite
# file would cause `database is locked` and cross-worker leakage.
# Without TEST.NAME, pytest-django gives each xdist worker its own
# `:memory:` DB, which is what we want for unit runs.
# .github/workflows/test-runner.yml exports ANTHIAS_INTEGRATION_TEST=1
# only for the `pytest -m integration` step.
# Three processes share this SQLite file across containers (uvicorn,
# the celery worker, the viewer — they bind-mount the same
# ~/.anthias). The stock rollback journal allows exactly one writer
# and blocks readers while a write transaction is open, and the stock
# busy timeout is 0 — so a celery sweep UPDATE landing while the
# viewer reads its asset list raised ``OperationalError: database is
# locked`` immediately instead of waiting (Sentry ANTHIAS-C/E/G).
#
#   * ``timeout`` — sqlite's busy handler: wait up to 20s for a lock
#     instead of failing on the spot. Longer than any transaction
#     Anthias runs (the writes are single-row UPDATEs; the longest
#     holder is the startup ``migrate``/``dbbackup`` pass).
#   * ``journal_mode=WAL`` — readers no longer block the writer and
#     vice versa; persists in the DB file but is re-asserted each
#     connect so restored backups / legacy DBs get upgraded too.
#   * ``synchronous=NORMAL`` — the recommended pairing with WAL;
#     FULL's per-commit fsync cost buys nothing extra under WAL on
#     a power-loss-prone SD card (WAL commits are crash-safe at
#     NORMAL; worst case is losing the very last commit).
#   * ``transaction_mode=IMMEDIATE`` — take the write lock at
#     transaction start so two writers queue on the busy handler
#     instead of deadlocking mid-transaction on the read→write lock
#     upgrade (those fail instantly, ignoring the busy timeout).
#
# Unit runs (pytest-django's per-worker ``:memory:`` DBs) accept the
# same pragmas harmlessly: ``journal_mode=WAL`` is a no-op that
# reports ``memory`` on in-memory databases.
_db_default: dict[str, Any] = {
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': db_path,
    'OPTIONS': {
        'timeout': 20,
        'init_command': ('PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;'),
        'transaction_mode': 'IMMEDIATE',
    },
}
if getenv('ANTHIAS_INTEGRATION_TEST') == '1':
    _db_default['TEST'] = {'NAME': db_path}

DATABASES = {'default': _db_default}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators
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
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

USE_I18N = True

USE_TZ = True


def get_host_time_zone(
    timezone_file: str = '/etc/timezone',
    zoneinfo_root: Path = Path('/usr/share/zoneinfo'),
) -> str:
    """Read the host's /etc/timezone, falling back to UTC.

    /etc/timezone is bind-mounted from the host, so its value is
    whatever the host distro wrote there. Validate it the same way
    Django does in django.conf.Settings.__init__ — a zoneinfo lookup
    PLUS the /usr/share/zoneinfo file check. Validating with a bundled
    database alone (the old pytz check) is not enough: it accepts
    names like `US/Central` that the image's tzdata may not ship on
    disk, and Django then raises ValueError at startup, crash-looping
    every Django process on the device.
    """
    try:
        with open(timezone_file, 'r') as f:
            time_zone = f.read().strip()
        zoneinfo.ZoneInfo(time_zone)
        if (
            zoneinfo_root.exists()
            and not zoneinfo_root.joinpath(*time_zone.split('/')).exists()
        ):
            raise zoneinfo.ZoneInfoNotFoundError(time_zone)
        return time_zone
    except (OSError, ValueError, zoneinfo.ZoneInfoNotFoundError):
        return 'UTC'


TIME_ZONE = get_host_time_zone()


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = '/static/'
# Baked into the production server image at build time
# (docker/Dockerfile.server.j2 runs `manage.py collectstatic` after
# the bun-built dist/ is copied in). The runtime container treats
# this path as read-only: admin assets and collected app static are
# immutable per-image. Dev (DEBUG=True) bypasses STATIC_ROOT
# entirely via WHITENOISE_USE_FINDERS below, so the path doesn't
# need to exist in the dev image.
STATIC_ROOT = '/usr/src/app/staticfiles'

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
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'anthias_server.api.helpers.custom_exception_handler',
    # Two auth paths for the JSON API.
    #
    # Ordering matters: DRF tries authenticators in sequence and the
    # first one that recognises the request short-circuits the rest.
    # ``SessionAuthentication.authenticate`` enforces CSRF on unsafe
    # methods whenever a session cookie is present, and a missing
    # ``X-CSRFToken`` raises 403 — which would mask a perfectly valid
    # ``Authorization: Basic …`` header on the same request (some CLI
    # tooling shares a cookie jar with the operator's browser).
    # Run BasicAuthentication first so an explicit Authorization
    # header always wins over an incidental session cookie.
    #
    #   * DeprecatedBasicAuthentication — DRF's stock
    #     ``BasicAuthentication`` plus a throttled warning log
    #     (one line per (user, IP, path) per 1-hour TTL) so we can
    #     identify the last callers without flooding the log when a
    #     polling client hammers a single endpoint. Retained for
    #     back-compat with pre-2826 Anthias-CLI builds and
    #     third-party scripts written against the old auth.
    #   * GatedSessionAuthentication — DRF's stock
    #     ``SessionAuthentication`` plus the same ``auth_backend``
    #     gate as DeprecatedBasicAuthentication: when auth is
    #     disabled (``settings['auth_backend'] == ''``) both classes
    #     no-op so the documented "auth disabled = API is fully
    #     open" contract holds even for clients that happen to carry
    #     a session cookie or a malformed Authorization header.
    #
    # New integrations should use the bearer-token path coming in a
    # follow-up PR (UI-managed personal tokens, not
    # username/password exchange).
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'anthias_server.lib.auth.DeprecatedBasicAuthentication',
        'anthias_server.lib.auth.GatedSessionAuthentication',
    ],
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Anthias API',
    'VERSION': '2.0.0',
    'PREPROCESSING_HOOKS': [
        'anthias_server.api.api_docs_filter_spec.preprocessing_filter_spec'
    ],
}

# django-dbbackup v5 moved storage config under Django 5's STORAGES
# dict; defining STORAGES replaces the framework defaults entirely.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
    'dbbackup': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
        'OPTIONS': {'location': '/data/.anthias/backups'},
    },
}
DBBACKUP_HOSTNAME = 'anthias'
