"""ScreenCloud (Studio) import provider — GraphQL.

ScreenCloud's Signage/Studio API is a PostGraphile GraphQL endpoint (not
REST), which is exactly why the provider interface is transport-agnostic:
this module speaks GraphQL behind the same
``validate_token`` / ``list_media`` / ``import_item`` contract, and reuses
:mod:`anthias_server.lib.integrations.ingest` for the download + asset
creation + idempotency that every provider shares.

Media is split across two GraphQL types:

* ``File``  → uploaded images and videos (distinguished by ``mimetype``).
  The downloadable original comes from ``fileOutputsByFileId``.
* ``Link``  → web pages / URLs (imported as Anthias ``webpage`` assets).

Remote ids are namespaced ``file:<uuid>`` / ``link:<uuid>`` so a single
``import_item`` call knows which GraphQL type to fetch.

Endpoint / region: ScreenCloud endpoints are per-organization and region
(``eu`` / ``us``). The operator can prefix the token with a region
(``eu:<token>`` / ``us:<token>``); without a prefix the EU endpoint is
used. See ``token_help``.

TODO(confirm-with-live-token): the production regional hostnames, the
``fileById`` / ``linkById`` field names, and which ``FileOutput`` is the
durable original vs a thumbnail are documented behind a ScreenCloud login
and should be verified against a real account before GA. The resolution
points are isolated in ``_endpoint_for`` / ``_file_download_url`` /
``_FILE_BY_ID`` so confirming them is a localized change.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

import requests

from . import graphql, ingest
from .base import (
    ImportOutcome,
    ImportProvider,
    ProviderImportError,
    RemoteMediaItem,
)
from .http import new_import_session

logger = logging.getLogger(__name__)

PROVIDER_KEY = 'screencloud'

# Region → GraphQL endpoint. The staging EU host is the only one the
# public docs state outright; the production hosts follow the documented
# ``*.next.sc/graphql`` pattern but are unconfirmed (read the exact value
# from Studio → Account Settings → DEVELOPER). Kept in one map so a
# correction is a one-line change.
_REGION_ENDPOINTS = {
    'eu': 'https://graphql.eu.next.sc/graphql',
    'us': 'https://graphql.us.next.sc/graphql',
}
_DEFAULT_REGION = 'eu'

_LIST_PAGE_SIZE = 100
_VALIDATE_TIMEOUT_S = 15.0
_QUERY_TIMEOUT_S = 30.0

_session = new_import_session()

# --- GraphQL documents ------------------------------------------------------

_VALIDATE_QUERY = '{ currentOrg { id } }'

_ALL_FILES = """
query($first: Int!, $after: Cursor) {
  allFiles(first: $first, after: $after, orderBy: [CREATED_AT_DESC]) {
    pageInfo { hasNextPage endCursor }
    nodes { id name mimetype }
  }
}
"""

# ``linkType`` filters out non-portable links: only STANDARD is a real
# external URL. INTERNAL / CLOUD are ScreenCloud-hosted content (apps,
# dashboards, cloud integrations) that would not render outside Studio.
_STANDARD_LINK = 'STANDARD'

_ALL_LINKS = """
query($first: Int!, $after: Cursor) {
  allLinks(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id name linkType }
  }
}
"""

_FILE_BY_ID = """
query($id: UUID!) {
  fileById(id: $id) {
    id name mimetype availableAt expireAt
    fileOutputsByFileId { nodes { url mimetype } }
  }
}
"""

_LINK_BY_ID = """
query($id: UUID!) {
  linkById(id: $id) { id name url linkType }
}
"""


def _parse_token(token: str) -> tuple[str, str]:
    """Split an optional ``<region>:`` prefix off the bearer token."""
    raw = (token or '').strip()
    prefix, sep, rest = raw.partition(':')
    if sep and prefix.lower() in _REGION_ENDPOINTS:
        return prefix.lower(), rest
    return _DEFAULT_REGION, raw


def _endpoint_for(token: str) -> tuple[str, str]:
    region, bearer = _parse_token(token)
    return _REGION_ENDPOINTS[region], bearer


def _post(
    token: str,
    query: str,
    variables: dict[str, Any] | None,
    timeout: float,
) -> Any:
    endpoint, bearer = _endpoint_for(token)
    return graphql.post(
        _session,
        endpoint,
        graphql.bearer_headers(bearer),
        query,
        variables,
        timeout,
    )


def _graphql(
    token: str,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    timeout: float = _QUERY_TIMEOUT_S,
) -> dict[str, Any]:
    return graphql.data_or_raise(
        _post(token, query, variables, timeout), source='ScreenCloud'
    )


def _file_media_type(mimetype: str | None) -> str:
    mt = (mimetype or '').lower()
    if mt.startswith('image/'):
        return 'image'
    if mt.startswith('video/'):
        return 'video'
    if mt.startswith('audio/'):
        return 'audio'
    return 'document'


def _file_download_url(file_obj: dict[str, Any]) -> str | None:
    """Pick the downloadable original URL from a File's outputs.

    Prefers a ``FileOutput`` whose mimetype matches the file's top-level
    type (image/video), falling back to the first valid URL. Which output
    is the durable original vs a thumbnail isn't documented — see the
    module TODO.
    """
    mimetype = file_obj.get('mimetype') or ''
    top = mimetype.split('/', 1)[0]
    outputs = (file_obj.get('fileOutputsByFileId') or {}).get('nodes') or []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        url = output.get('url')
        out_top = (output.get('mimetype') or '').split('/', 1)[0]
        if out_top and out_top == top and ingest.first_http_url([url]):
            return url
    return ingest.first_http_url(
        output.get('url') for output in outputs if isinstance(output, dict)
    )


def _default_duration() -> int:
    # ScreenCloud files/links don't expose a per-item display duration, so
    # imported assets always use the device default.
    return ingest.duration_or_default(None)


class ScreenCloudProvider(ImportProvider):
    key = PROVIDER_KEY
    label = 'ScreenCloud'
    description = (
        'Copy images, videos and web pages from a ScreenCloud (Studio) '
        'account into this player using a ScreenCloud API token.'
    )
    token_help = (
        'Create an API token in ScreenCloud Studio under Account Settings '
        '→ Developer → New Token. Paste it here; if your account is in the '
        'US region, prefix the token with "us:" (EU is the default). It is '
        'used only for this import and is never stored.'
    )

    # -- token / listing ---------------------------------------------------

    def validate_token(self, token: str) -> bool:
        response = _post(token, _VALIDATE_QUERY, None, _VALIDATE_TIMEOUT_S)
        if response.status_code in (401, 403):
            return False
        response.raise_for_status()
        body = response.json()
        # A minimal ``currentOrg`` query returns data for a good token; an
        # errors[] here means the token was rejected.
        if body.get('errors'):
            return False
        data = body.get('data') or {}
        return bool(data.get('currentOrg'))

    def list_media(
        self, token: str, *, workspace: str | None = None
    ) -> list[RemoteMediaItem]:
        items: list[RemoteMediaItem] = []
        for node in self._paginate(token, _ALL_FILES, 'allFiles'):
            remote_id = node.get('id')
            if not remote_id:
                continue
            media_type = _file_media_type(node.get('mimetype'))
            importable = media_type in ('image', 'video')
            items.append(
                RemoteMediaItem(
                    remote_id=f'file:{remote_id}',
                    name=node.get('name') or f'ScreenCloud file {remote_id}',
                    media_type=media_type,
                    importable=importable,
                    skip_reason=None
                    if importable
                    else (
                        f"{media_type.capitalize()} media isn't supported by "
                        'Anthias and was skipped.'
                    ),
                    raw=node,
                )
            )
        for node in self._paginate(token, _ALL_LINKS, 'allLinks'):
            remote_id = node.get('id')
            if not remote_id:
                continue
            importable = node.get('linkType') == _STANDARD_LINK
            items.append(
                RemoteMediaItem(
                    remote_id=f'link:{remote_id}',
                    name=node.get('name') or f'ScreenCloud link {remote_id}',
                    media_type='webpage',
                    importable=importable,
                    skip_reason=None
                    if importable
                    else (
                        'Internal ScreenCloud content, not a standard web '
                        'link.'
                    ),
                    raw=node,
                )
            )
        return items

    def _paginate(
        self, token: str, query: str, connection: str
    ) -> Iterator[dict[str, Any]]:
        after: str | None = None
        while True:
            try:
                data = _graphql(
                    token,
                    query,
                    {'first': _LIST_PAGE_SIZE, 'after': after},
                )
            except ProviderImportError as error:
                # ``_paginate`` is only used by ``list_media``, whose caller
                # (the validate API view) handles transport errors but not
                # ``ProviderImportError`` — surface a GraphQL-level failure
                # as a transport error so it becomes a controlled 502
                # rather than a 500.
                raise requests.RequestException(error.user_message) from error
            block = data.get(connection) or {}
            for node in block.get('nodes') or []:
                if isinstance(node, dict):
                    yield node
            page_info = block.get('pageInfo') or {}
            if not page_info.get('hasNextPage'):
                break
            after = page_info.get('endCursor')
            if not after:
                break

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

        kind, _sep, native_id = remote_id.partition(':')
        if kind == 'link':
            return self._import_link(token, remote_id, native_id, enable)
        if kind == 'file':
            return self._import_file(token, remote_id, native_id, enable)
        return ImportOutcome(
            success=False,
            skipped=True,
            reason=f'Unrecognised ScreenCloud id: {remote_id}.',
        )

    def _import_link(
        self, token: str, remote_id: str, native_id: str, enable: bool
    ) -> ImportOutcome:
        data = _graphql(token, _LINK_BY_ID, {'id': native_id})
        link = data.get('linkById')
        if not isinstance(link, dict):
            raise ProviderImportError('Link no longer exists in ScreenCloud.')
        if link.get('linkType') != _STANDARD_LINK:
            return ImportOutcome(
                success=False,
                skipped=True,
                reason=(
                    'Internal ScreenCloud content (not a standard web link) '
                    'cannot be imported.'
                ),
            )
        url = ingest.first_http_url([link.get('url')])
        if not url:
            return ImportOutcome(
                success=False,
                skipped=True,
                reason='ScreenCloud link has no URL.',
            )
        start_date, end_date = ingest.default_window()
        asset = ingest.create_webpage_asset(
            provider_key=PROVIDER_KEY,
            remote_id=remote_id,
            name=(link.get('name') or f'ScreenCloud link {native_id}').strip(),
            url=url,
            duration=_default_duration(),
            start_date=start_date,
            end_date=end_date,
            enable=enable,
        )
        return ImportOutcome(success=True, asset_id=asset.asset_id)

    def _import_file(
        self, token: str, remote_id: str, native_id: str, enable: bool
    ) -> ImportOutcome:
        data = _graphql(token, _FILE_BY_ID, {'id': native_id})
        file_obj = data.get('fileById')
        if not isinstance(file_obj, dict):
            raise ProviderImportError('File no longer exists in ScreenCloud.')

        media_type = _file_media_type(file_obj.get('mimetype'))
        if media_type not in ('image', 'video'):
            return ImportOutcome(
                success=False,
                skipped=True,
                reason=(
                    f"{media_type.capitalize()} media isn't supported by "
                    'Anthias.'
                ),
            )

        file_url = _file_download_url(file_obj)
        if not file_url:
            return ImportOutcome(
                success=False,
                skipped=True,
                reason=(
                    "ScreenCloud didn't expose a downloadable original for "
                    f'this {media_type}; re-upload it manually.'
                ),
            )

        start_date, end_date = _file_window(file_obj)
        # No auth on the download: ScreenCloud FileOutput URLs are
        # pre-signed CDN links, so forwarding the bearer token would leak
        # it and can break the signed request.
        asset = ingest.create_file_asset(
            session=_session,
            provider_key=PROVIDER_KEY,
            remote_id=remote_id,
            name=(
                file_obj.get('name') or f'ScreenCloud file {native_id}'
            ).strip(),
            mimetype=media_type,
            file_url=file_url,
            ext=ingest.file_ext_from(None, file_url),
            # Video duration is probed server-side; images use the default.
            duration=0 if media_type == 'video' else _default_duration(),
            start_date=start_date,
            end_date=end_date,
            enable=enable,
        )
        return ImportOutcome(success=True, asset_id=asset.asset_id)


def _parse_dt(value: Any) -> Any:
    from datetime import datetime

    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _file_window(file_obj: dict[str, Any]) -> tuple[Any, Any]:
    now, far_future = ingest.default_window()
    start = _parse_dt(file_obj.get('availableAt')) or now
    end = _parse_dt(file_obj.get('expireAt')) or far_future
    if end <= start:
        return now, far_future
    return start, end
