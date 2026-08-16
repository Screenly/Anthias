import json
import logging
import os
import secrets
import string
from collections import Counter

from requests import exceptions
from requests import post as requests_post

from anthias_common.device_helper import parse_cpu_info
from anthias_common.utils import connect_to_redis, is_balena_app, is_ci
from anthias_common.version import get_anthias_release
from anthias_server.app.models import Asset
from anthias_server.lib.diagnostics import get_git_branch, get_git_short_hash
from anthias_server.settings import settings

logger = logging.getLogger(__name__)

ANALYTICS_MEASURE_ID = 'G-S3VX8HTPK7'
ANALYTICS_API_SECRET = 'G8NcBpRIS9qBsOj3ODK8gw'
ANALYTICS_URL = 'https://www.google-analytics.com/mp/collect'
ANALYTICS_TIMEOUT = 5

# celery-beat runs with the in-memory scheduler (--scheduler
# celery.beat.Scheduler in docker-compose.yml.tmpl), so the daily
# interval resets on every container restart. The cooldown lives in
# the persisted Redis volume so devices that reboot frequently still
# emit at most one telemetry event per 24h.
TELEMETRY_COOLDOWN_TTL = 60 * 60 * 24
TELEMETRY_COOLDOWN_KEY = 'telemetry-cooldown'
DEVICE_ID_KEY = 'device_id'
DEVICE_ID_LENGTH = 15

r = connect_to_redis()


def _get_device_id() -> str:
    cached = r.get(DEVICE_ID_KEY)
    if cached:
        return cached
    device_id = ''.join(
        secrets.choice(string.ascii_lowercase + string.digits)
        for _ in range(DEVICE_ID_LENGTH)
    )
    r.set(DEVICE_ID_KEY, device_id)
    return device_id


# Asset mimetypes counted individually in the payload. Anything outside
# this set still rolls into asset_count via the total.
ASSET_MIMETYPES = ('image', 'video', 'webpage')


def _get_asset_counts() -> dict[str, int]:
    try:
        rows = Asset.objects.filter(is_enabled=True).values_list(
            'mimetype', flat=True
        )
        counts = Counter(rows)
    except Exception as exc:
        # Telemetry must never crash the worker — DB unreachable, table
        # missing pre-migrate, etc., all degrade to zeros.
        logger.debug('asset count query failed: %s', exc)
        counts = Counter()

    result: dict[str, int] = {'asset_count': sum(counts.values())}
    for mt in ASSET_MIMETYPES:
        result[f'asset_{mt}_count'] = counts.get(mt, 0)
    return result


def _build_payload() -> dict[str, object]:
    # GA4 conventions: lowercase snake_case event + param names, boolean
    # values for `is_*` flags. Names are device-neutral now that x86 is
    # a first-class device_type — `device_type` is the board variant
    # (pi4-64, pi5, x86, ...) and `hardware_model` is /proc/cpuinfo's
    # free-text model.
    params: dict[str, object] = {
        # The released CalVer (e.g. '2026.8.0'), which is what the GA4
        # "Version distribution" report reads via its `version_name`
        # dimension. It was never sent — not by this payload and not by
        # the pre-#2798 one either, whose `Pi_Version` was the *hardware*
        # model — so that report showed ~3,700 devices as
        # "(not reported)" and could never have worked.
        #
        # Sourced from get_anthias_release() rather than an env var
        # precisely because env vars are the failure mode here: it reads
        # pyproject.toml's [project].version, which ships inside the
        # image, so it resolves in the celery container with no
        # additional plumbing (verified on the pi5 testbed: returns
        # '2026.7.3' inside anthias-anthias-celery-1).
        #
        # Falls back to 'unknown' rather than '' so the GA4 dimension
        # shows an explicit bucket instead of silently re-joining the
        # "(not reported)" pile if both version sources ever fail.
        'version_name': get_anthias_release() or 'unknown',
        'branch': str(get_git_branch()),
        'commit_short': str(get_git_short_hash()),
        'device_type': os.getenv('DEVICE_TYPE', 'unknown'),
        'hardware_model': parse_cpu_info().get('model', 'unknown'),
        'is_balena': is_balena_app(),
        'resolution': str(settings['resolution']),
        'audio_output': str(settings['audio_output']),
        'tls_enabled': bool(settings['use_ssl']),
    }
    params.update(_get_asset_counts())
    return {
        'client_id': _get_device_id(),
        'events': [{'name': 'device_active', 'params': params}],
    }


def send_telemetry() -> bool:
    """
    Emit a single GA4 ``device_active`` event for this device.
    Rate-limited to once per TELEMETRY_COOLDOWN_TTL via Redis so
    frequent celery restarts don't multiply traffic. Returns True if an
    event was sent.

    (The event was renamed from ``version`` to ``device_active`` in
    #2798; this docstring said ``version`` until it was corrected here.)
    """
    if settings['analytics_opt_out'] or is_ci():
        return False

    if r.get(TELEMETRY_COOLDOWN_KEY) is not None:
        return False

    url = (
        f'{ANALYTICS_URL}'
        f'?measurement_id={ANALYTICS_MEASURE_ID}'
        f'&api_secret={ANALYTICS_API_SECRET}'
    )
    try:
        requests_post(
            url,
            data=json.dumps(_build_payload()),
            headers={'content-type': 'application/json'},
            timeout=ANALYTICS_TIMEOUT,
        )
    except exceptions.RequestException as exc:
        # Don't set the cooldown — let the next beat tick retry.
        logger.debug('Telemetry POST failed: %s', exc)
        return False

    # Single SET with ex= so the value and its TTL are written
    # atomically — send_telemetry_task now runs under a soft time
    # limit, and a SoftTimeLimitExceeded landing between a separate
    # SET and EXPIRE would leave the cooldown key without a TTL,
    # silencing telemetry permanently.
    r.set(TELEMETRY_COOLDOWN_KEY, '1', ex=TELEMETRY_COOLDOWN_TTL)
    return True
