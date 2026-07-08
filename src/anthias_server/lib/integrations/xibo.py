"""Xibo import provider — REST (Xibo CMS API).

Works with both Xibo Cloud (``https://<name>.xibosignage.com``) and
self-hosted CMS instances (any URL, including a custom host, port or
sub-path). Authenticates with OAuth2 client-credentials: an API
application's ``client_id`` / ``client_secret`` are exchanged for a Bearer
token at ``POST <cms>/api/authorize/access_token``.

Because a self-hosted CMS URL can contain ``:`` (scheme, port), the
operator's "token" is whitespace-separated:
``<cms-url> <client_id> <client_secret>`` — e.g.
``https://signage.example.com:8080 abc123 secretxyz``.

Media is the CMS library (``GET /library``): images and videos are
imported, everything else (audio, documents, module widgets) is skipped.
Files download from the same host as the API, so the Bearer token is
attached to the download (scoped to that host by the shared ingest layer).

TODO(confirm-with-live-token): Xibo web pages live on layouts as widgets,
not in the library, so they aren't imported here. The
``<cms>/api/library/download/{mediaId}/{mediaType}`` download shape is from
the Swagger spec and worth confirming against a live CMS.
"""

from __future__ import annotations

from typing import Any, Iterator
from urllib.parse import urlparse

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


def _parse_token(token: str) -> tuple[str, str, str]:
    """Split ``<cms-url> <client_id> <client_secret>`` (whitespace-separated).

    The CMS URL is validated as http(s); any trailing slash is trimmed so
    ``<base>/api/...`` is well-formed for cloud and self-hosted alike.
    """
    parts = (token or '').split()
    if len(parts) != 3:
        raise ProviderImportError(
            'Xibo token must be "<cms-url> <client_id> <client_secret>".'
        )
    base, client_id, client_secret = parts
    parsed = urlparse(base)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ProviderImportError(
            'Xibo CMS URL must be a full http(s) URL, e.g. '
            'https://name.xibosignage.com.'
        )
    return base.rstrip('/'), client_id, client_secret


def _api_url(base: str, path: str) -> str:
    return f'{base}/api{path}'


def _host(base: str) -> str:
    """Host (with port, if any) the CMS is served from."""
    return urlparse(base).netloc.lower()


def _authorize(base: str, client_id: str, client_secret: str) -> str | None:
    """Exchange client credentials for a Bearer token, or None if rejected."""
    response = _session.post(
        _api_url(base, '/authorize/access_token'),
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
    """Return (cms-base-url, auth-headers) after authorizing.

    Raises ``ProviderImportError`` for a malformed token or rejected
    credentials; transport errors propagate.
    """
    base, client_id, client_secret = _parse_token(token)
    access = _authorize(base, client_id, client_secret)
    if not access:
        raise ProviderImportError('Xibo rejected these API credentials.')
    return base, {'Authorization': f'Bearer {access}'}


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
        'Copy images and videos from a Xibo CMS library into this player '
        '(Xibo Cloud or self-hosted).'
    )
    token_help = (
        'In your Xibo CMS create an API application (Applications → Add), '
        'then enter "<cms-url> <client_id> <client_secret>" (space '
        'separated) — the CMS URL is your Xibo address, e.g. '
        'https://name.xibosignage.com, or your self-hosted CMS URL. Used '
        'only for this import and never stored.'
    )

    # -- token / listing ---------------------------------------------------

    def validate_token(self, token: str) -> bool:
        try:
            base, client_id, client_secret = _parse_token(token)
        except ProviderImportError:
            return False
        return _authorize(base, client_id, client_secret) is not None

    def list_media(
        self, token: str, *, workspace: str | None = None
    ) -> list[RemoteMediaItem]:
        try:
            base, headers = _login_or_raise(token)
        except ProviderImportError as error:
            # list_media's caller handles transport errors, not
            # ProviderImportError — surface bad creds as a controlled 502.
            raise requests.RequestException(error.user_message) from error

        items: list[RemoteMediaItem] = []
        for media in self._paginate(base, headers):
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
        self, base: str, headers: dict[str, str]
    ) -> Iterator[dict[str, Any]]:
        start = 0
        seen: set[Any] = set()
        while True:
            batch = self._library(
                base, headers, {'start': start, 'length': _PAGE_SIZE}
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
        self, base: str, headers: dict[str, str], params: dict[str, Any]
    ) -> list[Any]:
        response = _session.get(
            _api_url(base, '/library'),
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

        base, headers = _login_or_raise(token)
        matches = self._library(base, headers, {'mediaId': remote_id})
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

        # Use the normalised ``media_type`` (not the raw field) so casing
        # variance in the API response can't produce a wrong download URL.
        file_url = _api_url(
            base,
            f'/library/download/{remote_id}/{media_type}',
        )
        start_date, end_date = ingest.default_window()
        asset = ingest.create_file_asset(
            session=_session,
            headers=headers,
            # The download is on the CMS host, so the Bearer token IS
            # attached (scoped to that host by ingest).
            auth_host=_host(base),
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
