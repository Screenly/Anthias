"""Unit tests for ``anthias_common.remote_video``.

The classifier sits in the synchronous POST /assets path, so behaviour
under each input shape (known extension, manifest, stream scheme,
extensionless URL with various HEAD responses, network failure) is
covered explicitly to lock the create-asset contract.

These tests do not need Django — the helpers are framework-free — but
they live under ``tests/`` so the existing pytest harness picks them
up alongside the celery/processing suites.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest import mock

import pytest
import requests

from anthias_common.remote_video import (
    is_downloadable_remote_video,
    is_streaming_uri,
    remote_video_destination_path,
)
from anthias_server.settings import AnthiasSettings


# ---------------------------------------------------------------------------
# Extension-based classify (no HEAD call)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'uri,expected_ext',
    [
        ('https://example.com/clip.mp4', '.mp4'),
        ('https://cdn.example.com/path/to/file.webm', '.webm'),
        ('https://example.com/movie.MOV', '.mov'),
        # http (not https) is intentional — the classifier must accept
        # both schemes so operators on internal LANs (where TLS isn't
        # set up for the media server) can still auto-download.
        ('http://example.com/x.mkv', '.mkv'),  # NOSONAR
        ('https://example.com/a.avi', '.avi'),
        ('https://example.com/short.m4v', '.m4v'),
        ('https://example.com/old.ogv', '.ogv'),
        # Query strings and fragments do not fool the extension match.
        ('https://example.com/clip.mp4?download=true', '.mp4'),
        ('https://example.com/clip.mp4#t=10', '.mp4'),
    ],
)
def test_classify_known_video_extension_returns_download(
    uri: str, expected_ext: str
) -> None:
    """A URL whose path ends in a known single-file video container
    auto-downloads with the matching local extension. No HEAD call
    fires — extension match is the fast path."""
    with mock.patch('anthias_common.remote_video._session.head') as head:
        ok, ext = is_downloadable_remote_video(uri)
    assert ok is True
    assert ext == expected_ext
    head.assert_not_called()


@pytest.mark.parametrize(
    'uri',
    [
        'https://example.com/stream.m3u8',
        'https://example.com/dash/manifest.mpd',
        'https://example.com/legacy.m3u',
        'https://example.com/smooth/Manifest.ism',
    ],
)
def test_classify_streaming_manifest_extensions_return_stream(
    uri: str,
) -> None:
    """HLS / DASH / SmoothStreaming manifests never auto-download —
    they describe a stream, not a single file. No HEAD call (the
    extension match short-circuits)."""
    with mock.patch('anthias_common.remote_video._session.head') as head:
        ok, ext = is_downloadable_remote_video(uri)
    assert ok is False
    assert ext == ''
    head.assert_not_called()


@pytest.mark.parametrize(
    'uri',
    [
        'rtsp://camera.local/feed',
        'rtmp://media.example.com/live',
        'srt://stream.example.com:9000',
        'udp://stream.example.test:1234',
        'mms://media.example.com/live',
    ],
)
def test_classify_streaming_schemes_return_stream(uri: str) -> None:
    """RTSP / RTMP / SRT / UDP / MMS are streaming-by-construction,
    even if the URL's path happens to end in ``.mp4``. The viewer
    plays them live via mpv's network stack."""
    with mock.patch('anthias_common.remote_video._session.head') as head:
        ok, ext = is_downloadable_remote_video(uri)
    assert ok is False
    assert ext == ''
    head.assert_not_called()


def test_classify_streaming_scheme_with_mp4_path_returns_stream() -> None:
    """``rtsp://camera/feed.mp4`` is RTSP. Path extension does not
    promote it to an http(s) download."""
    with mock.patch('anthias_common.remote_video._session.head') as head:
        ok, ext = is_downloadable_remote_video('rtsp://camera/feed.mp4')
    assert ok is False
    assert ext == ''
    head.assert_not_called()


# ---------------------------------------------------------------------------
# is_streaming_uri — the create-path classifier that maps stream URIs
# to mimetype='streaming' (counterpart to is_downloadable_remote_video)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'uri',
    [
        'rtsp://camera.local/feed',
        'rtsp://camera/feed.mp4',  # scheme wins over path extension
        'rtmp://media.example.com/live',
        'srt://stream.example.com:9000',
        'udp://stream.example.test:1234',
        'mms://media.example.com/live',
        'https://cdn.example.com/live/index.m3u8',  # HLS over http(s)
        'http://example.com/stream.mpd',  # DASH over http(s)
        'https://example.com/legacy.m3u',
        'https://example.com/smooth.ism',
        'https://cdn.example.com/live/index.m3u8?token=abc',  # query
    ],
)
def test_is_streaming_uri_true_for_streams(uri: str) -> None:
    assert is_streaming_uri(uri) is True


@pytest.mark.parametrize(
    'uri',
    [
        '',
        'https://example.com/clip.mp4',  # downloadable video, not a stream
        'https://example.com/photo.jpg',
        'https://dashboard.example.com/',  # plain web page
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        'file:///tmp/clip.mp4',
    ],
)
def test_is_streaming_uri_false_for_non_streams(uri: str) -> None:
    assert is_streaming_uri(uri) is False


def test_classify_non_http_scheme_returns_stream() -> None:
    """Non-http(s)/non-streaming schemes (file://, ftp://, ...) get
    the negative classify. The classifier deliberately refuses to
    download from anything but well-known network protocols."""
    with mock.patch('anthias_common.remote_video._session.head') as head:
        ok, ext = is_downloadable_remote_video('file:///tmp/clip.mp4')
    assert ok is False
    assert ext == ''
    head.assert_not_called()


def test_classify_empty_uri_returns_stream() -> None:
    ok, ext = is_downloadable_remote_video('')
    assert ok is False
    assert ext == ''


# ---------------------------------------------------------------------------
# HEAD-probe fallback (extensionless / unknown-extension URLs)
# ---------------------------------------------------------------------------


def _fake_head(content_type: str, status_code: int = 200) -> mock.MagicMock:
    """Shape a fake ``requests.head`` response with a given
    Content-Type and status code."""
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.headers = {'Content-Type': content_type}
    return resp


def test_classify_bare_url_falls_back_to_head_probe_video() -> None:
    """No extension on the URL, but HEAD reports ``Content-Type:
    video/mp4`` → auto-download with the inferred extension."""
    with mock.patch(
        'anthias_common.remote_video._session.head',
        return_value=_fake_head('video/mp4'),
    ) as head:
        ok, ext = is_downloadable_remote_video(
            'https://api.example.com/video/12345'
        )
    assert ok is True
    assert ext == '.mp4'
    head.assert_called_once()


def test_classify_head_probe_html_returns_stream() -> None:
    """HEAD reports ``Content-Type: text/html`` (a 404 page, a JSON
    error envelope's text/html landing page, ...) → stay as stream
    URL. The download task would have stored the error page as the
    asset; we want the row to remain a literal-URL stream instead."""
    with mock.patch(
        'anthias_common.remote_video._session.head',
        return_value=_fake_head('text/html; charset=utf-8'),
    ):
        ok, ext = is_downloadable_remote_video(
            'https://api.example.com/video/12345'
        )
    assert ok is False
    assert ext == ''


def test_classify_head_probe_manifest_content_type_returns_stream() -> None:
    """Some HLS origins serve ``application/vnd.apple.mpegurl`` from
    URLs without a ``.m3u8`` extension. Reject those at the HEAD
    probe — downloading the manifest as a single file would store
    the playlist, not the segments it points at."""
    with mock.patch(
        'anthias_common.remote_video._session.head',
        return_value=_fake_head('application/vnd.apple.mpegurl'),
    ):
        ok, ext = is_downloadable_remote_video(
            'https://hls.example.com/stream'
        )
    assert ok is False
    assert ext == ''


def test_classify_head_probe_http_error_returns_stream() -> None:
    """HEAD returns 4xx → stay as stream URL. Some origins respond
    405 Method Not Allowed to HEAD; either way, downgrading to
    stream-mode keeps the create call from failing — the viewer
    will play (or fail to play) the URL as a stream."""
    with mock.patch(
        'anthias_common.remote_video._session.head',
        return_value=_fake_head('video/mp4', status_code=405),
    ):
        ok, ext = is_downloadable_remote_video(
            'https://api.example.com/video/12345'
        )
    assert ok is False
    assert ext == ''


@pytest.mark.parametrize(
    'exc',
    [
        requests.exceptions.Timeout('slow origin'),
        requests.exceptions.ConnectionError('refused'),
        requests.exceptions.TooManyRedirects('loop'),
        requests.exceptions.SSLError('bad cert'),
    ],
)
def test_classify_head_probe_network_failure_returns_stream(
    exc: Exception,
) -> None:
    """Any network exception during the HEAD probe → stay as stream
    URL. The classifier is best-effort; we never block the create
    call on a flaky origin."""
    with mock.patch(
        'anthias_common.remote_video._session.head',
        side_effect=exc,
    ):
        ok, ext = is_downloadable_remote_video(
            'https://api.example.com/video/12345'
        )
    assert ok is False
    assert ext == ''


def test_classify_head_probe_uses_short_timeout() -> None:
    """The synchronous HEAD probe must run with the documented 5s
    timeout — operators are blocking on the POST /assets call. Any
    drift in the timeout constant would slow create requests."""
    with mock.patch(
        'anthias_common.remote_video._session.head',
        return_value=_fake_head('video/mp4'),
    ) as head:
        is_downloadable_remote_video('https://api.example.com/video/12345')
    _, kwargs = head.call_args
    assert kwargs['timeout'] == 5
    assert kwargs['allow_redirects'] is True


# ---------------------------------------------------------------------------
# Destination path
# ---------------------------------------------------------------------------


def test_remote_video_destination_path_uses_assetdir(tmp_path: Path) -> None:
    """The local destination lives under settings['assetdir'] so
    cleanup() recognises the downloaded file as referenced and
    doesn't sweep it as an orphan."""
    # ``AnthiasSettings`` is a ``UserDict`` subclass whose real
    # constructor reads ``~/.anthias/anthias.conf``; for the
    # destination-path test we only need the ``assetdir`` key, so
    # cast a minimal dict to the type to satisfy mypy without
    # spinning up the full config layer.
    fake_settings = cast(AnthiasSettings, {'assetdir': str(tmp_path)})
    result = remote_video_destination_path('abc123', '.mp4', fake_settings)
    assert result == f'{tmp_path}/abc123.mp4'


def test_remote_video_destination_path_preserves_extension() -> None:
    """The extension is the caller's responsibility — pass through
    verbatim. Allows webm/mkv/avi to land with their real container
    so ffprobe identifies them correctly."""
    fake_settings = cast(AnthiasSettings, {'assetdir': '/data'})
    for ext in ('.mp4', '.webm', '.mkv', '.mov'):
        result = remote_video_destination_path('asset-1', ext, fake_settings)
        assert result == f'/data/asset-1{ext}'
