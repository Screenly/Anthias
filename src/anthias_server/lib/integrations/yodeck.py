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

Only the Yodeck-specific API shape lives here (endpoints, auth header,
pagination, and mapping a media object's fields). The provider-agnostic
work — downloading the original, creating the asset, and idempotency —
is delegated to :mod:`anthias_server.lib.integrations.ingest`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import urlparse

from . import ingest
from .base import (
    ImportOutcome,
    ImportProvider,
    ProviderImportError,
    RemoteMediaItem,
)
from .http import new_import_session

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

_LIST_PAGE_SIZE = 100
_VALIDATE_TIMEOUT_S = 10.0
_LIST_TIMEOUT_S = 30.0

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

# Neutral (non-Anthias) session — see ``integrations.http`` for why import
# traffic must not carry the ``Anthias/<version>`` User-Agent.
_session = new_import_session()


def _auth_headers(token: str) -> dict[str, str]:
    # Yodeck API tokens are issued as "<label>:<secret>" and passed in
    # the Authorization header. "Token <value>" is the DRF-style scheme
    # Yodeck's stack uses; a wrong scheme surfaces immediately via
    # validate_token() as an invalid token rather than a silent 401 mid
    # import.
    return {'Authorization': f'Token {token}'}


def _webpage_url(detail: dict[str, Any]) -> str | None:
    arguments = detail.get('arguments') or {}
    top = (detail.get(k) for k in _WEBPAGE_URL_KEYS)
    inner = (arguments.get(k) for k in _WEBPAGE_URL_KEYS)
    # Fall back to any http(s) value anywhere in ``arguments`` — Yodeck
    # webpage media keep the destination URL there under a key we may not
    # have enumerated above.
    return ingest.first_http_url([*top, *inner, *arguments.values()])


def _file_url(detail: dict[str, Any]) -> str | None:
    arguments = detail.get('arguments') or {}
    top = (detail.get(k) for k in _FILE_URL_KEYS)
    inner = (arguments.get(k) for k in _ARGUMENT_FILE_KEYS)
    return ingest.first_http_url([*top, *inner])


def _is_internal_yodeck_url(url: str) -> bool:
    """True when a webpage URL points back at Yodeck-hosted content.

    Yodeck apps/widgets (weather, RSS, dashboards) surface as webpage
    media whose URL is rendered inside Yodeck. Those only work within
    Yodeck, so they're filtered out of the import rather than recreated
    as broken webpage assets.
    """
    host = urlparse(url).netloc.lower()
    return host == 'yodeck.com' or host.endswith('.yodeck.com')


def _file_ext(detail: dict[str, Any], url: str) -> str:
    return ingest.file_ext_from(detail.get('file_extension'), url)


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
    otherwise the asset is always-on (``ingest.default_window()``).
    """
    now, far_future = ingest.default_window()
    schedule = detail.get('availability_schedule') or {}
    if schedule.get('enable'):
        start = _parse_dt(schedule.get('available_after')) or now
        end = _parse_dt(schedule.get('available_before')) or far_future
        if end > start:
            return start, end
    return now, far_future


def _default_duration(detail: dict[str, Any]) -> int:
    from anthias_server.settings import settings

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
        existing = ingest.find_imported_asset(PROVIDER_KEY, remote_id)
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
            asset = ingest.create_webpage_asset(
                provider_key=PROVIDER_KEY,
                remote_id=remote_id,
                name=name,
                url=url,
                duration=_default_duration(detail),
                start_date=start_date,
                end_date=end_date,
                enable=enable,
            )
        else:
            file_url = _file_url(detail)
            if not file_url:
                return ImportOutcome(
                    success=False,
                    skipped=True,
                    reason=(
                        f"Yodeck didn't expose a downloadable original for "
                        f'this {media_type}; re-upload it manually.'
                    ),
                )
            asset = ingest.create_file_asset(
                session=_session,
                headers=_auth_headers(token),
                # Scope the Yodeck token to the Yodeck host — never forward
                # it to a pre-signed CDN original.
                auth_host=_YODECK_HOST,
                provider_key=PROVIDER_KEY,
                remote_id=remote_id,
                name=name,
                mimetype=media_type,
                file_url=file_url,
                ext=_file_ext(detail, file_url),
                # Video duration is probed server-side (the serializer
                # requires 0 on input); images carry Yodeck's duration.
                duration=(
                    0 if media_type == 'video' else _default_duration(detail)
                ),
                start_date=start_date,
                end_date=end_date,
                enable=enable,
            )

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
