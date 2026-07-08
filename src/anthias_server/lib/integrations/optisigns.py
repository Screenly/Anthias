"""OptiSigns import provider — GraphQL.

OptiSigns' API is a single GraphQL endpoint. Media lives under one
``assets`` connection (there is no per-type query and no single-item
query — a lookup is a filtered ``assets(query: {_id: …})``). Each asset's
kind is read from ``fileType`` (image/video) or a populated external
``webLink`` (web pages); apps, widgets, YouTube and other
internally-generated content are filtered out.

Reuses the shared ingest layer for download + asset creation +
idempotency, behind the same ``ImportProvider`` interface as the other
providers.

TODO(confirm-with-live-token): the exact downloadable original-file field
is undocumented — ``path`` (and ``video_1080p`` for video) are the
candidates but may be CDN-relative, in which case the item is skipped
rather than guessed at. The ``QueryAssetInput`` ``_id`` filter shape and
the ``fileType``/``appType`` vocabularies are also worth confirming
against a live account. Resolution points are isolated in
``_file_download_url`` / ``_ASSET_BY_ID`` / ``_classify``.
"""

from __future__ import annotations

from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from . import graphql, ingest
from .base import (
    ImportOutcome,
    ImportProvider,
    ProviderImportError,
    RemoteMediaItem,
)
from .http import new_import_session

PROVIDER_KEY = 'optisigns'
OPTISIGNS_ENDPOINT = 'https://graphql-gateway.optisigns.com/graphql'

_LIST_PAGE_SIZE = 100
_VALIDATE_TIMEOUT_S = 15.0
_QUERY_TIMEOUT_S = 30.0

# Hosts whose web links are internal/app content, not portable external
# URLs (OptiSigns-hosted apps/dashboards, and YouTube handled as an app).
_INTERNAL_WEB_HOSTS = ('optisigns.com', 'youtube.com', 'youtu.be')

_session = new_import_session()

# --- GraphQL documents ------------------------------------------------------

_VALIDATE_QUERY = (
    'query { assets(first: 1, query: {}) { page { edges { cursor } } } }'
)

# Only the fields needed to classify a row (id/name + type discriminators);
# the full field set is fetched per item on import.
_ASSETS = """
query($first: Int!, $after: String) {
  assets(first: $first, after: $after, query: {}) {
    page {
      edges {
        cursor
        node { _id name fileType appType webType webLink youtubeType }
      }
    }
  }
}
"""

_ASSET_BY_ID = """
query($id: String!) {
  assets(first: 1, query: { _id: $id }) {
    page {
      edges {
        node {
          _id name fileType appType webType webLink youtubeType embedLink
          duration path thumbnail video_1080p fileExtension
          originalFileExtension originalFileName filename
        }
      }
    }
  }
}
"""


def _post(
    token: str,
    query: str,
    variables: dict[str, Any] | None,
    timeout: float,
) -> Any:
    return graphql.post(
        _session,
        OPTISIGNS_ENDPOINT,
        graphql.bearer_headers(token),
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
        _post(token, query, variables, timeout), source='OptiSigns'
    )


def _edges(data: dict[str, Any]) -> list[dict[str, Any]]:
    page = (data.get('assets') or {}).get('page') or {}
    edges = page.get('edges') or []
    return [edge for edge in edges if isinstance(edge, dict)]


def _classify(node: dict[str, Any]) -> str | None:
    """Return 'image' | 'video' | 'webpage', or None for unsupported.

    Files are keyed off ``fileType``; a genuine external ``webLink`` (not
    an OptiSigns/YouTube app) is a web page. Everything else — apps,
    widgets, YouTube, audio, documents — is unsupported.
    """
    file_type = (node.get('fileType') or '').lower()
    if file_type == 'image':
        return 'image'
    if file_type == 'video':
        return 'video'
    if _webpage_url(node):
        return 'webpage'
    return None


def _webpage_url(node: dict[str, Any]) -> str | None:
    """Return a genuine external web URL, or None for internal/app content."""
    if node.get('youtubeType'):
        return None
    url = ingest.first_http_url([node.get('webLink')])
    if not url:
        return None
    # ``hostname`` (not ``netloc``) so an explicit port or userinfo can't
    # dodge the internal-host match.
    host = (urlparse(url).hostname or '').lower()
    if any(
        host == internal or host.endswith(f'.{internal}')
        for internal in _INTERNAL_WEB_HOSTS
    ):
        return None
    return url


def _file_download_url(node: dict[str, Any]) -> str | None:
    """Pick the downloadable original URL (absolute http(s) only).

    ``video_1080p`` is preferred for video; otherwise ``path``. If neither
    is an absolute URL (e.g. a CDN-relative path), None is returned and the
    item is skipped rather than guessed at — see the module TODO.
    """
    candidates = []
    if (node.get('fileType') or '').lower() == 'video':
        candidates.append(node.get('video_1080p'))
    candidates.append(node.get('path'))
    return ingest.first_http_url(candidates)


def _default_duration(node: dict[str, Any]) -> int:
    return ingest.duration_or_default(node.get('duration'))


def _asset_name(node: dict[str, Any]) -> str:
    # Coerce to str: OptiSigns fields are typed loosely, and a non-string
    # (or falsy non-None) name would otherwise break ``.strip()``.
    name = (
        node.get('name')
        or node.get('originalFileName')
        or node.get('filename')
        or f'OptiSigns asset {node.get("_id")}'
    )
    return str(name).strip()


class OptiSignsProvider(ImportProvider):
    key = PROVIDER_KEY
    label = 'OptiSigns'
    description = (
        'Copy images, videos and web pages from an OptiSigns account into '
        'this player using an OptiSigns API key.'
    )
    token_help = (
        'Create an API key in OptiSigns under Account Settings → API Keys → '
        'New API Key. Paste it here; it is used only for this import and is '
        'never stored.'
    )

    # -- token / listing ---------------------------------------------------

    def validate_token(self, token: str) -> bool:
        response = _post(token, _VALIDATE_QUERY, None, _VALIDATE_TIMEOUT_S)
        if response.status_code in (401, 403):
            return False
        response.raise_for_status()
        body = response.json()
        if body.get('errors'):
            return False
        data = body.get('data') or {}
        return isinstance(data.get('assets'), dict)

    def list_media(
        self, token: str, *, workspace: str | None = None
    ) -> list[RemoteMediaItem]:
        items: list[RemoteMediaItem] = []
        for node in self._paginate(token):
            remote_id = node.get('_id')
            if not remote_id:
                continue
            media_type = _classify(node)
            importable = media_type is not None
            items.append(
                RemoteMediaItem(
                    remote_id=str(remote_id),
                    name=_asset_name(node),
                    media_type=media_type or 'unsupported',
                    importable=importable,
                    skip_reason=None
                    if importable
                    else (
                        "This OptiSigns asset isn't a supported image, video "
                        'or web page (app, widget or other content).'
                    ),
                    raw=node,
                )
            )
        return items

    def _paginate(self, token: str) -> Iterator[dict[str, Any]]:
        after: str | None = None
        while True:
            try:
                data = _graphql(
                    token, _ASSETS, {'first': _LIST_PAGE_SIZE, 'after': after}
                )
            except ProviderImportError as error:
                # list_media's caller (the API view) handles transport
                # errors but not ProviderImportError; surface a GraphQL
                # failure as one so it becomes a controlled 502, not a 500.
                raise requests.RequestException(error.user_message) from error
            edges = _edges(data)
            for edge in edges:
                node = edge.get('node')
                if isinstance(node, dict):
                    yield node
            # OptiSigns' PageInfo field names aren't documented, so stop on a
            # short page (or a missing cursor) rather than trusting a
            # hasNextPage flag.
            if len(edges) < _LIST_PAGE_SIZE:
                break
            after = edges[-1].get('cursor')
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

        node = self._get_asset(token, remote_id)
        media_type = _classify(node)
        if media_type is None:
            return ImportOutcome(
                success=False,
                skipped=True,
                reason=(
                    "This OptiSigns asset isn't a supported image, video or "
                    'web page.'
                ),
            )

        name = _asset_name(node)
        start_date, end_date = ingest.default_window()

        if media_type == 'webpage':
            url = _webpage_url(node)
            if not url:
                return ImportOutcome(
                    success=False,
                    skipped=True,
                    reason='Could not determine an external URL for this asset.',
                )
            asset = ingest.create_webpage_asset(
                provider_key=PROVIDER_KEY,
                remote_id=remote_id,
                name=name,
                url=url,
                duration=_default_duration(node),
                start_date=start_date,
                end_date=end_date,
                enable=enable,
            )
            return ImportOutcome(success=True, asset_id=asset.asset_id)

        file_url = _file_download_url(node)
        if not file_url:
            return ImportOutcome(
                success=False,
                skipped=True,
                reason=(
                    "OptiSigns didn't expose a downloadable original for this "
                    f'{media_type}; re-upload it manually.'
                ),
            )
        ext_hint = node.get('fileExtension') or node.get(
            'originalFileExtension'
        )
        # No auth on the download: OptiSigns files are CDN/S3-served, so the
        # bearer token must not be forwarded.
        asset = ingest.create_file_asset(
            session=_session,
            provider_key=PROVIDER_KEY,
            remote_id=remote_id,
            name=name,
            mimetype=media_type,
            file_url=file_url,
            ext=ingest.file_ext_from(ext_hint, file_url),
            # Video duration is probed server-side; images use the default.
            duration=0 if media_type == 'video' else _default_duration(node),
            start_date=start_date,
            end_date=end_date,
            enable=enable,
        )
        return ImportOutcome(success=True, asset_id=asset.asset_id)

    def _get_asset(self, token: str, remote_id: str) -> dict[str, Any]:
        data = _graphql(token, _ASSET_BY_ID, {'id': remote_id})
        edges = _edges(data)
        if not edges:
            raise ProviderImportError('Asset no longer exists in OptiSigns.')
        node = edges[0].get('node')
        if not isinstance(node, dict):
            raise ProviderImportError('Unexpected response from OptiSigns.')
        return node
