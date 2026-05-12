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
  / Pi 5 / x86. The grid lives in ``processing._BOARD_PROFILES``
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

from anthias_server import processing
from anthias_server.app.models import Asset
from anthias_server.settings import settings as anthias_settings


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
    ('codec', 'ext', 'container'),
    [
        ('libx264', '.mkv', 'matroska'),
        ('libx264', '.mov', 'mov'),
        ('libx265', '.mp4', 'mp4'),
        ('libx265', '.mkv', 'matroska'),
    ],
)
def test_video_passthrough_for_h264_or_hevc_in_known_containers(
    asset_dir: str,
    codec: str,
    ext: str,
    container: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H.264 and HEVC in any of the accepted containers passes
    through *on a board profile that supports HEVC*. Pin
    ``DEVICE_TYPE=pi5`` so the libx265-source rows hit passthrough
    rather than getting transcoded back down to H.264 by the
    default profile."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, f'sample{ext}')
    _make_video(src, codec=codec, container=container, audio='aac')
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

    def fake_transcode(_in: str, out: str, _profile: Any = None) -> None:
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

    def explode(_in: str, staging: str, _profile: Any = None) -> None:
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

    def explode(_in: str, staging: str, _profile: Any = None) -> None:
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

    def empty_transcode(_in: str, staging: str, _profile: Any = None) -> None:
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

    def good_transcode(_in: str, staging: str, _profile: Any = None) -> None:
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
    pi5 = processing._BOARD_PROFILES['pi5']
    assert processing._video_can_passthrough(summary, pi5), (
        f'{description}: passthrough check rejected a real-ffprobe-name '
        f'container; would force an unnecessary transcode'
    )


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
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'clip.mp4')
    with open(src, 'wb') as fh:
        fh.write(b'\x00' * 64)
    asset = _make_processing_asset('vid-no-2nd-probe', src, mimetype='video')

    summary = {
        'container': 'mp4',
        'video_codec': 'h264',
        'audio_codec': 'aac',
        'duration_seconds': 42,
    }
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
        'audio_codec': 'unknown',
        'duration_seconds': None,
    }


@pytest.mark.parametrize(
    ('summary', 'expected'),
    [
        # Happy path: H.264 + AAC in mp4
        (
            {'container': 'mp4', 'video_codec': 'h264', 'audio_codec': 'aac'},
            True,
        ),
        # HEVC in mkv with no audio (board profile must allow hevc)
        (
            {'container': 'mkv', 'video_codec': 'hevc', 'audio_codec': 'none'},
            True,
        ),
        # Unknown container — fail
        (
            {'container': 'avs', 'video_codec': 'h264', 'audio_codec': 'aac'},
            False,
        ),
        # Exotic codec — fail
        (
            {
                'container': 'mov',
                'video_codec': 'prores',
                'audio_codec': 'pcm_s16le',
            },
            False,
        ),
        # Unknown audio codec — fail (we'd have to demux it out)
        (
            {
                'container': 'mp4',
                'video_codec': 'h264',
                'audio_codec': 'truehd',
            },
            False,
        ),
        # All unknowns (probe failed) — fail safely → transcode
        (
            {
                'container': 'unknown',
                'video_codec': 'unknown',
                'audio_codec': 'unknown',
            },
            False,
        ),
    ],
)
def test_video_can_passthrough_decision_table(
    summary: dict[str, str], expected: bool
) -> None:
    """Exhaustive truth table for ``_video_can_passthrough``. Catches
    a future change to the passthrough sets that wasn't intended.
    Pins the board profile to ``pi5`` (which accepts both h264 + hevc)
    so the legacy "happy path" cases stay equivalent — separate
    per-board tests below cover the pi2/pi3 H.264-only branch."""
    pi5_profile = processing._BOARD_PROFILES['pi5']
    assert processing._video_can_passthrough(summary, pi5_profile) is expected


# ---------------------------------------------------------------------------
# Per-board transcode profile (the codec grid)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('device_type', 'expected_target'),
    [
        ('pi2', 'h264'),
        ('pi3', 'h264'),
        ('pi4-64', 'hevc'),
        ('pi5', 'hevc'),
        ('x86', 'hevc'),
        # Unset / unknown env var falls back to H.264 — the most
        # compatible codec for any Anthias-supported device.
        ('', 'h264'),
        ('weird-future-board', 'h264'),
    ],
)
def test_resolve_board_profile_picks_target_codec_per_board(
    device_type: str, expected_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transcode target lives in a board profile keyed by
    ``DEVICE_TYPE``. This regression-tests the grid in one place so a
    future "let's also build a pi6 image" rollout can't silently fall
    through to H.264 if it forgets to register a profile entry."""
    monkeypatch.setenv('DEVICE_TYPE', device_type)
    profile = processing._resolve_board_profile()
    assert profile['transcode_target'] == expected_target


@pytest.mark.parametrize(
    ('device_type', 'video_codec', 'expected_passthrough'),
    [
        # pi2 / pi3: VLC + mmal-vc4. H.264 only. HEVC must transcode.
        ('pi2', 'h264', True),
        ('pi2', 'hevc', False),
        ('pi3', 'h264', True),
        ('pi3', 'hevc', False),
        # pi4-64 / pi5 / x86: mpv with HEVC support. Both codecs OK.
        ('pi4-64', 'h264', True),
        ('pi4-64', 'hevc', True),
        ('pi5', 'h264', True),
        ('pi5', 'hevc', True),
        ('x86', 'h264', True),
        ('x86', 'hevc', True),
        # Default profile is H.264-only — safer for unknown boards.
        ('', 'h264', True),
        ('', 'hevc', False),
    ],
)
def test_video_can_passthrough_respects_board_codec_set(
    device_type: str,
    video_codec: str,
    expected_passthrough: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pi3 device must not passthrough an HEVC upload; a pi5 device
    must. The test pins ``DEVICE_TYPE`` rather than passing the
    profile explicitly so the env-resolution code path is exercised
    end-to-end (mirrors how the celery worker decides at runtime)."""
    monkeypatch.setenv('DEVICE_TYPE', device_type)
    summary = {
        'container': 'mp4',
        'video_codec': video_codec,
        'audio_codec': 'aac',
    }
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
    board's expected output. Mocks ``sh.ffmpeg`` so no actual encode
    runs — we only care about the argv shape here."""
    monkeypatch.setenv('DEVICE_TYPE', device_type)

    captured: dict[str, Any] = {}

    def fake_ffmpeg(*args: Any, **kwargs: Any) -> None:
        captured['args'] = list(args)
        captured['kwargs'] = kwargs

    with mock.patch.object(sh, 'ffmpeg', side_effect=fake_ffmpeg):
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


@pytest_ffmpeg
@pytest.mark.django_db
def test_video_passthrough_records_target_codec(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passthrough rows still get ``transcode_target`` written so the
    operator can see "this device wanted hevc, the upload already was
    hevc, no work needed"."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'sample.mp4')
    _make_video(src, codec='libx264', container='mp4', audio='aac')
    asset = _make_processing_asset('vid-pass-pi5', src, mimetype='video')

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

    def fake_transcode(_in: str, staging: str, _profile: Any = None) -> None:
        # Capture the profile that was selected and produce a stub
        # output so the runner can finalise the row.
        captured['profile'] = _profile
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
    assert captured['profile']['transcode_target'] == 'h264'


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

    summary = {
        'container': 'mp4',
        'video_codec': 'h264',
        'audio_codec': 'aac',
    }
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
