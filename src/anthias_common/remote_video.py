"""Generic remote-video URL helpers shared across the API
serializers and the Celery worker.

Mirrors ``anthias_common.youtube`` but for the broader case of a
``http(s)://…`` URL pointing at a single-file video container (mp4 /
webm / mov / mkv / ...). Centralising the classify, the on-disk
destination, and the Celery dispatch here means the create views never
diverge in their handling and a future API version inherits the
behaviour by importing the same helpers.

Keep this module free of Django and Celery imports so the serializers
can import it without dragging the Django settings module into the
hot create-asset path twice. The dispatch helper does a lazy import of
``anthias_server.celery_tasks`` only when it actually fires.
"""

from __future__ import annotations

import mimetypes
from os import path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests

from anthias_common.http import AnthiasSession

if TYPE_CHECKING:
    from anthias_server.settings import AnthiasSettings


# Module-level shared session so the HEAD probe reuses one TCP/TLS
# connection pool across the lifetime of the process. Pattern matches
# ``anthias_server.lib.screenly_migration._session`` — tests patch
# ``_session.head`` (or whichever method) directly.
_session = AnthiasSession()


# Single-file video containers we know how to download and that the
# normalisation pipeline can ffprobe. The set is deliberately
# conservative: anything ending in one of these gets auto-downloaded,
# everything else either falls through to a HEAD probe (extensionless
# URLs) or stays as a streaming URL. Extending the set is a one-line
# change once the codec gate has been verified for the new container.
_VIDEO_CONTAINER_EXTS = frozenset(
    {
        '.mp4',
        '.webm',
        '.mov',
        '.mkv',
        '.avi',
        '.m4v',
        '.ogv',
    }
)

# Protocol schemes that are streaming-by-construction. The serializer
# never rewrites these to a local download even when the URL path's
# extension suggests a single file (``rtsp://host/stream.mp4`` is
# still an RTSP session, not an HTTP MP4). The viewer plays them
# through QtMultimedia's network stack as-is.
#
# ``rtmp`` is deliberately absent. Qt6's QMediaPlayer (FFmpeg backend)
# can't open it: the backend sets a ``timeout`` AVFormatContext option
# the rtmp protocol misreads as TCP *listen* mode, so the open fails
# and the screen stays black. ``validate_url`` rejects ``rtmp://`` at
# create time; keeping it out of this set too means ``is_streaming_uri``
# never classifies a (legacy) rtmp row as a playable stream.
_STREAM_SCHEMES = frozenset({'rtsp', 'srt', 'udp', 'mms'})

# HTTP-delivered manifests that describe a stream rather than a single
# downloadable file. ``.m3u8`` (HLS), ``.mpd`` (DASH), ``.m3u`` (legacy
# playlist), ``.ism`` (Smooth Streaming) all need live origin
# connectivity at play time and can't be flattened to a local file.
# Treat them the same as RTSP/RTMP — leave the URI verbatim.
_STREAM_MANIFEST_EXTS = frozenset({'.m3u8', '.mpd', '.m3u', '.ism'})

# Wall-clock cap on the HEAD probe for extensionless URLs. Kept short
# because the probe runs synchronously inside the POST /assets path
# and the operator's request blocks on it. 5s covers a slow origin
# without making the create request feel hung. Any failure (timeout,
# DNS, 4xx, redirect loop) downgrades the URL to "stream as-is"
# rather than rejecting the create — operators can still paste a
# weird URL and have it work for stream playback, they just don't
# get the auto-download benefit.
_HEAD_PROBE_TIMEOUT_S = 5

# Manifest content-types we explicitly reject even when the server
# advertises them as ``video/*`` (some HLS origins do this). Streaming
# manifests need live origin connectivity at play time — pulling them
# down as a single file would store the playlist, not the segments
# the playlist points at.
_MANIFEST_CONTENT_TYPES = frozenset(
    {
        'application/vnd.apple.mpegurl',
        'application/x-mpegurl',
        'application/dash+xml',
    }
)


def _url_path_ext(uri: str) -> str:
    """Return the lowercase extension of the URL's path component, with
    the leading dot. Empty string when the URL has no extension.

    Splits on ``urlparse(...).path`` rather than the full URI so a
    query string (``?download=true``) or fragment (``#t=10``) doesn't
    fool the extension match.
    """
    try:
        parsed = urlparse(uri.strip())
    except ValueError:
        return ''
    return path.splitext(parsed.path)[1].lower()


def _url_scheme(uri: str) -> str:
    try:
        return (urlparse(uri.strip()).scheme or '').lower()
    except ValueError:
        return ''


def _head_probe(uri: str) -> tuple[bool, str]:
    """Issue an HTTP HEAD against *uri* to classify its content.

    Returns ``(True, ext)`` when the response advertises a downloadable
    video and ``(False, '')`` otherwise. Any exception (timeout, DNS,
    refused connection, 4xx, redirect chain too long) collapses to the
    negative case — the URL stays as a stream URL.

    ``allow_redirects=True`` follows the common CDN pattern where the
    canonical URL redirects to a signed S3/Cloudfront URL whose path
    *does* carry the extension. The final response's Content-Type is
    what we classify on.

    ``mimetypes.guess_extension`` resolves the response's content-type
    to a file extension (``video/mp4`` → ``.mp4``). The default
    Python mimetypes table covers every container in
    ``_VIDEO_CONTAINER_EXTS``. Falls back to ``.mp4`` on the (rare)
    case where guess_extension returns None for a video/* type.
    """
    # Route through the module-level ``AnthiasSession`` so origins
    # see a consistent ``Anthias/<release>`` UA (matches the project-
    # wide outbound convention from #2897) and the connection pool
    # is reused across probes.
    try:
        resp = _session.head(
            uri,
            allow_redirects=True,
            timeout=_HEAD_PROBE_TIMEOUT_S,
        )
    except requests.RequestException:
        return False, ''
    if resp.status_code >= 400:
        return False, ''
    content_type = (resp.headers.get('Content-Type') or '').lower()
    # Strip parameters (``video/mp4; codecs=...``) before classifying.
    base_type = content_type.split(';', 1)[0].strip()
    if base_type in _MANIFEST_CONTENT_TYPES:
        return False, ''
    if not base_type.startswith('video/'):
        return False, ''
    # ``guess_extension`` returns ``.m4v`` for ``video/mp4`` in some
    # Python versions and ``.mp4`` in others. Normalise to a value in
    # our container set; default to ``.mp4`` for the common case.
    guessed = mimetypes.guess_extension(base_type)
    if guessed and guessed.lower() in _VIDEO_CONTAINER_EXTS:
        return True, guessed.lower()
    return True, '.mp4'


def is_downloadable_remote_video(uri: str) -> tuple[bool, str]:
    """Classify *uri* as auto-downloadable single-file video or not.

    Returns ``(True, ext)`` when the serializer should rewrite the
    asset to a local-download row, with ``ext`` (including the leading
    dot) being the extension to use for the on-disk file. Returns
    ``(False, '')`` when the URI should stay as a stream URL for the
    viewer to play live.

    Three-step decision:

    1. **Stream short-circuit** — non-http(s) streaming schemes
       (``rtsp://``, ``srt://``, ``udp://``, ``mms://``) and manifest
       extensions (``.m3u8`` / ``.mpd`` / ...) never download, no HEAD
       call.
    2. **Extension match** — the URL path's lowercase extension is in
       ``_VIDEO_CONTAINER_EXTS``: ``(True, ext)``, no HEAD call.
       Common path, zero network round-trips.
    3. **HEAD probe fallback** — extensionless URL, http(s) only:
       single HEAD, accept on ``Content-Type: video/*`` (excluding
       manifest types).

    Any unrecognised scheme (file://, ftp://, …) returns ``(False,
    '')`` so we never download from non-network or non-HTTP sources.
    """
    if not uri:
        return False, ''
    scheme = _url_scheme(uri)
    ext = _url_path_ext(uri)
    if scheme in _STREAM_SCHEMES:
        return False, ''
    if ext in _STREAM_MANIFEST_EXTS:
        return False, ''
    if scheme not in ('http', 'https'):
        return False, ''
    if ext in _VIDEO_CONTAINER_EXTS:
        return True, ext
    return _head_probe(uri)


def is_streaming_uri(uri: str) -> bool:
    """True when *uri* is a live stream the viewer plays directly via
    its video pipeline rather than a downloadable file or a renderable
    web page.

    Covers both streaming-by-construction schemes (``rtsp://``,
    ``srt://``, ``udp://``, ``mms://``) and HTTP-delivered streaming
    manifests (HLS ``.m3u8``, DASH ``.mpd``, legacy ``.m3u``,
    Smooth Streaming ``.ism``). The create paths use this to stamp such
    URIs as ``mimetype='streaming'`` so the viewer routes them to
    ``view_video`` instead of loading them in QtWebEngine (which can't
    open ``rtsp://`` and renders a manifest as plain text).

    Shares the same scheme / extension sets as
    ``is_downloadable_remote_video`` so the "leave the URI verbatim"
    decision there and the "classify it as streaming" decision here can
    never drift apart.
    """
    if not uri:
        return False
    if _url_scheme(uri) in _STREAM_SCHEMES:
        return True
    return _url_path_ext(uri) in _STREAM_MANIFEST_EXTS


def remote_video_destination_path(
    asset_id: str,
    ext: str,
    settings: 'AnthiasSettings | None' = None,
) -> str:
    """Resolve where the downloaded file will land on disk.

    Mirrors ``anthias_common.youtube.youtube_destination_path`` but
    takes the extension as a parameter because remote URLs span every
    container (mp4 / webm / mkv / ...) — preserving the source
    extension lets ffprobe identify the container correctly and the
    asset table show what was uploaded.
    """
    if settings is None:
        from anthias_server.settings import settings as _settings

        settings = _settings
    return path.join(settings['assetdir'], f'{asset_id}{ext}')


def dispatch_remote_video_download(asset_id: str, source_uri: str) -> None:
    """Queue the Celery worker to fetch *source_uri* into the row.

    Mirrors ``anthias_common.youtube.dispatch_download``. Stamps
    ``metadata.processing_started_at`` so the periodic
    ``reconcile_stuck_processing`` task can recover a row whose
    download went missing (worker SIGKILL between enqueue and pickup,
    redis flake during dispatch).

    Lazy imports keep ``celery_tasks`` and Django settings out of any
    import path that only needs ``is_downloadable_remote_video`` (the
    serializer hot path).
    """
    from anthias_server.celery_tasks import download_remote_video_asset
    from anthias_server.processing import stamp_processing_start

    stamp_processing_start(asset_id)
    download_remote_video_asset.delay(asset_id, source_uri)
