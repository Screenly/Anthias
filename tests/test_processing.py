"""Unit tests for the upload-time normalisation pipeline.

Two tasks under test:

* ``normalize_image_asset`` — every extension in
  ``NORMALIZE_IMAGE_EXTS`` (HEIC / HEIF / TIFF / BMP / ICO / TGA /
  JPEG 2000 family / AVIF) → lossless WebP. JPEG / PNG / WebP / GIF
  / SVG short-circuit through the no-op branch.
* ``normalize_video_asset`` — passthrough or transcode driven by an
  ffprobe call against the source. Transcode target depends on the
  board profile resolved from ``DEVICE_TYPE``: libx264 on legacy Pi
  2 / Pi 3 (no HEVC hardware), libx265 + ``-tag:v hvc1`` on Pi 4-64
  / Pi 5 / x86. The grid lives in ``anthias_server.playback_envelope.ENVELOPE_BY_DEVICE_TYPE``
  and is exercised end-to-end by the per-board parametrised tests.

Fixtures are generated programmatically (Pillow + ffmpeg) so the test
suite is self-contained — no checked-in binary blobs to drift, and
the matrix of formats can grow without a fixture-file dance. The
ffmpeg-driven video fixtures need a real ffmpeg in PATH; the host CI
image already has it (it's already a runtime dep for
``get_video_duration``), and the local-dev path matches once the
``ffmpeg`` apt package is installed.

Tests deliberately exercise the underlying helper functions
(``_run_image_normalisation``, ``_run_video_normalisation``) rather
than the celery wrappers — the wrappers are thin enough that calling
``.run()`` would just retest the same code path while bringing
celery's eager-mode plumbing into scope. The wrapper-side guarantees
(``Task.on_failure`` clearing ``is_processing``, autoretry config)
get their own dedicated tests below.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from os import path
from typing import Any
from unittest import mock

import pytest
import sh
from PIL import Image, UnidentifiedImageError

from anthias_server import playback_envelope, processing
from anthias_server.app.models import Asset
from anthias_server.settings import settings as anthias_settings


def _envelope_summary(
    codec: str,
    width: int,
    height: int,
    fps: float,
    *,
    container: str = 'mp4',
    audio_codec: str = 'aac',
    codec_override: str | None = None,
) -> dict[str, Any]:
    """Hand-build an ``_ffprobe_summary``-shape dict for envelope
    tests. ``codec_override`` lets a test inject an off-envelope
    codec name (e.g. 'prores') while keeping the other axes valid,
    so a single test row exercises exactly one gate. ``audio_codec``
    defaults to 'aac' because audio is orthogonal to the envelope
    decision — it's the existing demuxer-set gate."""
    return {
        'container': container,
        'video_codec': codec_override if codec_override is not None else codec,
        'video_pixels': width * height,
        'video_width': width,
        'video_height': height,
        'video_fps': fps,
        'audio_codec': audio_codec,
        'duration_seconds': 30,
    }


# ---------------------------------------------------------------------------
# Skip markers — keep the suite green on hosts without optional deps
# ---------------------------------------------------------------------------


_FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None
_FFPROBE_AVAILABLE = shutil.which('ffprobe') is not None

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    _HEIF_AVAILABLE = True
except Exception:
    _HEIF_AVAILABLE = False


pytest_ffmpeg = pytest.mark.skipif(
    not (_FFMPEG_AVAILABLE and _FFPROBE_AVAILABLE),
    reason='ffmpeg / ffprobe not on PATH',
)
pytest_heif = pytest.mark.skipif(
    not _HEIF_AVAILABLE,
    reason='pillow-heif not installed (libheif1 missing?)',
)


# ---------------------------------------------------------------------------
# Asset / asset-dir fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def asset_dir(tmp_path: Any) -> Iterator[str]:
    """Point ``settings['assetdir']`` at a fresh per-test tempdir.

    Each test that writes a fixture file lands it under here, and
    asserts on what survives. ``Asset.objects.all().delete()`` clears
    DB rows between tests because ``django_db`` rollback covers
    persisted writes but the celery tasks above call
    ``Asset.objects.update`` directly, which doesn't always observe
    the wrap.
    """
    Asset.objects.all().delete()
    original = anthias_settings['assetdir']
    anthias_settings['assetdir'] = str(tmp_path)
    try:
        yield str(tmp_path)
    finally:
        anthias_settings['assetdir'] = original
        Asset.objects.all().delete()


def _make_processing_asset(
    asset_id: str,
    uri: str,
    mimetype: str = 'image',
    metadata: dict[str, Any] | None = None,
) -> Asset:
    """Persist a row in the state the upload path leaves behind:
    ``is_processing=True``, mimetype set, uri pointing at the upload
    file. The normalisation task takes it from there.
    """
    return Asset.objects.create(
        asset_id=asset_id,
        name=asset_id,
        uri=uri,
        mimetype=mimetype,
        duration=0,
        is_enabled=True,
        is_processing=True,
        play_order=0,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Image-fixture builders (HEIC / HEIF / TIFF / corrupt)
# ---------------------------------------------------------------------------


def _write_image(out_path: str, fmt: str, **save_kwargs: Any) -> str:
    """Synthesise a ~16×16 image in *fmt* and save it to *out_path*.

    Variety: alpha vs no-alpha, RGB vs RGBA, multi-frame TIFF. The
    point is that the conversion path's ``convert('RGBA')`` call
    handles every common Pillow input shape; tests don't need
    photorealistic content, just plausibly-shaped bytes.
    """
    mode = save_kwargs.pop('mode', 'RGBA' if fmt != 'JPEG' else 'RGB')
    colour = save_kwargs.pop(
        'colour',
        (255, 0, 0, 200) if mode == 'RGBA' else (255, 0, 0),
    )
    size = save_kwargs.pop('size', (16, 16))
    image = Image.new(mode, size, colour)
    image.save(out_path, fmt, **save_kwargs)
    return out_path


def _write_corrupt(out_path: str, suffix: str) -> str:
    """A file that decoders will reject — header bytes from one
    format, body cut short. Pillow raises ``UnidentifiedImageError``
    on these; the normalisation task surfaces the failure via the
    metadata.error_message contract."""
    with open(out_path, 'wb') as fh:
        # 'GIF89a' is enough to fool extension sniffing but Pillow
        # rejects it as an invalid GIF mid-decode.
        fh.write(b'GIF89a' + b'\x00' * 8)
    return out_path


# ---------------------------------------------------------------------------
# Video-fixture builders (h264 mp4, hevc mkv, mpeg2 mpg, prores mov, ...)
# ---------------------------------------------------------------------------


def _make_video(
    out_path: str,
    *,
    codec: str = 'libx264',
    container: str | None = None,
    audio: str | None = 'aac',
    extra_args: tuple[str, ...] = (),
    duration_s: float = 0.5,
) -> str:
    """Synthesise a tiny clip with a chosen codec / container / audio.

    Why ffmpeg instead of a binary fixture: the matrix of codecs the
    task branches on (h264 vs hevc vs mpeg2 vs prores vs mjpeg, with
    or without audio, mp4 vs mkv vs mov vs mpg) would grow into ~20
    MB of binary blobs in-tree. A few seconds of ffmpeg per test is
    cheaper. Each clip is half a second of solid-colour video at
    32×32 — enough for ffprobe to identify codecs and for libx264 to
    produce a valid output, small enough that even the worst
    transcode finishes in well under a second.
    """
    args = [
        'ffmpeg',
        '-hide_banner',
        '-y',
        '-loglevel',
        'error',
        # ``lavfi`` source: synthesised SMPTE colour bars, deterministic.
        '-f',
        'lavfi',
        '-i',
        f'color=c=blue:s=32x32:d={duration_s}:r=10',
    ]
    if audio:
        args += [
            '-f',
            'lavfi',
            '-i',
            f'sine=f=440:d={duration_s}',
            '-c:a',
            audio,
        ]
    args += ['-c:v', codec]
    args += list(extra_args)
    if container:
        args += ['-f', container]
    args += [out_path]
    subprocess.run(args, check=True, timeout=60)
    return out_path


# ---------------------------------------------------------------------------
# IMAGE normalisation tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('fmt', 'ext'),
    [
        # TIFF — both extension spellings must route through the
        # same path; covers the multi-page-flatten case implicitly
        # (Pillow opens the first frame).
        ('TIFF', '.tif'),
        ('TIFF', '.tiff'),
        # BMP — uncompressed source, dramatic size win after WebP.
        ('BMP', '.bmp'),
        # ICO — Windows icon. Pillow saves+reloads as a single
        # frame, which is what we want for a signage asset.
        ('ICO', '.ico'),
        # TGA — Truevision Targa, common in screenshot tools.
        ('TGA', '.tga'),
        # JPEG 2000 family — every Pillow-recognised extension we
        # accept must round-trip.
        ('JPEG2000', '.jp2'),
        ('JPEG2000', '.j2k'),
        ('JPEG2000', '.jpx'),
        # AVIF — modern phone camera output. Pillow 12+ supports
        # AVIF natively; round-trip proves the libavif binding is
        # actually linked at runtime, not just registered.
        ('AVIF', '.avif'),
    ],
)
def test_image_normalises_to_lossless_webp_across_formats(
    asset_dir: str, fmt: str, ext: str
) -> None:
    """Every entry in ``NORMALIZE_IMAGE_EXTS`` produces a valid WebP
    output: the original is removed, the row's URI is swapped to the
    new ``.webp`` path, ``metadata.original_ext`` carries the source
    extension, and the resulting file actually decodes back as WebP
    (not a stub or a bytewise-renamed source).

    Parametrising over the full grid catches a future change to
    ``_convert_image_to_webp`` that breaks one decoder while leaving
    the others intact — e.g. a move to a non-RGBA convert mode would
    crash JPEG2000 (which Pillow opens in mode 'RGB' by default for
    some files), and a Pillow version drop that loses libavif would
    fail AVIF specifically. Each case is one assertion per
    invariant; failures point at one row in the matrix."""
    src = path.join(asset_dir, f'fixture{ext}')
    # AVIF needs RGB (Pillow's libavif binding doesn't accept RGBA on
    # write); ICO/TGA/JP2/BMP all accept RGBA. The conversion target
    # (``_convert_image_to_webp``'s internal ``.convert('RGBA')``) is
    # what unifies — just make sure the SOURCE encodes happily.
    if fmt in ('AVIF', 'JPEG2000'):
        _write_image(src, fmt, mode='RGB', colour=(0, 200, 0))
    else:
        _write_image(src, fmt)
    asset = _make_processing_asset(f'img-{fmt.lower()}', src)

    with mock.patch.object(processing, '_notify') as notify:
        processing._run_image_normalisation(asset)

    asset.refresh_from_db()
    expected_uri = path.join(asset_dir, 'fixture.webp')
    assert asset.uri == expected_uri
    assert path.isfile(expected_uri)
    assert not path.exists(src), f'original {ext} must be removed'
    assert asset.is_processing is False
    assert asset.mimetype == 'image'
    assert asset.metadata['original_ext'] == ext
    assert asset.metadata['converted'] is True
    notify.assert_called_once_with(f'img-{fmt.lower()}')

    # Round-trip the WebP — proves the file isn't a stub.
    with Image.open(expected_uri) as im:
        assert im.format == 'WEBP'
        assert im.size == (16, 16)


@pytest_heif
@pytest.mark.django_db
@pytest.mark.parametrize('ext', ['.heic', '.heif', '.HEIC'])
def test_image_heif_converts_to_lossless_webp(
    asset_dir: str, ext: str
) -> None:
    """HEIC / HEIF (case-insensitive) → WebP. RGBA so the alpha
    handling on the WebP write path is exercised even though HEIF
    sources commonly arrive as RGB."""
    src = path.join(asset_dir, f'fixture{ext}')
    _write_image(src, 'HEIF', mode='RGB', colour=(0, 200, 0))
    asset = _make_processing_asset('img-heif', src)

    with mock.patch.object(processing, '_notify'):
        processing._run_image_normalisation(asset)

    asset.refresh_from_db()
    expected_uri = path.join(asset_dir, 'fixture.webp')
    assert asset.uri == expected_uri
    assert path.isfile(expected_uri)
    assert not path.exists(src)
    # ``original_ext`` carries the lowercased extension regardless of
    # the case the file landed with.
    assert asset.metadata['original_ext'] == ext.lower()
    assert asset.metadata['converted'] is True


@pytest.mark.django_db
def test_image_corrupt_input_raises_clean_error(asset_dir: str) -> None:
    """Pillow's UnidentifiedImageError must bubble out so the
    on_failure hook can write metadata.error_message — never leave a
    half-written staging file behind."""
    src = path.join(asset_dir, 'broken.tiff')
    _write_corrupt(src, '.tiff')
    asset = _make_processing_asset('img-bad', src)

    with mock.patch.object(processing, '_notify'):
        with pytest.raises(UnidentifiedImageError):
            processing._run_image_normalisation(asset)

    # No webp produced; the source file is left in place for the
    # operator to inspect / re-upload. Staging .webp.tmp must also
    # be gone — same contract as the video pipeline's _drop_staging.
    assert not path.exists(path.join(asset_dir, 'broken.webp'))
    assert not path.exists(path.join(asset_dir, 'broken.webp.tmp'))
    assert path.exists(src)


@pytest.mark.django_db
def test_image_decompression_bomb_is_rejected(asset_dir: str) -> None:
    """A malicious image (or a misclassified scan) advertising
    enormous dimensions must be rejected *before* any pixel decode
    happens. The check reads ``image.size`` from the format header
    and raises a ValueError that on_failure surfaces via
    ``metadata.error_message`` — same contract as a corrupt input.

    Mocks ``Image.open`` to return a stub whose ``.size`` exceeds
    the cap, so the test runs without writing a real billion-pixel
    fixture (which would itself need GBs of memory at create time)."""
    src = path.join(asset_dir, 'bomb.tiff')
    _write_image(src, 'TIFF')  # real fixture that would otherwise decode
    asset = _make_processing_asset('img-bomb', src)

    bomb_size = (processing._MAX_IMAGE_PIXELS // 8 + 1, 8)

    class _FakeImage:
        size = bomb_size
        format = 'TIFF'

        def __enter__(self) -> '_FakeImage':
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def convert(self, _mode: str) -> 'mock.MagicMock':
            # Should never be reached — the size check raises first.
            raise AssertionError(
                'convert() called on a bomb input — size check missed'
            )

        def save(self, *_: object, **__: object) -> None:
            raise AssertionError('save() called on a bomb input')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch(
            'anthias_server.processing.Image.open',
            return_value=_FakeImage(),
        ),
    ):
        with pytest.raises(ValueError, match='exceed cap'):
            processing._run_image_normalisation(asset)

    # Staging cleanup contract still holds for the bomb path.
    leftover = [n for n in os.listdir(asset_dir) if n.endswith('.webp.tmp')]
    assert not leftover, f'image staging leftover: {leftover}'


@pytest.mark.django_db
def test_image_partial_write_cleans_staging(asset_dir: str) -> None:
    """If Pillow writes some bytes to the staging file and *then*
    raises mid-encode (disk pressure, codec crash), the runner must
    clean up the partial .webp.tmp before propagating. Mocking
    ``_convert_image_to_webp`` to half-write + raise is the cheapest
    way to exercise that path deterministically — a real OSError
    mid-WebP-encode is hard to provoke from userspace."""
    src = path.join(asset_dir, 'fixture.tiff')
    _write_image(src, 'TIFF')
    asset = _make_processing_asset('img-partial', src)

    def half_write(_in: str, staging: str) -> None:
        with open(staging, 'wb') as fh:
            fh.write(b'partial WebP bytes')
        raise OSError('disk full mid-encode')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_convert_image_to_webp', side_effect=half_write
        ),
    ):
        with pytest.raises(OSError, match='disk full'):
            processing._run_image_normalisation(asset)

    # No leftover .webp.tmp in the asset dir — the runner removed it
    # before the raise propagated.
    leftover = [n for n in os.listdir(asset_dir) if n.endswith('.webp.tmp')]
    assert not leftover, f'image staging leftover: {leftover}'


@pytest.mark.django_db
def test_image_rename_failure_cleans_staging(asset_dir: str) -> None:
    """The atomic ``os.replace(staging, final_uri)`` normally succeeds
    in <1ms, but a filesystem-full / permissions / cross-device error
    there would otherwise leave the .webp.tmp behind. Wrap-the-rename
    contract: any OSError on rename drops the staging file before
    propagating, matching the timeout/error/zero-byte branches."""
    src = path.join(asset_dir, 'fixture.tiff')
    _write_image(src, 'TIFF')
    asset = _make_processing_asset('img-rename-fail', src)

    real_replace = os.replace

    def boom(staging: str, final_uri: str) -> None:
        # Verify the staging file actually exists before we explode —
        # otherwise the test would also pass if Pillow never wrote.
        assert path.isfile(staging), 'precondition: staging must exist'
        raise OSError('simulated cross-device rename failure')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch('anthias_server.processing.os.replace', side_effect=boom),
    ):
        with pytest.raises(OSError, match='cross-device'):
            processing._run_image_normalisation(asset)

    leftover = [n for n in os.listdir(asset_dir) if n.endswith('.webp.tmp')]
    assert not leftover, f'image staging leftover after rename: {leftover}'
    # The real replace was never called.
    del real_replace


@pytest.mark.django_db
def test_image_missing_file_raises_filenotfound(asset_dir: str) -> None:
    """Source file disappeared between row creation and task
    pickup (cleanup raced operator, disk pressure). Fail clean so
    on_failure writes the error and clears the flag."""
    src = path.join(asset_dir, 'gone.tiff')
    asset = _make_processing_asset('img-gone', src)

    with mock.patch.object(processing, '_notify'):
        with pytest.raises(FileNotFoundError):
            processing._run_image_normalisation(asset)


@pytest.mark.django_db
def test_image_jpeg_routes_no_op(asset_dir: str) -> None:
    """A caller that mis-routed a JPEG (or .png, .webp) through this
    task must not re-encode it — the row already plays. Just clear
    is_processing and move on. Defensive guard for future call sites."""
    src = path.join(asset_dir, 'photo.jpg')
    _write_image(src, 'JPEG', mode='RGB')
    asset = _make_processing_asset('img-jpeg', src)

    with mock.patch.object(processing, '_notify') as notify:
        processing._run_image_normalisation(asset)

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert asset.uri == src  # untouched
    assert path.exists(src)
    notify.assert_called_once_with('img-jpeg')


@pytest.mark.django_db
def test_image_no_op_path_clears_stale_error_message(asset_dir: str) -> None:
    """A row being re-uploaded after a previous failed conversion
    carries ``metadata.error_message``. When the new upload is a
    format the pipeline doesn't convert (JPEG/PNG/etc.), the no-op
    branch must still wipe the stale error so the operator's table
    doesn't keep showing the "Failed" pill on a row that's now
    fine."""
    src = path.join(asset_dir, 'photo.jpg')
    _write_image(src, 'JPEG', mode='RGB')
    asset = _make_processing_asset(
        'img-retry-jpeg',
        src,
        metadata={'error_message': 'previous attempt: libheif crashed'},
    )

    with mock.patch.object(processing, '_notify'):
        processing._run_image_normalisation(asset)

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert 'error_message' not in asset.metadata


@pytest.mark.django_db
def test_image_row_already_finalized_no_op(asset_dir: str) -> None:
    """Duplicate task fire on an already-finalised row → no-op. Same
    contract download_youtube_asset enforces."""
    src = path.join(asset_dir, 'fixture.tiff')
    _write_image(src, 'TIFF')
    Asset.objects.create(
        asset_id='img-done',
        name='img-done',
        uri=src,
        mimetype='image',
        duration=10,
        is_processing=False,
    )

    asset = processing._row_or_none('img-done')
    assert asset is None  # task body would short-circuit here


@pytest.mark.django_db
def test_image_row_missing_no_op() -> None:
    """Row deleted between dispatch and pickup → no-op."""
    assert processing._row_or_none('does-not-exist') is None


@pytest.mark.django_db
def test_image_pipeline_clears_prior_error(asset_dir: str) -> None:
    """A re-uploaded asset whose previous attempt left an error
    message in metadata must clear that on success — the operator's
    next refresh shouldn't show a stale failure on a now-good row."""
    src = path.join(asset_dir, 'fixture.tiff')
    _write_image(src, 'TIFF')
    asset = _make_processing_asset(
        'img-retry',
        src,
        metadata={'error_message': 'previous run failed'},
    )

    with mock.patch.object(processing, '_notify'):
        processing._run_image_normalisation(asset)

    asset.refresh_from_db()
    assert 'error_message' not in asset.metadata
    assert asset.metadata['converted'] is True


@pytest.mark.django_db
def test_set_processing_error_writes_metadata(asset_dir: str) -> None:
    """Direct test of the failure-state contract: error message
    persisted, is_processing cleared, prior metadata preserved,
    and the row disabled so the viewer's scheduler can't pick up
    a known-bad asset."""
    asset = Asset.objects.create(
        asset_id='img-err',
        name='img-err',
        uri=path.join(asset_dir, 'broken.tiff'),
        mimetype='image',
        duration=10,
        # Row was active before the failure (the operator enabled it
        # at create time). The error path must flip is_enabled too.
        is_enabled=True,
        is_processing=True,
        play_order=0,
        metadata={'original_ext': '.tiff'},
    )
    processing._set_processing_error(asset.asset_id, 'libheif: bad input')

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert asset.metadata['error_message'] == 'libheif: bad input'
    # Earlier metadata keys are merged, not stomped on.
    assert asset.metadata['original_ext'] == '.tiff'
    # Row disabled: the viewer's scheduling.generate_asset_list
    # filters on is_enabled+date, not metadata.error_message — so
    # without this flip a failed conversion would still get queued
    # for playback even though the file at uri is unplayable.
    assert asset.is_enabled is False


# ---------------------------------------------------------------------------
# VIDEO normalisation tests
# ---------------------------------------------------------------------------


@pytest_ffmpeg
@pytest.mark.django_db
def test_video_h264_mp4_passes_through(asset_dir: str) -> None:
    """The bread-and-butter case: an H.264 MP4 with AAC audio. ffmpeg
    is *not* called; the row gets duration + metadata + is_processing
    cleared, file untouched on disk."""
    src = path.join(asset_dir, 'sample.mp4')
    _make_video(src, codec='libx264', container='mp4', audio='aac')
    asset = _make_processing_asset('vid-h264', src, mimetype='video')

    pre_size = os.stat(src).st_size
    with mock.patch.object(processing, '_notify') as notify:
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.uri == src  # passthrough — no rename
    assert asset.is_processing is False
    assert asset.metadata['original_ext'] == '.mp4'
    assert asset.metadata['transcoded'] is False
    # Duration has been probed in (>= 1 second floor).
    assert asset.duration is not None and asset.duration >= 1
    assert os.stat(src).st_size == pre_size  # bytes untouched
    notify.assert_called_once_with('vid-h264')


@pytest_ffmpeg
@pytest.mark.django_db
@pytest.mark.parametrize(
    ('device_type', 'codec'),
    [
        # H.264 board → libx264 mp4 source passes through; HEVC
        # source on the same board re-encodes to H.264.
        ('pi3', 'libx264'),
        # HEVC board → libx265 mp4 source passes through; H.264
        # source on the same board re-encodes to HEVC.
        ('pi4-64', 'libx265'),
        ('pi5', 'libx265'),
        ('x86', 'libx265'),
    ],
)
def test_video_passthrough_when_source_matches_board_envelope(
    asset_dir: str,
    device_type: str,
    codec: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source in the board's envelope codec, packaged as mp4,
    passes through. The variant ``<id>.mp4`` is a byte-identical
    copy of the original sibling. Non-mp4 containers always
    transcode because the variant slot is fixed at ``.mp4``."""
    monkeypatch.setenv('DEVICE_TYPE', device_type)
    src = path.join(asset_dir, 'sample.mp4')
    _make_video(src, codec=codec, container='mp4', audio='aac')
    asset = _make_processing_asset('vid-pass', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.metadata['transcoded'] is False
    assert asset.uri == src


@pytest_ffmpeg
@pytest.mark.django_db
def test_video_silent_passes_through(asset_dir: str) -> None:
    """A muted clip (no audio stream at all) must passthrough — the
    audio_codec=='none' branch is the third leg of
    _video_can_passthrough and the easiest to regress."""
    src = path.join(asset_dir, 'silent.mp4')
    _make_video(src, codec='libx264', container='mp4', audio=None)
    asset = _make_processing_asset('vid-silent', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.metadata['transcoded'] is False


@pytest_ffmpeg
@pytest.mark.django_db
@pytest.mark.parametrize(
    ('codec', 'ext', 'container', 'extra'),
    [
        # MPEG-2 in an MPEG-PS container — common camcorder dump.
        ('mpeg2video', '.mpg', 'mpeg', ()),
        # Motion JPEG — exotic but ffmpeg-supported.
        ('mjpeg', '.avi', 'avi', ('-q:v', '5')),
    ],
)
def test_video_exotic_codec_transcodes_to_h264_mp4(
    asset_dir: str,
    codec: str,
    ext: str,
    container: str,
    extra: tuple[str, ...],
) -> None:
    """Codecs outside the passthrough set become H.264 + AAC MP4. The
    output filename ends in .mp4 regardless of the source extension;
    the source file is removed once the .mp4 is in place."""
    src = path.join(asset_dir, f'fixture{ext}')
    _make_video(
        src, codec=codec, container=container, audio='mp2', extra_args=extra
    )
    asset = _make_processing_asset('vid-tc', src, mimetype='video')

    with mock.patch.object(processing, '_notify') as notify:
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    final_uri = path.join(asset_dir, 'fixture.mp4')
    assert asset.uri == final_uri
    assert path.isfile(final_uri)
    assert not path.exists(src), 'original must be removed after transcode'
    assert asset.metadata['transcoded'] is True
    assert asset.metadata['original_ext'] == ext
    assert asset.is_processing is False
    notify.assert_called_once_with('vid-tc')

    # Verify the output is actually H.264 in a passthrough-eligible
    # container — not just an extension-rename of the source. The
    # container check uses the passthrough set rather than asserting
    # ``container == 'mp4'`` directly because ffprobe's
    # format.format_name reports a comma-joined synonym list for MP4
    # files (e.g. ``mov,mp4,m4a,3gp,3g2,mj2``); _ffprobe_summary
    # picks whichever token first matches the passthrough set, and
    # the exact pick is implementation detail.
    summary = processing._ffprobe_summary(final_uri)
    assert summary['video_codec'] == 'h264'
    assert summary['container'] in processing._PASSTHROUGH_CONTAINERS


@pytest_ffmpeg
@pytest.mark.django_db
def test_video_non_h264_mp4_is_transcoded_in_place(asset_dir: str) -> None:
    """An MP4-container with a non-passthrough codec needs a transcode
    even though the extension is already .mp4. Test the staging
    rename: source must NOT be truncated mid-read by the output going
    to the same path. Output ends up at the same `<base>.mp4` URI."""
    src = path.join(asset_dir, 'fixture.mp4')
    # MPEG-4 Part 2 (xvid-style). Neither h264 nor hevc → must
    # transcode despite landing in mp4.
    _make_video(src, codec='mpeg4', container='mp4', audio='aac')
    pre_inode = os.stat(src).st_ino
    asset = _make_processing_asset('vid-mpeg4', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.uri == src
    summary = processing._ffprobe_summary(src)
    assert summary['video_codec'] == 'h264'
    # Post-transcode the on-disk inode must differ — the staging
    # rename replaced the original; we did not in-place truncate.
    assert os.stat(src).st_ino != pre_inode


@pytest.mark.django_db
def test_video_missing_file_raises_filenotfound(asset_dir: str) -> None:
    src = path.join(asset_dir, 'gone.mp4')
    asset = _make_processing_asset('vid-gone', src, mimetype='video')
    with mock.patch.object(processing, '_notify'):
        with pytest.raises(FileNotFoundError):
            processing._run_video_normalisation(asset)


@pytest.mark.django_db
def test_video_ffprobe_failure_falls_through_to_transcode(
    asset_dir: str,
) -> None:
    """A probe that crashes (corrupt header) returns 'unknown' for
    every dimension; _video_can_passthrough rejects unknowns so the
    code falls through to transcode. We mock ffprobe to verify the
    branch wires up — running the real probe on a synthetic corrupt
    file is non-deterministic across ffprobe versions."""
    src = path.join(asset_dir, 'broken.mp4')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 32)
    asset = _make_processing_asset('vid-broken', src, mimetype='video')

    fake_summary = {
        'container': 'unknown',
        'video_codec': 'unknown',
        'audio_codec': 'unknown',
        'duration_seconds': None,
    }

    def fake_transcode(_in: str, out: str, **kwargs: Any) -> None:
        with open(out, 'wb') as fh:
            fh.write(b'\x00\x00\x00\x18ftypmp42')  # 24-byte stub

    def fake_probe_post(uri: str) -> int | None:
        return 5  # mocked duration

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=fake_transcode
        ),
        mock.patch.object(
            processing,
            '_resolve_duration_seconds',
            side_effect=fake_probe_post,
        ),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.metadata['transcoded'] is True
    assert asset.duration == 5


@pytest.mark.django_db
def test_video_ffmpeg_timeout_cleans_staging(asset_dir: str) -> None:
    """ffmpeg time-limit overrun: staging file removed, RuntimeError
    raised so on_failure clears is_processing. Mocking the transcode
    helper directly because reproducing a real time-limit kill in a
    unit test is brittle (depends on subprocess scheduling)."""
    src = path.join(asset_dir, 'bigfile.mov')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 256)
    asset = _make_processing_asset('vid-timeout', src, mimetype='video')

    summary = {
        'container': 'mov',
        'video_codec': 'prores',  # not passthrough
        'audio_codec': 'aac',
    }

    def explode(_in: str, staging: str, **kwargs: Any) -> None:
        # Half-write the staging file so the cleanup branch has
        # something to remove — proves we don't leak orphans.
        with open(staging, 'wb') as fh:
            fh.write(b'partial')
        raise sh.TimeoutException(
            exit_code=124,
            full_cmd='ffmpeg ...',
        )

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=explode
        ),
    ):
        with pytest.raises(RuntimeError):
            processing._run_video_normalisation(asset)

    # Staging file was cleaned up.
    leftover = [
        n for n in os.listdir(asset_dir) if n.startswith('bigfile.mp4')
    ]
    assert not leftover, f'staging leftover: {leftover}'


@pytest.mark.django_db
def test_video_ffmpeg_error_cleans_staging(asset_dir: str) -> None:
    """Same shape as the timeout test but for a non-zero ffmpeg
    exit. RuntimeError must include stderr so the operator gets a
    diagnostic in metadata.error_message."""
    src = path.join(asset_dir, 'bad.avi')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 16)
    asset = _make_processing_asset('vid-fail', src, mimetype='video')

    summary = {
        'container': 'avi',
        'video_codec': 'cinepak',
        'audio_codec': 'pcm_s16le',
    }

    def explode(_in: str, staging: str, **kwargs: Any) -> None:
        with open(staging, 'wb') as fh:
            fh.write(b'')
        # ``ErrorReturnCode`` is the abstract parent — sh exports
        # numeric subclasses (ErrorReturnCode_1, ..._127) for each
        # exit code. The processing code catches the parent class so
        # the test can raise any subclass.
        raise sh.ErrorReturnCode_1(
            full_cmd='ffmpeg ...',
            stdout=b'',
            stderr=b'Invalid data found',
            truncate=False,
        )

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=explode
        ),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            processing._run_video_normalisation(asset)

    msg = str(excinfo.value)
    assert 'Invalid data found' in msg
    # The error message goes straight into metadata.error_message
    # which renders on the operator-facing "Failed" pill — must NOT
    # contain a Python bytes repr (``b'...'``) wrapper.
    assert "b'Invalid" not in msg, (
        'stderr should be decoded for operator display'
    )


def test_format_subprocess_stderr_decodes_and_trims() -> None:
    """``_format_subprocess_stderr`` must produce operator-readable
    text: bytes decoded as UTF-8 (with replacement for malformed
    bytes), no ``b'...'`` wrapper, very long output trimmed to its
    tail (where ffmpeg's actual diagnostic lives)."""
    # 1) Plain bytes decode cleanly.
    exc = sh.ErrorReturnCode_1(
        full_cmd='ffmpeg ...',
        stdout=b'',
        stderr=b'Invalid data found in input\n',
        truncate=False,
    )
    out = processing._format_subprocess_stderr(exc)
    assert out == 'Invalid data found in input'
    assert "b'" not in out

    # 2) Malformed UTF-8 doesn't crash — replacement char is fine.
    exc = sh.ErrorReturnCode_1(
        full_cmd='ffmpeg ...',
        stdout=b'',
        stderr=b'broken\xff byte',
        truncate=False,
    )
    out = processing._format_subprocess_stderr(exc)
    assert 'broken' in out and 'byte' in out

    # 3) Long stderr is tail-trimmed with an ellipsis prefix so the
    # operator sees the diagnostic, not 4 KB of build-info preamble.
    long_tail = 'final-error-line'
    big = b'x' * 2000 + long_tail.encode()
    exc = sh.ErrorReturnCode_1(
        full_cmd='ffmpeg ...',
        stdout=b'',
        stderr=big,
        truncate=False,
    )
    out = processing._format_subprocess_stderr(exc)
    assert out.startswith('…')
    assert long_tail in out
    assert len(out) <= processing._STDERR_TAIL_BYTES + 1

    # 4) Empty stderr returns the empty string, not "b''".
    exc = sh.ErrorReturnCode_1(
        full_cmd='ffmpeg ...',
        stdout=b'',
        stderr=b'',
        truncate=False,
    )
    assert processing._format_subprocess_stderr(exc) == ''


@pytest.mark.django_db
def test_video_zero_byte_output_fails_clean(asset_dir: str) -> None:
    """ffmpeg sometimes returns exit 0 but produces an empty file
    (broken stream, codec mismatch the syntax would have rejected
    in newer builds). The task must reject the empty output and
    raise — never advertise a 0-byte .mp4 as ready."""
    src = path.join(asset_dir, 'odd.mov')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 16)
    asset = _make_processing_asset('vid-empty', src, mimetype='video')

    summary = {
        'container': 'mov',
        'video_codec': 'prores',
        'audio_codec': 'aac',
    }

    def empty_transcode(_in: str, staging: str, **kwargs: Any) -> None:
        with open(staging, 'wb') as fh:
            fh.write(b'')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=empty_transcode
        ),
    ):
        with pytest.raises(RuntimeError, match='no output'):
            processing._run_video_normalisation(asset)

    # The empty staging file must be removed too, not just the
    # error raised — otherwise cleanup() would have to GC it
    # later via the orphan-file sweep. Same contract as the
    # timeout/error branches above.
    leftover = [n for n in os.listdir(asset_dir) if 'staging' in n]
    assert not leftover, f'staging leftover after empty output: {leftover}'


@pytest.mark.django_db
def test_video_rename_failure_cleans_staging(asset_dir: str) -> None:
    """Video pipeline mirrors the image-pipeline contract: an OSError
    on the post-transcode ``os.replace(staging, final_uri)`` (disk
    full, permissions, cross-device) drops the .staging.mp4 file
    before propagating."""
    src = path.join(asset_dir, 'odd.mov')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 16)
    asset = _make_processing_asset('vid-rename-fail', src, mimetype='video')

    summary = {
        'container': 'mov',
        'video_codec': 'prores',
        'audio_codec': 'aac',
    }

    def good_transcode(_in: str, staging: str, **kwargs: Any) -> None:
        with open(staging, 'wb') as fh:
            fh.write(b'\x00\x00\x00\x18ftypmp42')

    def boom(staging: str, final_uri: str) -> None:
        assert path.isfile(staging), 'precondition: staging must exist'
        raise OSError('simulated rename failure')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=good_transcode
        ),
        mock.patch('anthias_server.processing.os.replace', side_effect=boom),
    ):
        with pytest.raises(OSError, match='rename failure'):
            processing._run_video_normalisation(asset)

    leftover = [n for n in os.listdir(asset_dir) if n.endswith('.staging.mp4')]
    assert not leftover, f'video staging leftover after rename: {leftover}'


# ---------------------------------------------------------------------------
# ffprobe summary parsing — tested independently of the runner
# ---------------------------------------------------------------------------


def test_ffprobe_summary_handles_missing_streams() -> None:
    """A probe response missing audio/video streams is reported as
    'none' (audio absent — passthrough OK if the rest matches) or
    'unknown' (video absent — never passthrough). Defends the
    passthrough decision against ffprobe schema drift."""
    fake_probe_payload = {
        'format': {},
        'streams': [
            # No video stream in this payload — the file is audio-only
            # for the purpose of this test (a podcast-style .m4a was
            # mis-routed to the video pipeline).
            {'codec_type': 'audio', 'codec_name': 'aac'},
        ],
    }
    with mock.patch.object(
        processing, '_ffprobe_streams', return_value=fake_probe_payload
    ):
        summary = processing._ffprobe_summary('fixture.mp4')
    assert summary['audio_codec'] == 'aac'
    assert summary['video_codec'] == 'unknown'


def test_ffprobe_summary_handles_no_audio_track() -> None:
    fake = {
        'format': {},
        'streams': [
            {'codec_type': 'video', 'codec_name': 'h264'},
        ],
    }
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.mp4')
    assert summary['video_codec'] == 'h264'
    assert summary['audio_codec'] == 'none'


@pytest.mark.parametrize(
    ('r_frame_rate', 'expected_fps'),
    [
        # Integer rates land cleanly.
        ('30/1', 30.0),
        ('60/1', 60.0),
        ('25/1', 25.0),
        # NTSC drop-frame: 30000/1001 ≈ 29.97.
        ('30000/1001', 29.97002997002997),
        # 60000/1001 ≈ 59.94 (NTSC 60).
        ('60000/1001', 59.94005994005994),
        # Garbage values collapse to None so the envelope cap
        # treats the source as "we can't tell" and skips the fps
        # gate — codec / resolution gates still fire.
        ('bogus', None),
        ('60', None),  # no slash → no rational, drop to None
        ('0/0', None),  # denominator 0 → no fps
    ],
)
def test_ffprobe_summary_parses_video_fps(
    r_frame_rate: str, expected_fps: float | None
) -> None:
    """``video_fps`` is the average frame rate parsed from
    ffprobe's ``r_frame_rate`` rational. The envelope transcode
    uses it to decide when to emit ``-r envelope.max_fps`` — only
    when source fps > cap. Garbage / zero-denominator → ``None``."""
    fake = {
        'format': {},
        'streams': [
            {
                'codec_type': 'video',
                'codec_name': 'h264',
                'r_frame_rate': r_frame_rate,
            },
        ],
    }
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.mp4')
    if expected_fps is None:
        assert summary['video_fps'] is None
    else:
        assert summary['video_fps'] == pytest.approx(expected_fps)


def test_ffprobe_summary_prefers_format_name_over_filename_extension() -> None:
    """Defensive: ffprobe-reported ``format.format_name`` beats the
    filename. A ``.bin`` file that's actually an MP4 must classify
    as passthrough-eligible — and a ``.mp4`` file whose bytes are
    actually a non-passthrough format (e.g. ``avi``) must classify
    out of the passthrough set despite the misleading extension."""
    # MP4 bytes hidden behind an arbitrary extension.
    mp4_format_name = 'mov,mp4,m4a,3gp,3g2,mj2'
    fake = {
        'format': {'format_name': mp4_format_name},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264'}],
    }
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.bin')
    # The picked token matches the passthrough set.
    assert summary['container'] in processing._PASSTHROUGH_CONTAINERS

    # AVI bytes hidden behind a `.mp4` filename — must NOT pass
    # through. avi is intentionally in the passthrough list (h264
    # in avi is fine), but if format.format_name returns just
    # 'foo' (made up, not in our set) we report that token verbatim
    # so the caller falls through to transcode.
    fake = {
        'format': {'format_name': 'unsupported_format'},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264'}],
    }
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.mp4')
    assert summary['container'] == 'unsupported_format'
    assert summary['container'] not in processing._PASSTHROUGH_CONTAINERS


@pytest.mark.parametrize(
    ('format_name', 'description'),
    [
        # Real ffprobe format_name strings observed for each container
        # in the passthrough set. The decision must classify them all
        # as eligible — without ``mpegts`` / ``matroska`` in the set,
        # an MPEG-TS or MKV upload would force an unnecessary transcode
        # despite both being playable on every Anthias-supported board.
        ('mov,mp4,m4a,3gp,3g2,mj2', '.mp4 / .m4v / .mov'),
        ('matroska,webm', '.mkv / .webm'),
        ('mpegts', '.ts'),
        ('mpeg', '.mpg / .mpeg'),
        ('flv', '.flv'),
        ('avi', '.avi'),
    ],
)
def test_passthrough_containers_match_real_ffprobe_format_names(
    format_name: str, description: str
) -> None:
    """Every container that's listed in ``_PASSTHROUGH_CONTAINERS`` as
    a "we accept this" must actually match what ffprobe writes for
    real files of that container — not just the file's extension.

    Regression: ffprobe reports ``mpegts`` for .ts (not ``ts``) and
    ``matroska`` for .mkv (not ``mkv``). The passthrough set used to
    carry only the short extension labels, so MPEG-TS uploads were
    being unnecessarily re-encoded. Adding the canonical ffprobe
    names to the set keeps the decision consistent between the
    extension-fallback and format_name-driven detection paths.
    """
    fake = {
        'format': {'format_name': format_name},
        'streams': [
            {'codec_type': 'video', 'codec_name': 'h264'},
            {'codec_type': 'audio', 'codec_name': 'aac'},
        ],
    }
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.unused')
    assert summary['container'] in processing._PASSTHROUGH_CONTAINERS, (
        f'{description}: ffprobe format_name={format_name!r} resolved to '
        f'{summary["container"]!r} which is not in _PASSTHROUGH_CONTAINERS'
    )
    # The passthrough decision itself moved to the envelope check
    # in ``_video_can_passthrough`` (container must equal
    # envelope.container_ext, always 'mp4'). This test only locks
    # in the format_name → container mapping in
    # ``_ffprobe_summary``; the broader passthrough behaviour is
    # covered by ``test_video_can_passthrough_respects_envelope``.


def test_ffprobe_summary_falls_back_to_extension_when_format_missing() -> None:
    """When ffprobe doesn't populate ``format.format_name`` (older
    ffprobe builds, malformed input), fall back to the filename
    extension so we still get a deterministic answer rather than
    raising."""
    fake: dict[str, Any] = {'format': {}, 'streams': []}
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.mkv')
    assert summary['container'] == 'mkv'


def test_ffprobe_summary_handles_probe_failure() -> None:
    """Probe errors (corrupt file, ffprobe missing) must not crash
    the task — they downgrade to 'unknown' so the caller falls
    through to transcode."""
    with mock.patch.object(
        processing,
        '_ffprobe_streams',
        side_effect=sh.ErrorReturnCode_1(
            full_cmd='ffprobe ...',
            stdout=b'',
            stderr=b'invalid',
            truncate=False,
        ),
    ):
        summary = processing._ffprobe_summary('fixture.mp4')
    assert summary == {
        'container': 'unknown',
        'video_codec': 'unknown',
        'video_pixels': None,
        'video_width': None,
        'video_height': None,
        'video_fps': None,
        'audio_codec': 'unknown',
        'duration_seconds': None,
    }


def test_ffprobe_summary_extracts_duration_from_probe_payload() -> None:
    """The runner reuses ``summary['duration_seconds']`` on the
    passthrough path so we don't shell ffprobe twice. Confirm the
    helper extracts ``format.duration`` correctly: floors to 1s
    (sub-second clips can't slot a 0s rotation entry), returns
    None when missing or unparseable."""
    payload: dict[str, Any] = {
        'format': {
            'format_name': 'mov,mp4,m4a,3gp,3g2,mj2',
            'duration': '12.7',
        },
        'streams': [{'codec_type': 'video', 'codec_name': 'h264'}],
    }
    with mock.patch.object(
        processing, '_ffprobe_streams', return_value=payload
    ):
        summary = processing._ffprobe_summary('clip.mp4')
    assert summary['duration_seconds'] == 12

    # Sub-second clip floors to 1 (matches the YouTube-task rule).
    payload['format']['duration'] = '0.4'
    with mock.patch.object(
        processing, '_ffprobe_streams', return_value=payload
    ):
        summary = processing._ffprobe_summary('clip.mp4')
    assert summary['duration_seconds'] == 1

    # Missing duration → None.
    del payload['format']['duration']
    with mock.patch.object(
        processing, '_ffprobe_streams', return_value=payload
    ):
        summary = processing._ffprobe_summary('clip.mp4')
    assert summary['duration_seconds'] is None

    # Unparseable string → None (rather than crashing the task).
    payload['format']['duration'] = 'N/A'
    with mock.patch.object(
        processing, '_ffprobe_streams', return_value=payload
    ):
        summary = processing._ffprobe_summary('clip.mp4')
    assert summary['duration_seconds'] is None


@pytest.mark.django_db
def test_video_passthrough_uses_summary_duration_no_second_probe(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The passthrough branch must reuse the duration the summary
    already extracted; calling ``get_video_duration`` (which would
    re-shell ffprobe) is a regression. Asserts via mock-not-called."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    src = path.join(asset_dir, 'clip.mp4')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 64)
    asset = _make_processing_asset('vid-no-2nd-probe', src, mimetype='video')

    # HEVC 1080p30 in-envelope on pi4-64 → passthrough branch fires
    # and ``get_video_duration`` must not be re-shelled.
    summary = _envelope_summary('hevc', 1920, 1080, 30)
    summary['duration_seconds'] = 42
    with (
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(processing, 'get_video_duration') as get_dur,
        mock.patch.object(processing, '_notify'),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.duration == 42
    # Crucially: the second ffprobe shell never happened.
    get_dur.assert_not_called()


def test_resolve_duration_seconds_swallows_probe_exceptions() -> None:
    """``get_video_duration`` raises on ffprobe errors. After a
    successful transcode the row is otherwise ready to play; failing
    the entire task because the post-transcode duration probe stumbled
    would lose all the work. Helper must catch and return None so the
    runner just skips the duration update and lets the operator edit
    manually."""
    with mock.patch.object(
        processing,
        'get_video_duration',
        side_effect=Exception('Bad video format'),
    ):
        # Should NOT raise.
        result = processing._resolve_duration_seconds('clip.mp4')
    assert result is None


def test_format_subprocess_stderr_byte_trim_handles_multibyte_utf8() -> None:
    """The trim is documented as a byte limit; multibyte characters
    in the keep window must not push the decoded string over the
    limit. Edge case: trimming mid-multibyte produces a replacement
    character rather than a UnicodeDecodeError."""
    tail = '— ffmpeg final error: invalid bitstream — '
    big = b'x' * 2000 + tail.encode('utf-8')
    exc = sh.ErrorReturnCode_1(
        full_cmd='ffmpeg ...',
        stdout=b'',
        stderr=big,
        truncate=False,
    )
    out = processing._format_subprocess_stderr(exc)
    # The tail end (which has the diagnostic) is preserved.
    assert 'invalid bitstream' in out
    # The decoded string never exceeds the byte budget plus a small
    # margin for the leading ellipsis character.
    assert len(out.encode('utf-8')) <= processing._STDERR_TAIL_BYTES + 4


def test_ffprobe_summary_handles_missing_ffprobe_binary() -> None:
    """A stripped-down container or dev box without ffprobe in PATH
    must not crash the normalisation task. ``sh.CommandNotFound``
    is raised before any subprocess starts; the helper collapses
    it to the same all-'unknown' summary as a probe-runtime error,
    so the caller falls through to the transcode branch (which
    will then itself fail clean if ffmpeg is also missing)."""
    with mock.patch.object(
        processing,
        '_ffprobe_streams',
        side_effect=sh.CommandNotFound('ffprobe not on PATH'),
    ):
        summary = processing._ffprobe_summary('fixture.mp4')
    assert summary == {
        'container': 'unknown',
        'video_codec': 'unknown',
        'video_pixels': None,
        'video_width': None,
        'video_height': None,
        'video_fps': None,
        'audio_codec': 'unknown',
        'duration_seconds': None,
    }


@pytest.mark.parametrize(
    ('summary', 'expected'),
    [
        # Happy path: H.264 + AAC in mp4 at envelope.
        (
            _envelope_summary('h264', 1920, 1080, 30),
            True,
        ),
        # Wrong codec — even at envelope dims/fps, HEVC against an
        # H.264 envelope must transcode (per-codec hwdec routing on
        # Pi requires the on-disk codec to match envelope.codec
        # exactly).
        (
            _envelope_summary('hevc', 1920, 1080, 30),
            False,
        ),
        # Non-mp4 container — must transcode (variant convention is mp4).
        (
            _envelope_summary('h264', 1920, 1080, 30, container='matroska'),
            False,
        ),
        # Wrong codec — must transcode (codec is the only HW path).
        (
            _envelope_summary('hevc', 1920, 1080, 30, codec_override='prores'),
            False,
        ),
        # Width over cap — transcode (per-axis cap, not total pixels).
        (
            _envelope_summary('h264', 5760, 1080, 30),
            False,
        ),
        # Height over cap.
        (
            _envelope_summary('h264', 1080, 1920, 30),
            False,
        ),
        # FPS over cap (envelope is 30 here).
        (
            _envelope_summary('h264', 1920, 1080, 60),
            False,
        ),
        # Unknown audio codec — fail (we'd have to demux it out).
        (
            _envelope_summary('h264', 1920, 1080, 30, audio_codec='truehd'),
            False,
        ),
        # All unknowns (probe failed) — fail safely → transcode.
        (
            {
                'container': 'unknown',
                'video_codec': 'unknown',
                'video_pixels': None,
                'video_width': None,
                'video_height': None,
                'video_fps': None,
                'audio_codec': 'unknown',
            },
            False,
        ),
        # Width / height probe gap (a malformed source) — bail to
        # transcode rather than gamble on an unsized clip.
        (
            {
                'container': 'mp4',
                'video_codec': 'h264',
                'video_pixels': None,
                'video_width': None,
                'video_height': None,
                'video_fps': 30,
                'audio_codec': 'aac',
            },
            False,
        ),
    ],
)
def test_video_can_passthrough_decision_table(
    summary: dict[str, Any], expected: bool
) -> None:
    """Exhaustive truth table for the envelope-driven passthrough
    check. The envelope used here is the conservative
    H.264 1920×1080 30 fps default (matches an unset ``DEVICE_TYPE``)
    so the dimensions / fps cells exercise the gate directly. Per-
    board behaviour (Pi 4 / Pi 5 / x86 land on the larger HEVC
    envelope) is covered by
    ``test_video_can_passthrough_respects_envelope`` below."""
    envelope = playback_envelope.PlaybackEnvelope('h264', 1920, 1080, 30)
    assert processing._video_can_passthrough(summary, envelope) is expected


@pytest.mark.parametrize(
    (
        'device_type',
        'codec',
        'width',
        'height',
        'fps',
        'expected_passthrough',
    ),
    [
        # pi2 / pi3: H.264 1920×1080 30. Anything off-codec, over
        # axis, or over fps must transcode.
        ('pi2', 'h264', 1920, 1080, 30, True),
        ('pi2', 'h264', 1920, 1080, 60, False),  # over fps
        ('pi2', 'hevc', 1920, 1080, 30, False),  # wrong codec
        ('pi3', 'h264', 1920, 1080, 30, True),
        ('pi3', 'h264', 3840, 2160, 30, False),  # over dims
        # pi4-64 / pi5 / x86: HEVC 3840×2160 60. H.264 always
        # transcodes (envelope is HEVC), even at 1080p.
        ('pi4-64', 'hevc', 3840, 2160, 60, True),
        ('pi4-64', 'hevc', 1920, 1080, 30, True),  # sub-cap OK
        ('pi4-64', 'h264', 1920, 1080, 30, False),  # wrong codec
        ('pi5', 'hevc', 3840, 2160, 60, True),
        ('pi5', 'h264', 1920, 1080, 30, False),  # wrong codec
        ('x86', 'hevc', 3840, 2160, 60, True),
        ('x86', 'h264', 1920, 1080, 30, False),  # wrong codec
        # Default profile is H.264 1920×1080 30.
        ('', 'h264', 1920, 1080, 30, True),
        ('', 'hevc', 1920, 1080, 30, False),
    ],
)
def test_video_can_passthrough_respects_envelope(
    device_type: str,
    codec: str,
    width: int,
    height: int,
    fps: int,
    expected_passthrough: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end envelope matrix: pin ``DEVICE_TYPE``, build a
    summary at the given (codec, width, height, fps), assert the
    passthrough verdict. Mirrors the production code path: the
    celery worker resolves the envelope from env then calls
    ``_video_can_passthrough(summary)`` without threading a profile."""
    monkeypatch.setenv('DEVICE_TYPE', device_type)
    summary = _envelope_summary(codec, width, height, fps)
    assert processing._video_can_passthrough(summary) is expected_passthrough


@pytest.mark.parametrize(
    ('device_type', 'expected_codec', 'expected_extra'),
    [
        ('pi2', 'libx264', None),
        ('pi3', 'libx264', None),
        ('pi4-64', 'libx265', 'hvc1'),
        ('pi5', 'libx265', 'hvc1'),
        ('x86', 'libx265', 'hvc1'),
        ('', 'libx264', None),
    ],
)
def test_transcode_to_target_uses_board_specific_encoder(
    device_type: str,
    expected_codec: str,
    expected_extra: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture the ffmpeg argv ``_transcode_to_target`` invokes and
    assert the encoder + ``-tag:v hvc1`` (HEVC only) match the
    envelope's expected output. Mocks ``sh.ffmpeg`` so no actual
    encode runs — we only care about the argv shape here."""
    monkeypatch.setenv('DEVICE_TYPE', device_type)

    captured: dict[str, Any] = {}

    def fake_ffmpeg(*args: Any, **kwargs: Any) -> None:
        captured['args'] = list(args)
        captured['kwargs'] = kwargs

    with mock.patch.object(sh, 'ffmpeg', side_effect=fake_ffmpeg):
        # No source_summary passed: the function resolves the
        # envelope from DEVICE_TYPE but emits no -vf / -r flags
        # (those require the source to be known over-envelope).
        processing._transcode_to_target('in.mov', 'out.mp4')

    args = captured['args']
    # ``-c:v <encoder>`` lands somewhere in the middle of the argv.
    assert '-c:v' in args
    codec_index = args.index('-c:v')
    assert args[codec_index + 1] == expected_codec
    # AAC audio + faststart are invariants across boards.
    assert '-c:a' in args and 'aac' in args
    assert '-movflags' in args and '+faststart' in args
    assert '-threads' in args and '2' in args
    if expected_extra == 'hvc1':
        # HEVC output gets the iOS-friendly hvc1 codec tag.
        assert '-tag:v' in args
        tag_index = args.index('-tag:v')
        assert args[tag_index + 1] == 'hvc1'
    else:
        assert '-tag:v' not in args
    # Without a source_summary, no scale / fps clamp emitted.
    assert '-vf' not in args
    assert '-r' not in args


def test_transcode_to_target_emits_scale_when_source_oversize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 4K source under a 1080p envelope must get ``-vf scale=...``
    so the rendered variant fits the envelope. The scale expression
    caps the binding axis and lets ffmpeg pick the other side from
    the source aspect (``-2`` = even-aligned, libx264/libx265 both
    reject odd dims)."""
    monkeypatch.delenv('DEVICE_TYPE', raising=False)  # default H.264 1080p30
    captured: dict[str, Any] = {}

    def fake_ffmpeg(*args: Any, **kwargs: Any) -> None:
        captured['args'] = list(args)

    summary = _envelope_summary('h264', 3840, 2160, 30)  # 4K, in-fps cap
    with mock.patch.object(sh, 'ffmpeg', side_effect=fake_ffmpeg):
        processing._transcode_to_target(
            'in.mp4', 'out.mp4', source_summary=summary
        )

    args = captured['args']
    assert '-vf' in args, args
    vf_index = args.index('-vf')
    vf_expr = args[vf_index + 1]
    # Envelope is 1920×1080; the scale expression must reference
    # both axes so it works for landscape AND portrait sources.
    assert '1920' in vf_expr
    assert '1080' in vf_expr
    # No fps clamp — source is 30 fps, envelope is 30 fps.
    assert '-r' not in args


def test_transcode_to_target_emits_fps_clamp_when_source_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 60 fps source under a 30 fps envelope must get
    ``-r 30`` so the variant doesn't carry frames the display
    can't refresh fast enough to show."""
    monkeypatch.delenv('DEVICE_TYPE', raising=False)  # default H.264 1080p30
    captured: dict[str, Any] = {}

    def fake_ffmpeg(*args: Any, **kwargs: Any) -> None:
        captured['args'] = list(args)

    summary = _envelope_summary('h264', 1920, 1080, 60)  # at dims, over fps
    with mock.patch.object(sh, 'ffmpeg', side_effect=fake_ffmpeg):
        processing._transcode_to_target(
            'in.mp4', 'out.mp4', source_summary=summary
        )

    args = captured['args']
    assert '-r' in args, args
    r_index = args.index('-r')
    assert args[r_index + 1] == '30'
    # No scale — dims already inside envelope.
    assert '-vf' not in args


def test_transcode_to_target_omits_clamps_when_source_at_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An at-envelope source (codec aside) doesn't trigger either
    flag. The one-way cap means sub-cap source rates stay native;
    sub-cap dimensions stay untouched."""
    monkeypatch.delenv('DEVICE_TYPE', raising=False)
    captured: dict[str, Any] = {}

    def fake_ffmpeg(*args: Any, **kwargs: Any) -> None:
        captured['args'] = list(args)

    summary = _envelope_summary('h264', 1280, 720, 24)
    with mock.patch.object(sh, 'ffmpeg', side_effect=fake_ffmpeg):
        processing._transcode_to_target(
            'in.mp4', 'out.mp4', source_summary=summary
        )

    args = captured['args']
    assert '-vf' not in args
    assert '-r' not in args


@pytest.mark.parametrize(
    ('device_type', 'source_codec', 'subtype', 'expected_hwaccel'),
    [
        # Pi 4: both codecs go through v4l2_request via the +rpt1
        # ffmpeg's drm hwaccel.
        ('pi4-64', 'h264', None, ['-hwaccel', 'drm']),
        ('pi4-64', 'hevc', None, ['-hwaccel', 'drm']),
        # Pi 5: only HEVC has an upstream HW path; H.264 stays SW.
        ('pi5', 'hevc', None, ['-hwaccel', 'drm']),
        ('pi5', 'h264', None, []),
        # Rock Pi 4: both codecs reachable via v4l2_request when the
        # host_agent has published the subtype.
        ('arm64', 'h264', 'rockpi4', ['-hwaccel', 'drm']),
        ('arm64', 'hevc', 'rockpi4', ['-hwaccel', 'drm']),
        # Generic arm64 without a known subtype stays on SW decode —
        # we have no way to know what HW path the SBC exposes.
        ('arm64', 'h264', None, []),
        ('arm64', 'hevc', None, []),
        # x86: VAAPI for both codecs.
        (
            'x86',
            'h264',
            None,
            ['-hwaccel', 'vaapi', '-hwaccel_device', '/dev/dri/renderD128'],
        ),
        (
            'x86',
            'hevc',
            None,
            ['-hwaccel', 'vaapi', '-hwaccel_device', '/dev/dri/renderD128'],
        ),
        # Pi 2 / Pi 3: no HW decode path mpv can address.
        ('pi2', 'h264', None, []),
        ('pi3', 'h264', None, []),
        # Empty source codec (probe failure) collapses to SW. We
        # don't gamble on the wrong decoder.
        ('pi4-64', '', None, []),
        ('pi4-64', None, None, []),
    ],
)
def test_decode_hwaccel_args_per_board(
    monkeypatch: pytest.MonkeyPatch,
    device_type: str,
    source_codec: str | None,
    subtype: str | None,
    expected_hwaccel: list[str],
) -> None:
    """The walker's HW-decode dispatch matrix is the source of truth
    for which boards offload the decode half of the transcode
    pipeline. Drift between this table and what the +rpt1 ffmpeg
    can actually reach would silently route encodes through SW even
    on boards that could be using hardware — pin every cell."""
    monkeypatch.setenv('DEVICE_TYPE', device_type)
    # Patch the Redis subtype probe to return the test's chosen
    # subtype value. ``None`` exercises the "no subtype published"
    # path; a bytes value exercises the "host_agent has run"
    # promotion.
    fake = mock.MagicMock()
    fake.get.return_value = subtype.encode() if subtype else None
    with mock.patch(
        'anthias_common.utils.connect_to_redis', return_value=fake
    ):
        result = processing._decode_hwaccel_args(source_codec)
    assert result == expected_hwaccel


def test_transcode_to_target_inserts_hwaccel_before_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the dispatch matrix says a HW path is available, ffmpeg
    sees the ``-hwaccel`` flags *before* ``-i`` (where ffmpeg looks
    for input-related options). Placing them after ``-i`` would
    silently no-op — ffmpeg treats them as output-side then."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    captured: dict[str, Any] = {}

    def fake_ffmpeg(*args: Any, **kwargs: Any) -> None:
        captured['args'] = list(args)

    summary = _envelope_summary('h264', 1920, 1080, 30)
    with mock.patch.object(sh, 'ffmpeg', side_effect=fake_ffmpeg):
        processing._transcode_to_target(
            'in.mp4', 'out.mp4', source_summary=summary
        )

    args = captured['args']
    assert '-hwaccel' in args
    hwaccel_idx = args.index('-hwaccel')
    input_idx = args.index('-i')
    assert hwaccel_idx < input_idx, (
        'ffmpeg -hwaccel must precede -i to apply to the input '
        f'(got hwaccel at {hwaccel_idx}, -i at {input_idx})'
    )


def test_transcode_to_target_falls_back_to_sw_on_hwaccel_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime HW decode init can fail even on a board that
    nominally supports it (device busy, kernel driver mismatch,
    bitstream the v4l2_request decoder rejects). The walker must
    not error the asset in that case — it should retry once with
    the hwaccel flags stripped, so the operator gets a slow SW
    pass instead of a permanent processing-failed flag.
    """
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    call_args: list[list[Any]] = []

    def fake_ffmpeg(*args: Any, **kwargs: Any) -> None:
        call_args.append(list(args))
        # First call (with -hwaccel) fails; second (SW) succeeds.
        if '-hwaccel' in args:
            raise sh.ErrorReturnCode(
                full_cmd='ffmpeg ...',
                stdout=b'',
                stderr=b'Hwaccel device init failed',
                truncate=False,
            )

    summary = _envelope_summary('h264', 1920, 1080, 30)
    with mock.patch.object(sh, 'ffmpeg', side_effect=fake_ffmpeg):
        processing._transcode_to_target(
            'in.mp4', 'out.mp4', source_summary=summary
        )

    assert len(call_args) == 2, (
        f'expected exactly 2 ffmpeg calls (HW then SW); got {len(call_args)}'
    )
    assert '-hwaccel' in call_args[0], 'first attempt should use HW decode'
    assert '-hwaccel' not in call_args[1], (
        'second attempt should be plain SW (no hwaccel flags)'
    )


@pytest_ffmpeg
@pytest.mark.django_db
def test_video_passthrough_records_target_codec(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passthrough rows still get ``transcode_target`` written so the
    operator can see at a glance which envelope.codec the variant
    was rendered against. Use the pi4-64 envelope (HEVC) and a
    libx265 source to hit the passthrough path."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    src = path.join(asset_dir, 'sample.mp4')
    _make_video(src, codec='libx265', container='mp4', audio='aac')
    asset = _make_processing_asset('vid-pass-pi4', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.metadata['transcoded'] is False
    assert asset.metadata['transcode_target'] == 'hevc'


@pytest.mark.django_db
def test_video_pi3_transcodes_hevc_to_h264(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pi3 device receiving an HEVC upload must transcode to H.264
    even though the source is in an accepted container — pi3's VLC +
    mmal-vc4 path can't decode HEVC. Mocks the actual ffmpeg run so
    the test doesn't depend on libx265 being available; asserts on
    the captured argv to lock in the codec choice."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi3')
    src = path.join(asset_dir, 'fixture.mkv')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 64)
    asset = _make_processing_asset('vid-pi3-hevc', src, mimetype='video')

    summary = {
        'container': 'mkv',
        'video_codec': 'hevc',
        'audio_codec': 'aac',
    }
    captured: dict[str, Any] = {}

    def fake_transcode(_in: str, staging: str, **kwargs: Any) -> None:
        # Capture the profile that was selected and produce a stub
        # output so the runner can finalise the row.
        captured['profile'] = kwargs.get('envelope')
        with open(staging, 'wb') as fh:
            fh.write(b'\x00\x00\x00\x18ftypmp42')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=fake_transcode
        ),
        mock.patch.object(
            processing, '_resolve_duration_seconds', return_value=10
        ),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.metadata['transcoded'] is True
    assert asset.metadata['transcode_target'] == 'h264'
    # The runner threaded the resolved profile to the transcode
    # helper rather than letting it re-resolve from env (which would
    # also be correct, but threading is the cheaper invariant).
    assert captured['profile'].codec == 'h264'


@pytest.mark.django_db
def test_video_pi5_transcodes_h264_to_hevc(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pi 5 has no mpv H.264 HW path (Hantro G1 is invisible to
    upstream mpv), so any H.264 upload would silently SW-fall-back
    on-device. The asset processor catches it at upload time and
    re-encodes to HEVC, which IS HW-decodable on Pi 5 (Hantro G2 via
    v4l2_request_hevc / drm-copy)."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'fixture.mp4')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 64)
    asset = _make_processing_asset('vid-pi5-h264', src, mimetype='video')

    summary = {
        'container': 'mp4',
        'video_codec': 'h264',
        'video_pixels': 1920 * 1080,
        'audio_codec': 'aac',
    }
    captured: dict[str, Any] = {}

    def fake_transcode(_in: str, staging: str, **kwargs: Any) -> None:
        captured['profile'] = kwargs.get('envelope')
        with open(staging, 'wb') as fh:
            fh.write(b'\x00\x00\x00\x18ftypmp42')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=fake_transcode
        ),
        mock.patch.object(
            processing, '_resolve_duration_seconds', return_value=10
        ),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.metadata['transcoded'] is True
    assert asset.metadata['transcode_target'] == 'hevc'
    assert captured['profile'].codec == 'hevc'


@pytest.mark.django_db
def test_video_pi4_64_transcodes_4k_h264_to_hevc(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pi 4's V3D V4L2 M2M H.264 decoder is rated for ~1080p60.
    A 4K H.264 upload would clear the codec gate but fall to
    software at playback (V3D can't service the pixel throughput).
    The pixel cap in the pi4-64 profile forces a re-encode to HEVC
    — HEVC HW on Pi 4 handles 4Kp60 without breaking a sweat."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    src = path.join(asset_dir, 'fixture.mp4')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 64)
    asset = _make_processing_asset('vid-pi4-4k-h264', src, mimetype='video')

    summary = {
        'container': 'mp4',
        'video_codec': 'h264',
        'video_pixels': 3840 * 2160,
        'audio_codec': 'aac',
    }
    captured: dict[str, Any] = {}

    def fake_transcode(_in: str, staging: str, **kwargs: Any) -> None:
        captured['profile'] = kwargs.get('envelope')
        with open(staging, 'wb') as fh:
            fh.write(b'\x00\x00\x00\x18ftypmp42')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=fake_transcode
        ),
        mock.patch.object(
            processing, '_resolve_duration_seconds', return_value=10
        ),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.metadata['transcoded'] is True
    assert asset.metadata['transcode_target'] == 'hevc'
    assert captured['profile'].codec == 'hevc'


# ---------------------------------------------------------------------------
# Celery wrapper tests — task-level behaviour (no_op guards, on_failure)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_normalize_image_asset_celery_no_op_when_row_finalized(
    asset_dir: str,
) -> None:
    """The celery task body must short-circuit on a row that's
    already cleared is_processing — duplicate dispatch can't
    re-convert and stomp on operator-edited state."""
    Asset.objects.create(
        asset_id='img-final',
        name='img-final',
        uri=path.join(asset_dir, 'fixture.webp'),
        mimetype='image',
        duration=10,
        is_processing=False,
    )
    from anthias_server.celery_tasks import normalize_image_asset

    with mock.patch.object(processing, '_run_image_normalisation') as run:
        normalize_image_asset('img-final')
    run.assert_not_called()


@pytest.mark.django_db
def test_normalize_video_asset_celery_no_op_when_row_finalized(
    asset_dir: str,
) -> None:
    Asset.objects.create(
        asset_id='vid-final',
        name='vid-final',
        uri=path.join(asset_dir, 'fixture.mp4'),
        mimetype='video',
        duration=10,
        is_processing=False,
    )
    from anthias_server.celery_tasks import normalize_video_asset

    with mock.patch.object(processing, '_run_video_normalisation') as run:
        normalize_video_asset('vid-final')
    run.assert_not_called()


@pytest.mark.django_db
def test_normalize_image_asset_celery_no_op_when_row_missing() -> None:
    from anthias_server.celery_tasks import normalize_image_asset

    with mock.patch.object(processing, '_run_image_normalisation') as run:
        normalize_image_asset('does-not-exist')
    run.assert_not_called()


def test_normalize_tasks_exclude_permanent_oserrors_from_autoretry() -> None:
    """Both normalisation tasks have ``autoretry_for=(OSError,)`` so a
    transient disk hiccup is retried automatically. Several OSError
    subclasses are permanent and must be excluded so they land on
    on_failure immediately:

      * ``FileNotFoundError`` — source file disappeared between row
        creation and pickup.
      * ``UnidentifiedImageError`` (image task only) — Pillow refused
        to decode the file. Inherits from OSError, so without the
        explicit exclusion the autoretry filter would sweep it up.

    Confirms the exclusions are in effect at celery-config time so a
    future change to the decorators can't silently regress the
    immediate-fail contract.
    """
    from anthias_server.celery_tasks import (
        normalize_image_asset,
        normalize_video_asset,
    )

    for task in (normalize_image_asset, normalize_video_asset):
        # ``autoretry_for`` and ``dont_autoretry_for`` are read off
        # the celery Task instance via the per-task options dict that
        # ``add_autoretry_behaviour`` populates at registration. They
        # are not declared as class attributes on the Task type, so
        # mypy needs a getattr to see them; the ``celery-types``
        # stubs we use don't model these dynamic options.
        autoretry_for = tuple(getattr(task, 'autoretry_for', ()))
        dont_autoretry_for = tuple(getattr(task, 'dont_autoretry_for', ()))
        assert OSError in autoretry_for, (
            f'{task.name} expected autoretry_for=(OSError,)'
        )
        assert FileNotFoundError in dont_autoretry_for, (
            f'{task.name} expected dont_autoretry_for to include '
            f'FileNotFoundError so missing-source raises immediately'
        )

    # Image task additionally excludes UnidentifiedImageError (Pillow
    # only — video has no Pillow path).
    assert UnidentifiedImageError in tuple(
        getattr(normalize_image_asset, 'dont_autoretry_for', ())
    ), (
        'normalize_image_asset expected dont_autoretry_for to include '
        'UnidentifiedImageError so corrupt-image raises immediately '
        '(it inherits from OSError)'
    )


@pytest.mark.django_db
def test_normalize_on_failure_writes_error_metadata(
    asset_dir: str,
) -> None:
    """The custom Task.on_failure path must persist the error message
    and clear is_processing — operator must never see a row stuck
    forever in 'Processing' after a crash."""
    asset = _make_processing_asset(
        'img-onfail',
        path.join(asset_dir, 'fixture.tiff'),
        mimetype='image',
        metadata={'original_ext': '.tiff'},
    )
    task = processing._NormalizeAssetTask()

    # Args[0] is the asset_id: matches the celery task signature.
    with mock.patch.object(processing, '_notify') as notify:
        task.on_failure(
            UnidentifiedImageError('cannot decode'),
            'task-id',
            (asset.asset_id,),
            {},
            None,
        )

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert (
        'cannot decode' in asset.metadata['error_message']
        and 'UnidentifiedImageError' in asset.metadata['error_message']
    )
    # Earlier metadata keys are preserved.
    assert asset.metadata['original_ext'] == '.tiff'
    notify.assert_called_once_with(asset.asset_id)


@pytest.mark.django_db
def test_normalize_on_failure_no_args_is_safe() -> None:
    """on_failure called with empty args (e.g. a queueing crash
    before the task body ran) must not raise."""
    task = processing._NormalizeAssetTask()
    # Should not raise.
    task.on_failure(RuntimeError('boom'), 'task-id', (), {}, None)


# ---------------------------------------------------------------------------
# Helper / dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('filename', 'expected'),
    [
        # Every entry in NORMALIZE_IMAGE_EXTS routes through; case
        # variants exercise the case-insensitive lstrip-and-lower
        # path in ``_ext``.
        ('foo.heic', True),
        ('FOO.HEIC', True),
        ('foo.heif', True),
        ('foo.tif', True),
        ('foo.tiff', True),
        ('foo.bmp', True),
        ('foo.BMP', True),
        ('foo.ico', True),
        ('foo.tga', True),
        ('foo.jp2', True),
        ('foo.j2k', True),
        ('foo.jpx', True),
        ('foo.jpc', True),
        ('foo.jpf', True),
        ('foo.avif', True),
        # Already-friendly formats stay untouched — no Celery hop.
        ('foo.jpg', False),
        ('foo.jpeg', False),
        ('foo.png', False),
        ('foo.webp', False),
        ('foo.gif', False),
        ('foo.svg', False),
        # No extension and unknown extensions also fall through to
        # the no-op branch.
        ('foo', False),
        ('foo.psd', False),
    ],
)
def test_needs_image_normalisation(filename: str, expected: bool) -> None:
    assert processing.needs_image_normalisation(filename) is expected


def test_dispatch_normalize_image_invokes_celery_task() -> None:
    # ``stamp_processing_start`` writes ``metadata.processing_started_at``
    # for the reconciler — irrelevant to this delay-was-called check, so
    # mocked out to keep the test off the DB.
    with (
        mock.patch(
            'anthias_server.celery_tasks.normalize_image_asset.delay'
        ) as delay,
        mock.patch('anthias_server.processing.stamp_processing_start'),
    ):
        processing.dispatch_normalize_image('asset-1')
    delay.assert_called_once_with('asset-1')


def test_dispatch_normalize_video_invokes_celery_task() -> None:
    with (
        mock.patch(
            'anthias_server.celery_tasks.normalize_video_asset.delay'
        ) as delay,
        mock.patch('anthias_server.processing.stamp_processing_start'),
    ):
        processing.dispatch_normalize_video('asset-2')
    delay.assert_called_once_with('asset-2')


@pytest.mark.django_db
def test_dispatch_normalize_video_stamps_processing_started_at() -> None:
    """``dispatch_normalize_video`` writes
    ``metadata.processing_started_at`` so the periodic reconciler can
    age the row. Regression guard for GH #2870's second fix line —
    without the stamp, a stuck row has no signal the reconciler can
    use to decide whether enough time has elapsed to re-dispatch."""
    Asset.objects.create(
        asset_id='stamped-vid',
        name='Test',
        uri='/data/anthias_assets/stamped-vid.mp4',
        mimetype='video',
        duration=10,
        is_enabled=True,
        is_processing=True,
        metadata={'foo': 'bar'},
    )
    with mock.patch('anthias_server.celery_tasks.normalize_video_asset.delay'):
        processing.dispatch_normalize_video('stamped-vid')
    a = Asset.objects.get(asset_id='stamped-vid')
    assert 'processing_started_at' in a.metadata
    # Existing metadata keys preserved.
    assert a.metadata['foo'] == 'bar'
    # The stamp is a valid ISO-8601 string.
    from datetime import datetime

    datetime.fromisoformat(a.metadata['processing_started_at'])


class _FakeSerializer:
    """Stand-in for the four API serializer classes — the dispatch
    helper only reads ``_pending_normalize``, so a minimal duck type
    is enough to test the branch logic without spinning up DRF."""

    def __init__(self, pending: str | None) -> None:
        self._pending_normalize = pending


def test_dispatch_pending_normalize_routes_video() -> None:
    with (
        mock.patch('anthias_server.processing.dispatch_normalize_video') as v,
        mock.patch('anthias_server.processing.dispatch_normalize_image') as i,
    ):
        processing.dispatch_pending_normalize(
            _FakeSerializer('video'), 'asset-vid'
        )
    v.assert_called_once_with('asset-vid')
    i.assert_not_called()


def test_dispatch_pending_normalize_routes_image() -> None:
    with (
        mock.patch('anthias_server.processing.dispatch_normalize_video') as v,
        mock.patch('anthias_server.processing.dispatch_normalize_image') as i,
    ):
        processing.dispatch_pending_normalize(
            _FakeSerializer('image'), 'asset-img'
        )
    i.assert_called_once_with('asset-img')
    v.assert_not_called()


def test_dispatch_pending_normalize_noop_when_unset() -> None:
    """A serializer that didn't flag normalisation (most uploads:
    JPEG/PNG/H.264) leaves the helper as a no-op — no spurious
    Celery dispatch."""
    with (
        mock.patch('anthias_server.processing.dispatch_normalize_video') as v,
        mock.patch('anthias_server.processing.dispatch_normalize_image') as i,
    ):
        processing.dispatch_pending_normalize(
            _FakeSerializer(None), 'asset-plain'
        )
    v.assert_not_called()
    i.assert_not_called()


def test_dispatch_pending_normalize_handles_missing_attribute() -> None:
    """A serializer class that doesn't carry ``_pending_normalize``
    at all must not crash — defends against a future serializer
    that doesn't route through the shared mixin."""

    class _Bare:
        pass

    with (
        mock.patch('anthias_server.processing.dispatch_normalize_video') as v,
        mock.patch('anthias_server.processing.dispatch_normalize_image') as i,
    ):
        processing.dispatch_pending_normalize(_Bare(), 'asset-bare')
    v.assert_not_called()
    i.assert_not_called()


# ---------------------------------------------------------------------------
# notify() — smoke-test the publish/notify wiring without spinning up
# a real Channels stack.
# ---------------------------------------------------------------------------


def test_notify_swallows_publish_errors() -> None:
    """Redis flake during the viewer reload publish must not block
    the browser-side notify (or vice-versa). Both are best-effort."""
    fake_redis = mock.MagicMock()
    fake_redis.publish.side_effect = RuntimeError('redis flake')
    with (
        mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=fake_redis
        ),
        mock.patch(
            'anthias_server.app.consumers.notify_asset_update'
        ) as notify,
    ):
        processing._notify('asset-1')
    notify.assert_called_once_with('asset-1')


def test_notify_swallows_notify_errors() -> None:
    fake_redis = mock.MagicMock()
    with (
        mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=fake_redis
        ),
        mock.patch(
            'anthias_server.app.consumers.notify_asset_update',
            side_effect=RuntimeError('channels flake'),
        ),
    ):
        # Should not raise.
        processing._notify('asset-1')
    # The viewer-reload publish ran first and succeeded; the
    # subsequent notify failure was caught.
    fake_redis.publish.assert_called_once()


def test_notify_browser_only_skips_viewer_reload() -> None:
    """Intermediate hops that leave is_processing=True (e.g. the
    YouTube task before chaining into normalize_video) call
    ``_notify(..., reload_viewer=False)`` so the on-device viewer
    doesn't reload its playlist for a row that's still mid-flight.
    The browser-side update still fires so the dashboard picks up
    the new title/duration immediately."""
    fake_redis = mock.MagicMock()
    with (
        mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=fake_redis
        ),
        mock.patch(
            'anthias_server.app.consumers.notify_asset_update'
        ) as browser_notify,
    ):
        processing._notify('asset-1', reload_viewer=False)
    # Viewer publish never happened.
    fake_redis.publish.assert_not_called()
    # Browser nudge still went out.
    browser_notify.assert_called_once_with('asset-1')


# ---------------------------------------------------------------------------
# JSON probe payload — ffprobe output parsing
# ---------------------------------------------------------------------------


def test_ffprobe_streams_parses_json() -> None:
    payload = json.dumps(
        {'streams': [{'codec_type': 'video', 'codec_name': 'h264'}]}
    )

    class _FakeBuf:
        def __init__(self, body: str) -> None:
            self._body = body

        def __str__(self) -> str:
            return self._body

    with mock.patch.object(
        sh, 'ffprobe', create=True, return_value=_FakeBuf(payload)
    ):
        result = processing._ffprobe_streams('fixture.mp4')
    assert result['streams'][0]['codec_name'] == 'h264'


@pytest.mark.django_db
def test_video_passthrough_skips_duration_when_probe_unavailable(
    asset_dir: str,
) -> None:
    """If ffprobe is unavailable (host without ffmpeg apt package),
    the passthrough branch still flips is_processing — the row
    stays at its placeholder duration so the operator can edit it
    manually rather than being stuck."""
    src = path.join(asset_dir, 'fixture.mp4')
    with open(src, 'wb') as fh:
        # Just enough so isfile() passes; the probe is mocked anyway.
        fh.write(b'\x00' * 64)
    asset = _make_processing_asset('vid-noprobe', src, mimetype='video')

    # Synthesised summary must carry the full passthrough-decision
    # shape (codec / dims / fps) so ``_video_can_passthrough`` takes
    # the happy path; otherwise the test would fall through to a
    # real ffmpeg invocation on the 64-byte stub file and fail with
    # "Invalid data found when processing input". The ``ffprobe
    # unavailable`` scenario is that the summary lacks
    # ``duration_seconds`` (which is what the early-return path in
    # ``_ffprobe_summary`` produces); the *passthrough decision*
    # itself was already made elsewhere on a normally-probed file.
    summary = _envelope_summary('h264', 1920, 1080, 30)
    summary['duration_seconds'] = None
    with (
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=summary
        ),
        mock.patch.object(
            processing, '_resolve_duration_seconds', return_value=None
        ),
        mock.patch.object(processing, '_notify'),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.is_processing is False
    # Duration left at the placeholder — never overwritten with None.
    assert asset.duration == 0
    assert asset.metadata['transcoded'] is False


# ---------------------------------------------------------------------------
# Static webp fixture for upload-path tests
# ---------------------------------------------------------------------------


def _stage_temp_upload(asset_dir: str, content: bytes) -> str:
    """Mirror ``FileAssetViewMixin.post`` byte-for-byte: write
    ``<assetdir>/<uuid>.tmp`` and return the path. Used by the
    upload-path wiring tests to drive ``prepare_asset`` without a
    real HTTP roundtrip."""
    import uuid as _uuid

    p = path.join(asset_dir, f'{_uuid.uuid4().hex}.tmp')
    with open(p, 'wb') as fh:
        fh.write(content)
    return p


def _serialised_image_bytes() -> bytes:
    """Return a raw-PNG byte buffer — compatible with the URL
    reachability probe (which only checks for a local file's
    existence on schemeless paths)."""
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), (10, 20, 30)).save(buf, 'PNG')
    return buf.getvalue()


@pytest.mark.django_db
def test_prepare_asset_routes_heic_through_image_pipeline(
    asset_dir: str,
) -> None:
    """End-to-end-ish: simulate a HEIC upload and verify
    prepare_asset stamps is_processing=True and stashes the pending
    flag the view dispatches on."""
    from datetime import datetime, timezone as _tz

    from anthias_server.api.serializers.v2 import CreateAssetSerializerV2

    upload_path = _stage_temp_upload(asset_dir, _serialised_image_bytes())

    serializer = CreateAssetSerializerV2(
        data={
            'name': 'pic',
            'uri': upload_path,
            'ext': '.heic',
            'mimetype': 'image',
            'duration': 10,
            'start_date': datetime(2026, 1, 1, tzinfo=_tz.utc),
            'end_date': datetime(2030, 1, 1, tzinfo=_tz.utc),
            'is_enabled': False,
        },
        unique_name=False,
    )
    with (
        # We don't need an actual reachability probe for this test.
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=False,
        ),
    ):
        assert serializer.is_valid(), serializer.errors

    asset_dict = serializer.validated_data
    assert asset_dict['is_processing'] is True
    assert asset_dict['mimetype'] == 'image'
    assert serializer._pending_normalize == 'image'
    # The renamed file lives at <assetdir>/<asset_id>.heic
    assert asset_dict['uri'].endswith('.heic')


@pytest.mark.django_db
def test_prepare_asset_routes_video_through_video_pipeline(
    asset_dir: str,
) -> None:
    from datetime import datetime, timezone as _tz

    from anthias_server.api.serializers.v2 import CreateAssetSerializerV2

    upload_path = _stage_temp_upload(asset_dir, b'fake mp4 bytes ' * 16)

    serializer = CreateAssetSerializerV2(
        data={
            'name': 'clip',
            'uri': upload_path,
            'ext': '.mp4',
            'mimetype': 'video',
            'duration': 0,
            'start_date': datetime(2026, 1, 1, tzinfo=_tz.utc),
            'end_date': datetime(2030, 1, 1, tzinfo=_tz.utc),
            'is_enabled': False,
        },
        unique_name=False,
    )
    with (
        mock.patch(
            'anthias_server.api.serializers.mixins.url_fails',
            return_value=False,
        ),
        mock.patch(
            'anthias_server.api.serializers.mixins.get_video_duration',
            return_value=None,
        ),
    ):
        assert serializer.is_valid(), serializer.errors

    asset_dict = serializer.validated_data
    assert asset_dict['is_processing'] is True
    assert serializer._pending_normalize == 'video'
    # Duration left at 0 — task fills it in on completion.
    assert asset_dict['duration'] == 0


@pytest.mark.django_db
def test_prepare_asset_skips_pipeline_for_remote_url(
    asset_dir: str,
) -> None:
    """A webpage / RTSP / HTTP video URL must not get flagged for
    normalisation — only locally-uploaded files do."""
    from datetime import datetime, timezone as _tz

    from anthias_server.api.serializers.v2 import CreateAssetSerializerV2

    serializer = CreateAssetSerializerV2(
        data={
            'name': 'web',
            'uri': 'https://example.com/page',
            'mimetype': 'webpage',
            'duration': 30,
            'start_date': datetime(2026, 1, 1, tzinfo=_tz.utc),
            'end_date': datetime(2030, 1, 1, tzinfo=_tz.utc),
            'is_enabled': False,
        },
        unique_name=False,
    )
    with mock.patch(
        'anthias_server.api.serializers.mixins.url_fails',
        return_value=False,
    ):
        assert serializer.is_valid(), serializer.errors

    assert serializer._pending_normalize is None
    assert serializer.validated_data.get('is_processing') in (False, 0, None)


@pytest.mark.django_db
def test_prepare_asset_skips_pipeline_for_jpeg_upload(
    asset_dir: str,
) -> None:
    """Common JPEG / PNG / WebP uploads land ready-to-play — never
    enqueue a normalisation task for them."""
    from datetime import datetime, timezone as _tz

    from anthias_server.api.serializers.v2 import CreateAssetSerializerV2

    upload = _stage_temp_upload(asset_dir, _serialised_image_bytes())
    serializer = CreateAssetSerializerV2(
        data={
            'name': 'photo',
            'uri': upload,
            'ext': '.jpg',
            'mimetype': 'image',
            'duration': 10,
            'start_date': datetime(2026, 1, 1, tzinfo=_tz.utc),
            'end_date': datetime(2030, 1, 1, tzinfo=_tz.utc),
            'is_enabled': False,
        },
        unique_name=False,
    )
    with mock.patch(
        'anthias_server.api.serializers.mixins.url_fails',
        return_value=False,
    ):
        assert serializer.is_valid(), serializer.errors
    assert serializer._pending_normalize is None


# ---------------------------------------------------------------------------
# Sibling-original storage tests
#
# The contract under test: every video asset on disk after
# ``_run_video_normalisation`` consists of two files —
#
#   <id>.<envelope.container_ext>   ← the playback variant
#   <id>.original.<src_ext>         ← the source bytes, never modified
#
# plus two metadata keys (``original_uri`` and ``envelope``). The
# walker depends on this contract for envelope-change re-renders, so
# the tests below pin first-upload, re-render, passthrough, and the
# legacy migration paths.
# ---------------------------------------------------------------------------


@pytest_ffmpeg
@pytest.mark.django_db
def test_sibling_original_created_on_first_run(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh upload: source at ``<id>.mov`` becomes
    ``<id>.original.mov`` next to the rendered ``<id>.mp4`` variant.

    ``monkeypatch`` pins ``DEVICE_TYPE=pi5`` so the source (libx264)
    triggers a real transcode to HEVC — the *upload extension* (.mov)
    survives into the sibling regardless of the variant container.
    """
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'vid.mov')
    _make_video(src, codec='libx264', container='mov', audio='aac')
    asset = _make_processing_asset('vid', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    variant = path.join(asset_dir, 'vid.mp4')
    original = path.join(asset_dir, 'vid.original.mov')

    assert asset.uri == variant
    assert path.isfile(variant), 'variant must exist after render'
    assert path.isfile(original), 'sibling-original must exist'
    assert asset.metadata['original_uri'] == original
    assert asset.metadata['envelope'] == {
        'codec': 'hevc',
        'max_width': 3840,
        'max_height': 2160,
        'max_fps': 60,
    }
    assert asset.metadata['transcoded'] is True


@pytest_ffmpeg
@pytest.mark.django_db
def test_original_uri_persisted_before_render_for_crash_safety(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mid-flight failure (disk-full / SIGKILL / ffmpeg crash) must
    not leak the source bytes. The walker commits
    ``metadata['original_uri']`` to the DB right after renaming the
    upload to ``.original.<ext>``, *before* attempting the render,
    so the orphan-sweep in ``cleanup()`` recognises the renamed
    file as claimed. Without this contract a disk-full mid-walker
    silently deletes every renamed source on the next 1h tick --
    live-confirmed on the Pi 4 test bed during the envelope rollout.

    Asserts the DB write happens before ``_transcode_to_target`` by
    raising from the mock and checking the committed row state.
    """
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'crashy.mov')
    _make_video(src, codec='libx264', container='mov', audio='aac')
    asset = _make_processing_asset('crashy', src, mimetype='video')

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError('simulated mid-render crash')

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_transcode_to_target', side_effect=_explode
        ),
    ):
        with pytest.raises(RuntimeError, match='simulated mid-render crash'):
            processing._run_video_normalisation(asset)

    # The .original.<ext> was renamed onto disk AND the DB row
    # carries the pointer -- a subsequent cleanup() pass will see
    # the file as claimed even though the variant slot is empty.
    original = path.join(asset_dir, 'crashy.original.mov')
    assert path.isfile(original), '.original.<ext> must exist on disk'
    asset.refresh_from_db()
    assert asset.metadata.get('original_uri') == original, (
        'metadata.original_uri must be committed before render attempts'
    )


@pytest_ffmpeg
@pytest.mark.django_db
def test_sibling_original_preserved_across_re_render(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-render: ``.original.<ext>`` is the authoritative source. A
    second normalisation pass reads it, re-renders the variant, and
    leaves the original byte-for-byte unchanged. This is the walker's
    happy path on every envelope change."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'reroll.mov')
    _make_video(src, codec='libx264', container='mov', audio='aac')
    asset = _make_processing_asset('reroll', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)
    asset.refresh_from_db()

    original = asset.metadata['original_uri']
    import hashlib

    def _digest(p: str) -> str:
        h = hashlib.sha256()
        with open(p, 'rb') as fh:
            for chunk in iter(lambda: fh.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()

    original_sha = _digest(original)

    # Re-render against the same envelope. The render path branches
    # on ``metadata['original_uri']`` existing on disk.
    asset.is_processing = True
    asset.save()
    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)
    asset.refresh_from_db()

    assert path.isfile(original), 'original deleted across re-render'
    assert _digest(original) == original_sha, (
        '.original.<ext> bytes changed across re-render — the walker '
        'must not rewrite the source'
    )


@pytest_ffmpeg
@pytest.mark.django_db
def test_sibling_original_on_passthrough(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passthrough still produces both files — the variant is a copy
    of the original, not a rename of it. Lets the walker treat every
    video asset uniformly regardless of whether the source matched
    the envelope on first upload."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    src = path.join(asset_dir, 'pass.mp4')
    _make_video(src, codec='libx265', container='mp4', audio='aac')
    asset = _make_processing_asset('pass', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)
    asset.refresh_from_db()

    assert asset.metadata['transcoded'] is False
    original = path.join(asset_dir, 'pass.original.mp4')
    variant = path.join(asset_dir, 'pass.mp4')
    assert path.isfile(original), 'passthrough must still create .original'
    assert path.isfile(variant)
    assert asset.uri == variant
    # Byte-identical copy (passthrough is shutil.copyfile, not a remux).
    assert os.stat(original).st_size == os.stat(variant).st_size


@pytest_ffmpeg
@pytest.mark.django_db
def test_legacy_asset_migrates_on_first_normalisation(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-envelope rollout: ``Asset.uri`` points at the historical
    in-place-transcoded file (no sibling, no envelope key). The
    walker's first pass after upgrade must rename it to
    ``.original.<ext>`` and re-render. We can't recover the
    pre-Anthias upload bytes, but we don't lose more than the next
    envelope-change step would have lost anyway."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'legacy.mp4')
    _make_video(src, codec='libx264', container='mp4', audio='aac')
    # Simulate a legacy row: no ``original_uri`` or ``envelope`` key.
    asset = _make_processing_asset(
        'legacy', src, mimetype='video', metadata={'transcoded': True}
    )

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)
    asset.refresh_from_db()

    original = path.join(asset_dir, 'legacy.original.mp4')
    variant = path.join(asset_dir, 'legacy.mp4')
    assert path.isfile(original), 'legacy asset must be renamed to .original'
    assert path.isfile(variant), 'fresh variant must be rendered'
    assert asset.metadata['original_uri'] == original
    assert asset.metadata['envelope']['codec'] == 'hevc'


@pytest_ffmpeg
@pytest.mark.django_db
def test_re_render_falls_through_when_original_uri_dangling(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator deleted ``.original.<ext>`` to free disk: re-render
    falls back to treating ``Asset.uri`` as the source-to-preserve.
    No silent error, no lost variant — the asset just loses its
    bytes-back path for the next envelope change."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'dangling.mov')
    _make_video(src, codec='libx264', container='mov', audio='aac')
    asset = _make_processing_asset(
        'dangling',
        src,
        mimetype='video',
        metadata={
            'original_uri': path.join(asset_dir, 'does-not-exist.mov'),
        },
    )

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)
    asset.refresh_from_db()

    # We can't assert on a particular original path — the fall-through
    # path renames ``asset.uri`` to ``.original.<ext>``, so the new
    # original lives at ``dangling.original.mov``.
    assert path.isfile(path.join(asset_dir, 'dangling.original.mov'))
    assert path.isfile(path.join(asset_dir, 'dangling.mp4'))
    assert asset.metadata['original_uri'] == path.join(
        asset_dir, 'dangling.original.mov'
    )
