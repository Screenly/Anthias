import json
import logging
import os
import secrets
import string
from collections import Counter

from requests import exceptions
from requests import post as requests_post

from anthias_app.models import Asset
from lib.device_helper import parse_cpu_info
from lib.diagnostics import get_git_branch, get_git_short_hash
from lib.utils import connect_to_redis, is_balena_app, is_ci, is_docker
from settings import settings

ANALYTICS_MEASURE_ID = 'G-S3VX8HTPK7'
ANALYTICS_API_SECRET = 'G8NcBpRIS9qBsOj3ODK8gw'
ANALYTICS_URL = 'https://www.google-analytics.com/mp/collect'
ANALYTICS_TIMEOUT = 5

# Anthias's celerybeat schedule lives in /tmp (`--schedule
# /tmp/celerybeat-schedule` in docker-compose.yml.tmpl), so the daily
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
        logging.debug('asset count query failed: %s', exc)
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
        'branch': str(get_git_branch()),
        'commit_short': str(get_git_short_hash()),
        'device_type': os.getenv('DEVICE_TYPE', 'unknown'),
        'hardware_model': parse_cpu_info().get('model', 'unknown'),
        'is_balena': is_balena_app(),
        'is_docker': is_docker(),
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
    Emit a single GA4 `version` event for this device. Rate-limited to
    once per TELEMETRY_COOLDOWN_TTL via Redis so frequent celery
    restarts don't multiply traffic. Returns True if an event was sent.
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
        logging.debug('Telemetry POST failed: %s', exc)
        return False

    r.set(TELEMETRY_COOLDOWN_KEY, '1')
    r.expire(TELEMETRY_COOLDOWN_KEY, TELEMETRY_COOLDOWN_TTL)
    return True
