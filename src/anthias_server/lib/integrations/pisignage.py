"""piSignage import provider (REST).

piSignage is hosted per-account on a subdomain (``<account>.pisignage.com``)
and authenticates by exchanging credentials for a JWT at ``POST /session``,
then passing it as an ``x-access-token`` header. Because both the subdomain
and the login are needed, the operator's "token" is
``subdomain:email:password`` (email may be a username; password may itself
contain ``:``).

Media is the account's uploaded files (``GET /files``): images and videos
are imported, audio is skipped. Files download from the same host as the
API, so the token is attached to the download (scoped to that host by the
shared ingest layer). Reuses the ingest/http layer for download + asset
creation + idempotency.

TODO(confirm-with-live-token): piSignage web-link assets are not part of
``/files`` and aren't imported here; the media ``path`` → download URL
mapping (``https://<sub>.pisignage.com<path>``) and whether the media host
needs the token are worth confirming against a live hosted account.
"""

from __future__ import annotations

from typing import Any, Iterator
from urllib.parse import quote

import requests

from . import ingest
from .base import (
    ImportOutcome,
    ImportProvider,
    ProviderImportError,
    RemoteMediaItem,
)
from .http import new_import_session

PROVIDER_KEY = 'pisignage'
_PISIGNAGE_DOMAIN = 'pisignage.com'

_VALIDATE_TIMEOUT_S = 15.0
_LIST_TIMEOUT_S = 30.0

_session = new_import_session()


def _base_url(subdomain: str) -> str:
    return f'https://{subdomain}.{_PISIGNAGE_DOMAIN}/api'


def _media_host(subdomain: str) -> str:
    return f'{subdomain}.{_PISIGNAGE_DOMAIN}'


def _parse_token(token: str) -> tuple[str, str, str]:
    """Split ``subdomain:email:password`` (password may contain ``:``)."""
    parts = (token or '').split(':', 2)
    if len(parts) != 3 or not parts[0].strip() or not parts[1].strip():
        raise ProviderImportError(
            'piSignage token must be "subdomain:email:password".'
        )
    return parts[0].strip(), parts[1].strip(), parts[2]


def _login(subdomain: str, email: str, password: str) -> str | None:
    """Exchange credentials for a JWT, or None if they're rejected."""
    response = _session.post(
        f'{_base_url(subdomain)}/session',
        json={'email': email, 'password': password, 'getToken': True},
        timeout=_VALIDATE_TIMEOUT_S,
    )
    if response.status_code == 401:
        return None
    response.raise_for_status()
    token = response.json().get('token')
    return token if isinstance(token, str) and token else None


def _login_or_raise(token: str) -> tuple[str, dict[str, str]]:
    """Return (subdomain, auth-headers) after logging in.

    Raises ``ProviderImportError`` for a malformed token or rejected
    credentials; transport errors propagate.
    """
    subdomain, email, password = _parse_token(token)
    jwt = _login(subdomain, email, password)
    if not jwt:
        raise ProviderImportError('piSignage rejected these credentials.')
    return subdomain, {'x-access-token': jwt}


def _map_type(raw: Any) -> str | None:
    value = raw.lower() if isinstance(raw, str) else ''
    if value.startswith('image'):
        return 'image'
    if value.startswith('video'):
        return 'video'
    if value.startswith('audio'):
        return 'audio'
    return None


def _type_from_name(filename: str) -> str | None:
    ext = ingest.file_ext_from(None, filename).lstrip('.').lower()
    if ext in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'):
        return 'image'
    if ext in ('mp4', 'mov', 'mkv', 'webm', 'avi', 'flv', 'm4v'):
        return 'video'
    return None


def _duration(dbdata: dict[str, Any]) -> int | None:
    raw = dbdata.get('duration')
    if isinstance(raw, (int, str)):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


class PiSignageProvider(ImportProvider):
    key = PROVIDER_KEY
    label = 'piSignage'
    description = (
        'Copy images and videos from a piSignage account into this player.'
    )
    token_help = (
        'Enter your piSignage account as "subdomain:email:password". The '
        'subdomain is the "<name>" in <name>.pisignage.com, followed by your '
        'login email (or username) and password. It is used only for this '
        'import and is never stored.'
    )

    # -- token / listing ---------------------------------------------------

    def validate_token(self, token: str) -> bool:
        try:
            subdomain, email, password = _parse_token(token)
        except ProviderImportError:
            return False
        return _login(subdomain, email, password) is not None

    def list_media(
        self, token: str, *, workspace: str | None = None
    ) -> list[RemoteMediaItem]:
        try:
            subdomain, headers = _login_or_raise(token)
        except ProviderImportError as error:
            # list_media's caller handles transport errors, not
            # ProviderImportError — surface bad creds as a controlled 502.
            raise requests.RequestException(error.user_message) from error

        response = _session.get(
            f'{_base_url(subdomain)}/files',
            headers=headers,
            timeout=_LIST_TIMEOUT_S,
        )
        response.raise_for_status()
        data = (response.json() or {}).get('data') or {}
        return list(self._items_from_listing(data))

    def _items_from_listing(
        self, data: dict[str, Any]
    ) -> Iterator[RemoteMediaItem]:
        # ``dbdata`` (hosted service) carries the type; fall back to the
        # bare ``files`` name list and infer the type from the extension.
        dbdata = data.get('dbdata') or []
        seen: set[str] = set()
        for entry in dbdata:
            if not isinstance(entry, dict):
                continue
            raw_name = entry.get('name')
            if not raw_name:
                continue
            # Coerce to str so the de-dupe check against the string-only
            # ``files`` list below is reliable.
            name = str(raw_name)
            seen.add(name)
            yield self._item(name, _map_type(entry.get('type')))
        for name in data.get('files') or []:
            if isinstance(name, str) and name not in seen:
                yield self._item(name, _type_from_name(name))

    def _item(self, name: str, media_type: str | None) -> RemoteMediaItem:
        importable = media_type in ('image', 'video')
        return RemoteMediaItem(
            remote_id=name,
            name=name,
            media_type=media_type or 'unsupported',
            importable=importable,
            skip_reason=None
            if importable
            else "This piSignage file isn't a supported image or video.",
        )

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

        subdomain, headers = _login_or_raise(token)
        detail = self._get_file(subdomain, headers, remote_id)

        dbdata = detail.get('dbdata') or {}
        media_type = _map_type(detail.get('type')) or _map_type(
            dbdata.get('type')
        )
        if media_type not in ('image', 'video'):
            return ImportOutcome(
                success=False,
                skipped=True,
                reason="This piSignage file isn't a supported image or video.",
            )

        path = detail.get('path')
        if not isinstance(path, str) or not path:
            return ImportOutcome(
                success=False,
                skipped=True,
                reason='piSignage did not expose a download path for this file.',
            )
        # Guarantee a single leading slash so the URL is well-formed even if
        # the API ever returns a path without one.
        file_url = f'https://{_media_host(subdomain)}/{path.lstrip("/")}'
        start_date, end_date = ingest.default_window()
        asset = ingest.create_file_asset(
            session=_session,
            headers=headers,
            # Media is served from the same host as the API, so the token is
            # attached to the download (scoped to that host).
            auth_host=_media_host(subdomain),
            provider_key=PROVIDER_KEY,
            remote_id=remote_id,
            name=str(detail.get('name') or remote_id),
            mimetype=media_type,
            file_url=file_url,
            ext=ingest.file_ext_from(None, file_url),
            # Video duration is probed server-side; images use piSignage's
            # value or the device default.
            duration=(
                0
                if media_type == 'video'
                else ingest.duration_or_default(_duration(dbdata))
            ),
            start_date=start_date,
            end_date=end_date,
            enable=enable,
        )
        return ImportOutcome(success=True, asset_id=asset.asset_id)

    def _get_file(
        self, subdomain: str, headers: dict[str, str], filename: str
    ) -> dict[str, Any]:
        response = _session.get(
            f'{_base_url(subdomain)}/files/{quote(filename, safe="")}',
            headers=headers,
            timeout=_LIST_TIMEOUT_S,
        )
        if response.status_code == 404:
            raise ProviderImportError('File no longer exists in piSignage.')
        response.raise_for_status()
        return (response.json() or {}).get('data') or {}
