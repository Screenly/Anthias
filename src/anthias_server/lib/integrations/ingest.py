"""Provider-agnostic asset ingestion for import providers.

Every provider maps its own API's media object onto a handful of neutral
values (name, a URL or a downloadable file, a duration, a start/end
window) and then hands them here. This module owns the parts that must
behave identically no matter which platform the media came from:

* downloading an original file into the asset directory under a size cap,
* creating the ``Asset`` through the same ``CreateAssetSerializerV2``
  pipeline the web UI and REST API use (rename → normalise → duration
  probe), and
* idempotency — stamping ``Asset.metadata['import_source']`` and looking
  it up so a re-import returns the existing row instead of duplicating.

Keeping this in one place means a new provider only writes API-specific
field mapping, and every provider's imported assets are indistinguishable
from uploaded ones at playback time.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

from anthias_common.utils import validate_url
from anthias_server.api.helpers import AssetCreationError, persist_new_asset
from anthias_server.api.serializers.v2 import CreateAssetSerializerV2
from anthias_server.app.models import Asset
from anthias_server.settings import settings

from .base import ProviderImportError

# Same 5 GiB ceiling the Celery remote-video downloader enforces
# (``celery_tasks.REMOTE_VIDEO_MAX_BYTES``). Duplicated as a literal
# rather than imported so this request-path module doesn't pull in the
# whole Celery app.
MAX_DOWNLOAD_BYTES = 5 * 1024**3
_DOWNLOAD_CHUNK = 1024 * 1024
_DOWNLOAD_TIMEOUT_S = 120.0


def first_http_url(candidates: Iterable[Any]) -> str | None:
    """Return the first candidate that is a real http(s) URL.

    ``validate_url`` also accepts streaming schemes (rtsp/rtmp/…); this
    helper feeds webpage URIs and ``requests.get`` downloads, so it is
    restricted to http(s) to avoid picking a stream endpoint out of a
    field bag or handing an rtsp URL to ``requests``.
    """
    for value in candidates:
        if not isinstance(value, str) or not value:
            continue
        if validate_url(value) and urlparse(value).scheme in (
            'http',
            'https',
        ):
            return value
    return None


def _sanitise_ext(raw: str) -> str:
    """Reduce a candidate extension to a safe ``.<alnum>`` token.

    The value comes from a third-party API response or a URL path, so it
    is stripped to alphanumerics and capped — otherwise something like
    ``"/../../etc/passwd"`` could escape the asset directory when the
    staged filename is built.
    """
    cleaned = re.sub(r'[^A-Za-z0-9]', '', (raw or '').strip().lstrip('.'))
    cleaned = cleaned[:16]
    return f'.{cleaned}' if cleaned else ''


def file_ext_from(ext_hint: str | None, url: str) -> str:
    """Best on-disk extension for a downloaded original.

    Prefers an explicit provider hint ("mp4"), falls back to the
    extension on the download URL's path, and finally to none. Both
    sources are sanitised (see ``_sanitise_ext``) so a hostile value
    can't escape the asset directory when the staged filename is built.
    """
    ext = _sanitise_ext(ext_hint or '')
    if ext:
        return ext
    _stem, url_ext = os.path.splitext(url.split('?', 1)[0])
    return _sanitise_ext(url_ext)


def duration_or_default(raw: Any) -> int:
    """Return a positive integer duration (seconds), or the device default.

    Providers pass whatever their API gave (int/float/None); a missing or
    non-positive value falls back to the operator's configured
    ``default_duration``.
    """
    from anthias_server.settings import settings

    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    return int(settings['default_duration'])


def default_window() -> tuple[datetime, datetime]:
    """The always-on window used when a provider gives no schedule.

    Modelled as "now → +10 years" — the same wide window the schedule UI
    treats as unbounded.
    """
    now = datetime.now(timezone.utc)
    return now, now + timedelta(days=3650)


def find_imported_asset(provider_key: str, remote_id: str) -> Asset | None:
    """Return a previously-imported asset for this provider+remote id."""
    return Asset.objects.filter(
        metadata__import_source__provider=provider_key,
        metadata__import_source__remote_id=str(remote_id),
    ).first()


def _stamp_import_source(
    asset: Asset, provider_key: str, remote_id: str
) -> None:
    metadata = dict(asset.metadata or {})
    metadata['import_source'] = {
        'provider': provider_key,
        'remote_id': str(remote_id),
    }
    asset.metadata = metadata
    asset.save(update_fields=['metadata'])


def _stringify_errors(errors: Any) -> str:
    """Collapse a serializer error bag into one operator-facing line."""
    if isinstance(errors, str):
        return errors
    if isinstance(errors, dict):
        for value in errors.values():
            if isinstance(value, (list, tuple)) and value:
                return str(value[0])
            if value:
                return str(value)
    if isinstance(errors, (list, tuple)) and errors:
        return str(errors[0])
    return 'Anthias rejected the imported asset.'


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _create_via_serializer(
    *,
    uri: str,
    ext: str | None,
    mimetype: str,
    name: str,
    start_date: datetime,
    end_date: datetime,
    duration: int,
    enable: bool,
) -> Asset:
    data: dict[str, Any] = {
        'name': name,
        'uri': uri,
        'mimetype': mimetype,
        'start_date': start_date,
        'end_date': end_date,
        'duration': duration,
        'is_enabled': enable,
    }
    if ext:
        data['ext'] = ext
    serializer = CreateAssetSerializerV2(data=data, unique_name=True)
    # ``prepare_asset`` raises ``AssetCreationError`` (not a DRF
    # ValidationError) for reachability / duration failures, which
    # ``is_valid()`` does not catch — hence the explicit guard, matching
    # ``AssetListViewV2.post``.
    try:
        valid = serializer.is_valid()
    except AssetCreationError as error:
        raise ProviderImportError(_stringify_errors(error.errors))
    if not valid:
        raise ProviderImportError(_stringify_errors(serializer.errors))
    return persist_new_asset(serializer)


def _download_to_assetdir(
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    ext: str,
    auth_host: str | None,
) -> str:
    assetdir = settings['assetdir']
    staged = os.path.join(assetdir, f'.import-{uuid.uuid4().hex}{ext}')
    part = f'{staged}.part'
    # Only attach the provider's auth headers when the download stays on
    # the host they're scoped to — original-file URLs are frequently
    # pre-signed CDN links on a different host, and forwarding the API
    # token there would leak the credential and can break the signed
    # request. No ``auth_host`` (or a foreign host) → send no auth.
    send_auth = bool(auth_host) and (
        urlparse(url).netloc.lower() == (auth_host or '').lower()
    )
    request_headers = headers if send_auth else {}
    try:
        with session.get(
            url,
            headers=request_headers,
            stream=True,
            timeout=_DOWNLOAD_TIMEOUT_S,
        ) as response:
            if not response.ok:
                raise ProviderImportError(
                    f'Download failed ({response.status_code}).'
                )
            written = 0
            with open(part, 'wb') as handle:
                for chunk in response.iter_content(_DOWNLOAD_CHUNK):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise ProviderImportError(
                            'File exceeds the 5 GiB import size limit.'
                        )
                    handle.write(chunk)
        if written == 0:
            raise ProviderImportError('The provider returned an empty file.')
        # Atomic move into the final staged name only once the whole body
        # landed, so a truncated download can't be handed to the serializer.
        os.replace(part, staged)
        return staged
    except Exception:
        _safe_unlink(part)
        _safe_unlink(staged)
        raise


def create_webpage_asset(
    *,
    provider_key: str,
    remote_id: str,
    name: str,
    url: str,
    duration: int,
    start_date: datetime,
    end_date: datetime,
    enable: bool,
) -> Asset:
    """Create a webpage asset whose ``uri`` is the destination URL."""
    asset = _create_via_serializer(
        uri=url,
        ext=None,
        mimetype='webpage',
        name=name,
        start_date=start_date,
        end_date=end_date,
        duration=duration,
        enable=enable,
    )
    _stamp_import_source(asset, provider_key, remote_id)
    return asset


def create_file_asset(
    *,
    session: requests.Session,
    provider_key: str,
    remote_id: str,
    name: str,
    mimetype: str,
    file_url: str,
    ext: str,
    duration: int,
    start_date: datetime,
    end_date: datetime,
    enable: bool,
    headers: dict[str, str] | None = None,
    auth_host: str | None = None,
) -> Asset:
    """Download an original file and create an image/video asset.

    The file is staged inside the asset directory and handed to
    ``CreateAssetSerializerV2`` like a local upload. ``duration`` must be
    0 for video (probed server-side); images carry the provider's value.

    ``headers`` are attached to the download only when ``file_url`` is on
    ``auth_host`` — so an API token is never forwarded to a pre-signed CDN
    original on a different host.
    """
    staged_path = _download_to_assetdir(
        session, file_url, headers or {}, ext, auth_host
    )
    try:
        asset = _create_via_serializer(
            uri=staged_path,
            ext=ext,
            mimetype=mimetype,
            name=name,
            start_date=start_date,
            end_date=end_date,
            duration=duration,
            enable=enable,
        )
    except Exception:
        # ``prepare_asset`` renames the staged file into place only once
        # validation passes, so on failure it's still at ``staged_path``
        # — remove it rather than leaving an orphan for the hourly sweep.
        _safe_unlink(staged_path)
        raise
    _stamp_import_source(asset, provider_key, remote_id)
    return asset
