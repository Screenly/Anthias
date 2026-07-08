"""Xibo import provider — REST (Xibo CMS API).

Xibo runs per-CMS (Xibo Cloud gives each account a host such as
``<name>.xibosignage.com``) and authenticates with OAuth2
client-credentials: an API application's ``client_id`` / ``client_secret``
are exchanged for a Bearer token at ``POST /api/authorize/access_token``.
Because the CMS host and both credentials are needed, the operator's
"token" is ``host:client_id:client_secret`` (hostname only, no scheme).

Media is the CMS library (``GET /library``): images and videos are
imported, everything else (audio, documents, module widgets) is skipped.
Files download from the same host as the API, so the Bearer token is
attached to the download (scoped to that host by the shared ingest layer).

TODO(confirm-with-live-token): Xibo web pages live on layouts as widgets,
not in the library, so they aren't imported here. The
``/library/download/{mediaId}/{mediaType}`` download shape is from the
Swagger spec and worth confirming against a live CMS.
"""

from __future__ import annotations

from typing import Any, Iterator

import requests

from . import ingest
from .base import (
    ImportOutcome,
    ImportProvider,
    ProviderImportError,
    RemoteMediaItem,
)
from .http import new_import_session

PROVIDER_KEY = 'xibo'

_PAGE_SIZE = 100
_VALIDATE_TIMEOUT_S = 15.0
_LIST_TIMEOUT_S = 30.0

_session = new_import_session()


def _base_url(host: str) -> str:
    return f'https://{host}/api'


def _parse_token(token: str) -> tuple[str, str, str]:
    """Split ``host:client_id:client_secret`` (hostname only, no scheme)."""
    parts = (token or '').split(':', 2)
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ProviderImportError(
            'Xibo token must be "cms-host:client_id:client_secret".'
        )
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _authorize(host: str, client_id: str, client_secret: str) -> str | None:
    """Exchange client credentials for a Bearer token, or None if rejected."""
    response = _session.post(
        f'{_base_url(host)}/authorize/access_token',
        data={
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
        },
        timeout=_VALIDATE_TIMEOUT_S,
    )
    if response.status_code in (400, 401, 403):
        return None
    response.raise_for_status()
    token = response.json().get('access_token')
    return token if isinstance(token, str) and token else None


def _login_or_raise(token: str) -> tuple[str, dict[str, str]]:
    """Return (host, auth-headers) after authorizing.

    Raises ``ProviderImportError`` for a malformed token or rejected
    credentials; transport errors propagate.
    """
    host, client_id, client_secret = _parse_token(token)
    access = _authorize(host, client_id, client_secret)
    if not access:
        raise ProviderImportError('Xibo rejected these API credentials.')
    return host, {'Authorization': f'Bearer {access}'}


def _map_type(media_type: Any) -> str | None:
    value = media_type.lower() if isinstance(media_type, str) else ''
    if value == 'image':
        return 'image'
    if value == 'video':
        return 'video'
    return None


class XiboProvider(ImportProvider):
    key = PROVIDER_KEY
    label = 'Xibo'
    description = (
        'Copy images and videos from a Xibo CMS library into this player.'
    )
    token_help = (
        'In your Xibo CMS create an API application (Applications → Add), '
        'then enter "cms-host:client_id:client_secret" — the host is the '
        'CMS hostname (e.g. name.xibosignage.com). Used only for this import '
        'and never stored.'
    )

    # -- token / listing ---------------------------------------------------

    def validate_token(self, token: str) -> bool:
        try:
            host, client_id, client_secret = _parse_token(token)
        except ProviderImportError:
            return False
        return _authorize(host, client_id, client_secret) is not None

    def list_media(
        self, token: str, *, workspace: str | None = None
    ) -> list[RemoteMediaItem]:
        try:
            host, headers = _login_or_raise(token)
        except ProviderImportError as error:
            # list_media's caller handles transport errors, not
            # ProviderImportError — surface bad creds as a controlled 502.
            raise requests.RequestException(error.user_message) from error

        items: list[RemoteMediaItem] = []
        for media in self._paginate(host, headers):
            media_id = media.get('mediaId')
            if media_id is None:
                continue
            media_type = _map_type(media.get('mediaType'))
            importable = media_type in ('image', 'video')
            items.append(
                RemoteMediaItem(
                    remote_id=str(media_id),
                    name=str(
                        media.get('name')
                        or media.get('fileName')
                        or f'Xibo media {media_id}'
                    ),
                    media_type=media_type or 'unsupported',
                    importable=importable,
                    skip_reason=None
                    if importable
                    else "This Xibo library item isn't an image or video.",
                    raw=media,
                )
            )
        return items

    def _paginate(
        self, host: str, headers: dict[str, str]
    ) -> Iterator[dict[str, Any]]:
        start = 0
        seen: set[Any] = set()
        while True:
            batch = self._library(
                host, headers, {'start': start, 'length': _PAGE_SIZE}
            )
            fresh = [
                media
                for media in batch
                if isinstance(media, dict) and media.get('mediaId') not in seen
            ]
            # No new rows means the CMS ignored our paging (returned the
            # same set) or we're done — either way, stop.
            if not fresh:
                break
            for media in fresh:
                seen.add(media.get('mediaId'))
                yield media
            if len(batch) < _PAGE_SIZE:
                break
            start += _PAGE_SIZE

    def _library(
        self, host: str, headers: dict[str, str], params: dict[str, Any]
    ) -> list[Any]:
        response = _session.get(
            f'{_base_url(host)}/library',
            headers=headers,
            params=params,
            timeout=_LIST_TIMEOUT_S,
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

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

        host, headers = _login_or_raise(token)
        matches = self._library(host, headers, {'mediaId': remote_id})
        media = (
            matches[0] if matches and isinstance(matches[0], dict) else None
        )
        if media is None:
            raise ProviderImportError('Media no longer exists in Xibo.')

        media_type = _map_type(media.get('mediaType'))
        if media_type not in ('image', 'video'):
            return ImportOutcome(
                success=False,
                skipped=True,
                reason="This Xibo library item isn't an image or video.",
            )

        file_url = (
            f'{_base_url(host)}/library/download/{remote_id}/'
            f'{media.get("mediaType")}'
        )
        start_date, end_date = ingest.default_window()
        asset = ingest.create_file_asset(
            session=_session,
            headers=headers,
            # The download is on the CMS host, so the Bearer token IS
            # attached (scoped to that host by ingest).
            auth_host=host,
            provider_key=PROVIDER_KEY,
            remote_id=remote_id,
            name=str(media.get('name') or media.get('fileName') or remote_id),
            mimetype=media_type,
            file_url=file_url,
            ext=ingest.file_ext_from(None, media.get('fileName') or ''),
            # Video duration is probed server-side; images use Xibo's value.
            duration=(
                0
                if media_type == 'video'
                else ingest.duration_or_default(media.get('duration'))
            ),
            start_date=start_date,
            end_date=end_date,
            enable=enable,
        )
        return ImportOutcome(success=True, asset_id=asset.asset_id)
