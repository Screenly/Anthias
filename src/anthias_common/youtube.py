"""YouTube asset helpers shared across the frontend, the API
serializers, and the Celery worker.

Centralised here so URL detection, the on-disk destination path, and
the Celery dispatch are computed identically everywhere. The previous
layout sprinkled equivalent logic across two serializers and missed
the frontend create view entirely (see ``app/views.assets_create``),
which is how YouTube URLs pasted into the Add modal silently became
``mimetype='webpage'`` instead of triggering a download.
"""

from __future__ import annotations

from os import path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from anthias_server.settings import AnthiasSettings


_YOUTUBE_HOSTS = frozenset(
    {
        'youtube.com',
        'www.youtube.com',
        'm.youtube.com',
        'music.youtube.com',
        'youtu.be',
    }
)


def is_youtube_url(uri: str) -> bool:
    """True when *uri* points at a recognised YouTube hostname.

    Substring sniffing (`'youtube' in uri`) was rejected: it would
    match decoy hosts like ``evil.com/?youtube`` and miss the short
    ``youtu.be`` form. Parsing the hostname keeps the check anchored
    to the URL's authority component.
    """
    if not uri:
        return False
    try:
        parsed = urlparse(uri.strip())
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    host = (parsed.hostname or '').lower()
    return host in _YOUTUBE_HOSTS


def youtube_destination_path(
    asset_id: str,
    settings: 'AnthiasSettings | None' = None,
) -> str:
    """Resolve where the downloaded mp4 will land.

    Picks ``settings['assetdir']`` so ``cleanup()`` (which sweeps that
    same directory) recognises the final file as referenced. A custom
    assetdir would otherwise leak orphaned downloads in
    ``$HOME/anthias_assets`` after a failed run.
    """
    if settings is None:
        # Lazy import to avoid pulling Django settings into modules
        # that only need is_youtube_url.
        from anthias_server.settings import settings as _settings

        settings = _settings
    return path.join(settings['assetdir'], f'{asset_id}.mp4')


def dispatch_download(asset_id: str, source_uri: str) -> None:
    """Queue the Celery worker to fetch *source_uri* into the row.

    Lazy import keeps the celery_tasks module out of import paths
    that don't need it (e.g. the viewer or anthias_common itself).
    """
    from anthias_server.celery_tasks import download_youtube_asset

    download_youtube_asset.delay(asset_id, source_uri)
