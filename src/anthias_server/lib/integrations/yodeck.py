"""Yodeck import provider (REST, ``https://app.yodeck.com/api/v2``).

Pulls media out of a Yodeck account and recreates each item as an
Anthias ``Asset``:

* ``webpage`` → a webpage asset whose ``uri`` is the destination URL.
* ``image`` / ``video`` → the original file is downloaded into the asset
  directory and handed to ``CreateAssetSerializerV2`` exactly like a
  local upload, so it flows through the same rename + normalise +
  duration-probe pipeline the web UI and API already use.
* ``audio`` / ``document`` → surfaced as skipped-with-reason (Anthias has
  no viewer path for either).

Re-imports are idempotent: each created asset is stamped with its Yodeck
id in ``Asset.metadata['import_source']`` and a second run of the same
item returns the existing asset instead of duplicating it.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from anthias_common.utils import validate_url
from anthias_server.api.helpers import AssetCreationError, persist_new_asset
from anthias_server.api.serializers.v2 import CreateAssetSerializerV2
from anthias_server.app.models import Asset
from anthias_server.settings import settings

from .base import (
    ImportOutcome,
    ImportProvider,
    ProviderImportError,
    RemoteMediaItem,
)

logger = logging.getLogger(__name__)

YODECK_API_BASE = 'https://app.yodeck.com/api/v2'
# Host the API token is scoped to. Original-file URLs are frequently
# pre-signed CDN links on a different host; sending the Yodeck token to
# those would leak the credential and can break the signed request, so
# auth is attached only when downloading from Yodeck itself.
_YODECK_HOST = urlparse(YODECK_API_BASE).netloc.lower()
PROVIDER_KEY = 'yodeck'

# Anthias renders images, videos and web pages. Yodeck audio/document
# media have no viewer path, so they're surfaced as skipped-with-reason
# rather than half-imported.
_IMPORTABLE_TYPES = ('image', 'video', 'webpage')
_UNSUPPORTED_TYPES = ('audio', 'document')
_ALL_TYPES = _IMPORTABLE_TYPES + _UNSUPPORTED_TYPES

# Same 5 GiB ceiling the Celery remote-video downloader enforces
# (``celery_tasks.REMOTE_VIDEO_MAX_BYTES``). Duplicated as a literal
# rather than imported so this request-path module doesn't pull in the
# whole Celery app.
_MAX_DOWNLOAD_BYTES = 5 * 1024**3
_DOWNLOAD_CHUNK = 1024 * 1024

_LIST_PAGE_SIZE = 100
_VALIDATE_TIMEOUT_S = 10.0
_LIST_TIMEOUT_S = 30.0
_DOWNLOAD_TIMEOUT_S = 120.0

# Candidate fields that may carry a downloadable ORIGINAL file URL for an
# image/video, in priority order — first at the top level, then inside
# ``arguments``. ``arguments.download_from_url`` is Yodeck's download link
# for the original and is present for uploaded (``source: local``) media
# too — it points at Yodeck (app.yodeck.com) and 302-redirects to a signed
# S3 URL, so the token is attached for the Yodeck host and dropped by
# ``requests`` on the cross-host redirect. The extra keys cover forward-
# compatible alternatives; if none resolves, the item is skipped with a
# clear reason rather than guessed at.
_FILE_URL_KEYS = ('file', 'media_file', 'source_url', 'original_url', 'url')
_ARGUMENT_FILE_KEYS = ('download_from_url', 'source_url', 'file', 'url')

# Candidate fields carrying a webpage's destination URL (top level, then
# inside ``arguments``).
_WEBPAGE_URL_KEYS = ('url', 'website_url', 'source_url', 'link', 'address')

# Deliberately NOT ``AnthiasSession``: these calls hit a third-party
# vendor's API during a migration away from them, and a self-identifying
# ``Anthias/<version>`` User-Agent invites vendor-side blocking. A
# neutral browser-like UA lets import traffic look like an ordinary API
# client. (Same blend-in rationale as the anti-bot probe in
# ``anthias_common.utils.url_fails``.)
_IMPORT_USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
_session = requests.Session()
_session.headers['User-Agent'] = _IMPORT_USER_AGENT


def _auth_headers(token: str) -> dict[str, str]:
    # Yodeck API tokens are issued as "<label>:<secret>" and passed in
    # the Authorization header. "Token <value>" is the DRF-style scheme
    # Yodeck's stack uses; a wrong scheme surfaces immediately via
    # validate_token() as an invalid token rather than a silent 401 mid
    # import.
    return {'Authorization': f'Token {token}'}


def _first_http_url(candidates: Iterator[Any] | list[Any]) -> str | None:
    """Return the first candidate that is a real http(s) URL.

    ``validate_url`` also accepts streaming schemes (rtsp/rtmp/…); this
    helper feeds webpage URIs and ``requests.get`` downloads, so it is
    restricted to http(s) to avoid picking a stream endpoint out of the
    argument bag or handing an rtsp URL to ``requests``.
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


def _webpage_url(detail: dict[str, Any]) -> str | None:
    arguments = detail.get('arguments') or {}
    top = (detail.get(k) for k in _WEBPAGE_URL_KEYS)
    inner = (arguments.get(k) for k in _WEBPAGE_URL_KEYS)
    # Fall back to any http(s) value anywhere in ``arguments`` — Yodeck
    # webpage media keep the destination URL there under a key we may not
    # have enumerated above.
    return _first_http_url([*top, *inner, *arguments.values()])


def _file_url(detail: dict[str, Any]) -> str | None:
    arguments = detail.get('arguments') or {}
    top = (detail.get(k) for k in _FILE_URL_KEYS)
    inner = (arguments.get(k) for k in _ARGUMENT_FILE_KEYS)
    return _first_http_url([*top, *inner])


def _is_internal_yodeck_url(url: str) -> bool:
    """True when a webpage URL points back at Yodeck-hosted content.

    Yodeck apps/widgets (weather, RSS, dashboards) surface as webpage
    media whose URL is rendered inside Yodeck. Those only work within
    Yodeck, so they're filtered out of the import rather than recreated
    as broken webpage assets.
    """
    host = urlparse(url).netloc.lower()
    return host == 'yodeck.com' or host.endswith('.yodeck.com')


def _sanitise_ext(raw: str) -> str:
    """Reduce a candidate extension to a safe ``.<alnum>`` token.

    The value comes from a third-party API (Yodeck's ``file_extension``)
    or a URL path, so it is stripped to alphanumerics and capped —
    otherwise ``"/../../etc/passwd"`` could escape the asset directory
    when the staged filename is built.
    """
    cleaned = re.sub(r'[^A-Za-z0-9]', '', (raw or '').strip().lstrip('.'))
    cleaned = cleaned[:16]
    return f'.{cleaned}' if cleaned else ''


def _file_ext(detail: dict[str, Any], url: str) -> str:
    """Best on-disk extension for a downloaded original.

    Prefers Yodeck's ``file_extension`` field ("mp4"), falls back to the
    extension on the download URL's path, and finally to no extension.
    Both sources are sanitised (see ``_sanitise_ext``) so
    ``CreateAssetSerializerV2`` renames the file to a safe
    ``<asset_id>.mp4`` and ffprobe can identify the container.
    """
    ext = _sanitise_ext(detail.get('file_extension') or '')
    if ext:
        return ext
    _stem, url_ext = os.path.splitext(url.split('?', 1)[0])
    return _sanitise_ext(url_ext)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _availability_window(detail: dict[str, Any]) -> tuple[datetime, datetime]:
    """Map Yodeck's availability schedule onto Anthias start/end dates.

    When the schedule is enabled and carries both bounds we honour them;
    otherwise the asset is always-on, modelled as "now → +10 years" (the
    same wide window the schedule UI treats as unbounded).
    """
    now = datetime.now(timezone.utc)
    far_future = now + timedelta(days=3650)
    schedule = detail.get('availability_schedule') or {}
    if schedule.get('enable'):
        start = _parse_dt(schedule.get('available_after')) or now
        end = _parse_dt(schedule.get('available_before')) or far_future
        if end > start:
            return start, end
    return now, far_future


def _default_duration(detail: dict[str, Any]) -> int:
    raw = detail.get('default_duration')
    duration = 0
    if isinstance(raw, (int, str)):
        try:
            duration = int(raw)
        except (TypeError, ValueError):
            duration = 0
    if duration > 0:
        return duration
    return int(settings['default_duration'])


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


def _find_imported_asset(remote_id: str) -> Asset | None:
    return Asset.objects.filter(
        metadata__import_source__provider=PROVIDER_KEY,
        metadata__import_source__remote_id=str(remote_id),
    ).first()


def _stamp_import_source(asset: Asset, remote_id: str) -> None:
    metadata = dict(asset.metadata or {})
    metadata['import_source'] = {
        'provider': PROVIDER_KEY,
        'remote_id': str(remote_id),
    }
    asset.metadata = metadata
    asset.save(update_fields=['metadata'])


class YodeckProvider(ImportProvider):
    key = PROVIDER_KEY
    label = 'Yodeck'
    description = (
        'Copy images, videos and web pages from a Yodeck account into '
        'this player using a Yodeck API token.'
    )
    token_help = (
        'Create an API token in Yodeck under Account Settings → Advanced '
        'Settings → API Tokens. Enter it as "<label>:<token>" — the name '
        'you gave the token, a colon, then the copied token value (e.g. '
        '"mylabel:XXXXXXXX"). It is used only for this import and is never '
        'stored.'
    )

    # -- token / listing ---------------------------------------------------

    def validate_token(self, token: str) -> bool:
        response = _session.get(
            f'{YODECK_API_BASE}/media/',
            headers=_auth_headers(token),
            params={'limit': 1},
            timeout=_VALIDATE_TIMEOUT_S,
        )
        if response.status_code == 200:
            return True
        if response.status_code in (401, 403):
            return False
        response.raise_for_status()
        return False

    def list_media(
        self, token: str, *, workspace: str | None = None
    ) -> list[RemoteMediaItem]:
        # List each media_type with its filter rather than one mixed
        # page: it lets us tag every row with a known type without
        # depending on the exact field name Yodeck uses for type inside a
        # list row (only ``id`` + ``name`` are read there).
        items: list[RemoteMediaItem] = []
        for media_type in _ALL_TYPES:
            importable = media_type in _IMPORTABLE_TYPES
            skip_reason = None
            if not importable:
                skip_reason = (
                    f"{media_type.capitalize()} media isn't supported by "
                    'Anthias and was skipped.'
                )
            for obj in self._paginate(token, media_type, workspace):
                remote_id = obj.get('id')
                if remote_id is None:
                    continue
                items.append(
                    RemoteMediaItem(
                        remote_id=str(remote_id),
                        name=(obj.get('name') or f'Yodeck media {remote_id}'),
                        media_type=media_type,
                        importable=importable,
                        skip_reason=skip_reason,
                        raw=obj,
                    )
                )
        return items

    def _paginate(
        self, token: str, media_type: str, workspace: str | None
    ) -> Iterator[dict[str, Any]]:
        offset = 0
        params: dict[str, Any] = {
            'media_type': media_type,
            'limit': _LIST_PAGE_SIZE,
        }
        if workspace:
            params['workspace'] = workspace
        while True:
            response = _session.get(
                f'{YODECK_API_BASE}/media/',
                headers=_auth_headers(token),
                params={**params, 'offset': offset},
                timeout=_LIST_TIMEOUT_S,
            )
            response.raise_for_status()
            body = response.json()
            results = body.get('results') or []
            for item in results:
                if isinstance(item, dict):
                    yield item
            # Stop when the page came back empty or the API reports no
            # ``next`` cursor — either signals the last page.
            if not results or not body.get('next'):
                break
            offset += len(results)

    # -- import ------------------------------------------------------------

    def import_item(
        self, token: str, remote_id: str, *, enable: bool = True
    ) -> ImportOutcome:
        existing = _find_imported_asset(remote_id)
        if existing is not None:
            return ImportOutcome(
                success=True,
                asset_id=existing.asset_id,
                skipped=True,
                reason='Already imported.',
            )

        detail = self._get_detail(token, remote_id)
        media_type = (detail.get('media_origin') or {}).get('type')

        if media_type in _UNSUPPORTED_TYPES:
            return ImportOutcome(
                success=False,
                skipped=True,
                reason=(
                    f"{str(media_type).capitalize()} media isn't supported "
                    'by Anthias.'
                ),
            )
        if media_type not in _IMPORTABLE_TYPES:
            return ImportOutcome(
                success=False,
                skipped=True,
                reason=f'Unsupported media type: {media_type or "unknown"}.',
            )

        name = (detail.get('name') or f'Yodeck media {remote_id}').strip()
        start_date, end_date = _availability_window(detail)

        if media_type == 'webpage':
            url = _webpage_url(detail)
            if not url:
                return ImportOutcome(
                    success=False,
                    skipped=True,
                    reason='Could not determine the web page URL from Yodeck.',
                )
            if _is_internal_yodeck_url(url):
                # A "webpage" that resolves to Yodeck-hosted content is an
                # internally-rendered app/widget (weather, RSS, dashboards,
                # …). Its URL only works inside Yodeck, so importing it
                # would create a broken asset — skip with a clear reason.
                return ImportOutcome(
                    success=False,
                    skipped=True,
                    reason=(
                        'This is internally-hosted Yodeck content (an app '
                        'or widget) and cannot be imported.'
                    ),
                )
            asset = self._create_via_serializer(
                uri=url,
                ext=None,
                mimetype='webpage',
                name=name,
                start_date=start_date,
                end_date=end_date,
                duration=_default_duration(detail),
                enable=enable,
            )
        else:
            imported = self._import_file(
                token, detail, media_type, name, start_date, end_date, enable
            )
            if imported is None:
                return ImportOutcome(
                    success=False,
                    skipped=True,
                    reason=(
                        f"Yodeck didn't expose a downloadable original for "
                        f'this {media_type}; re-upload it manually.'
                    ),
                )
            asset = imported

        _stamp_import_source(asset, remote_id)
        return ImportOutcome(success=True, asset_id=asset.asset_id)

    def _get_detail(self, token: str, remote_id: str) -> dict[str, Any]:
        response = _session.get(
            f'{YODECK_API_BASE}/media/{remote_id}/',
            headers=_auth_headers(token),
            timeout=_LIST_TIMEOUT_S,
        )
        if response.status_code == 404:
            raise ProviderImportError('Media no longer exists in Yodeck.')
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ProviderImportError('Unexpected response from Yodeck.')
        return body

    def _import_file(
        self,
        token: str,
        detail: dict[str, Any],
        media_type: str,
        name: str,
        start_date: datetime,
        end_date: datetime,
        enable: bool,
    ) -> Asset | None:
        url = _file_url(detail)
        if not url:
            return None
        staged_path, ext = self._download_to_assetdir(token, url, detail)
        try:
            return self._create_via_serializer(
                uri=staged_path,
                ext=ext,
                mimetype=media_type,
                name=name,
                start_date=start_date,
                end_date=end_date,
                # Video duration is probed server-side (the serializer
                # requires 0 on input); images carry Yodeck's duration.
                duration=0
                if media_type == 'video'
                else _default_duration(detail),
                enable=enable,
            )
        except Exception:
            # ``prepare_asset`` renames the staged file into place only
            # once validation passes, so on failure it's still at
            # ``staged_path`` — remove it rather than leaving an orphan
            # for the hourly cleanup sweep.
            _safe_unlink(staged_path)
            raise

    def _download_to_assetdir(
        self, token: str, url: str, detail: dict[str, Any]
    ) -> tuple[str, str]:
        ext = _file_ext(detail, url)
        assetdir = settings['assetdir']
        staged = os.path.join(assetdir, f'.import-{uuid.uuid4().hex}{ext}')
        part = f'{staged}.part'
        # Only attach the Yodeck token when the download stays on the
        # Yodeck host — never forward it to a pre-signed CDN original.
        download_headers = (
            _auth_headers(token)
            if urlparse(url).netloc.lower() == _YODECK_HOST
            else {}
        )
        try:
            with _session.get(
                url,
                headers=download_headers,
                stream=True,
                timeout=_DOWNLOAD_TIMEOUT_S,
            ) as response:
                if not response.ok:
                    raise ProviderImportError(
                        f'Yodeck download failed ({response.status_code}).'
                    )
                written = 0
                with open(part, 'wb') as handle:
                    for chunk in response.iter_content(_DOWNLOAD_CHUNK):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > _MAX_DOWNLOAD_BYTES:
                            raise ProviderImportError(
                                'File exceeds the 5 GiB import size limit.'
                            )
                        handle.write(chunk)
            if written == 0:
                raise ProviderImportError('Yodeck returned an empty file.')
            # Atomic move into the final staged name only once the whole
            # body landed, so a truncated download can't be handed to the
            # serializer.
            os.replace(part, staged)
            return staged, ext
        except Exception:
            _safe_unlink(part)
            _safe_unlink(staged)
            raise

    def _create_via_serializer(
        self,
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
        # ``is_valid()`` does not catch — hence the explicit guard,
        # matching ``AssetListViewV2.post``.
        try:
            valid = serializer.is_valid()
        except AssetCreationError as error:
            raise ProviderImportError(_stringify_errors(error.errors))
        if not valid:
            raise ProviderImportError(_stringify_errors(serializer.errors))
        return persist_new_asset(serializer)
