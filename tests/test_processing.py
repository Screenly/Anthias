"""Unit tests for the upload-time normalisation pipeline.

Two tasks under test:

* ``normalize_image_asset`` — every extension in
  ``NORMALIZE_IMAGE_EXTS`` (HEIC / HEIF / TIFF / BMP / ICO / TGA /
  JPEG 2000 family / AVIF) → lossless WebP. JPEG / PNG / WebP / GIF
  / SVG short-circuit through the no-op branch.
* ``normalize_video_asset`` — runs ffprobe and writes codec / dims /
  fps / audio codec / container / duration into ``metadata``. The
  asset file is never rewritten; the operator's UI uses the metadata
  fields to identify clips the board can't decode in hardware.

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


@pytest.mark.django_db
def test_video_missing_file_raises_filenotfound(asset_dir: str) -> None:
    src = path.join(asset_dir, 'gone.mp4')
    asset = _make_processing_asset('vid-gone', src, mimetype='video')
    with mock.patch.object(processing, '_notify'):
        with pytest.raises(FileNotFoundError):
            processing._run_video_normalisation(asset)


@pytest_ffmpeg
@pytest.mark.django_db
def test_video_supported_codec_writes_metadata_and_clears_processing(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H.264 upload on a Pi 4 (which HW-decodes H.264) is accepted:
    the row gets codec / dims / fps written into ``metadata`` and
    ``is_processing`` is cleared."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    src = path.join(asset_dir, 'sample.mp4')
    _make_video(src, codec='libx264', container='mp4', audio='aac')
    asset = _make_processing_asset('vid-h264-pi4', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert asset.metadata.get('video_codec') == 'h264'
    assert asset.metadata.get('video_width') == 32
    assert asset.metadata.get('video_height') == 32
    # The asset file itself is untouched — no transcode.
    assert path.exists(src)


@pytest.mark.django_db
def test_video_unsupported_codec_raises_with_ffmpeg_recipe(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A codec outside the board's HW decode set is rejected. The
    exception's message names the rejected codec and supported set;
    its ``recipe`` attribute carries an ffmpeg command pre-filled with
    the upload's filename so the operator can copy-paste it verbatim.

    Pi 3 (H.264 only) is used here — an HEVC upload is the clearest
    unsupported-codec case for that board."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi3')
    src = path.join(asset_dir, 'sample.mp4')
    # Create a minimal placeholder — ffprobe is mocked below so the
    # file content doesn't matter, only path.isfile() needs to pass.
    with open(src, 'wb') as f:
        f.write(b'\x00' * 16)
    asset = _make_processing_asset(
        'vid-hevc-pi3',
        src,
        mimetype='video',
        metadata={'upload_name': 'beach-clip.mp4'},
    )
    fake_summary = {
        'container': 'mp4',
        'video_codec': 'hevc',
        'video_width': 1920,
        'video_height': 1080,
        'video_fps': 30.0,
        'audio_codec': 'aac',
        'duration_seconds': 60,
    }

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
    ):
        with pytest.raises(processing.UnsupportedVideoCodecError) as excinfo:
            processing._run_video_normalisation(asset)

    import shlex as _shlex

    msg = str(excinfo.value)
    assert "'hevc'" in msg
    assert 'h264' in msg
    recipe = excinfo.value.recipe
    assert (
        'libx264' in recipe
    )  # Pi 3 supports H.264 — recipe encodes to H.264.
    tokens = _shlex.split(recipe)
    assert tokens[1] == '-i'
    assert tokens[2] == 'beach-clip.mp4'
    assert tokens[-1] == 'beach-clip.h264.mp4'

    handbrake = excinfo.value.handbrake
    assert handbrake
    joined = ' '.join(handbrake)
    assert 'HandBrake' in joined
    assert processing.HANDBRAKE_URL in joined
    # H.264 board: the stock Fast 1080p30 preset is already H.264,
    # no encoder-switch step needed.
    assert 'H.265 (x265)' not in joined
    assert not any('Resolution Limit' in step for step in handbrake)

    asset.refresh_from_db()
    assert asset.metadata.get('video_codec') == 'hevc'
    assert asset.metadata.get('video_width') == 1920


@pytest.mark.django_db
def test_video_unsupported_codec_recipe_falls_back_to_upload_placeholder(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the row has no ``metadata.upload_name`` (YouTube downloads,
    pre-rebrand rows), the recipe uses a stable ``upload<ext>``
    placeholder so the operator still sees the correct input extension
    to substitute."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi3')
    src = path.join(asset_dir, 'noname.mp4')
    with open(src, 'wb') as f:
        f.write(b'\x00' * 16)
    asset = _make_processing_asset('vid-noname', src, mimetype='video')
    fake_summary = {
        'container': 'mp4',
        'video_codec': 'hevc',
        'video_width': 1920,
        'video_height': 1080,
        'video_fps': 30.0,
        'audio_codec': 'aac',
        'duration_seconds': 60,
    }

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
    ):
        with pytest.raises(processing.UnsupportedVideoCodecError) as excinfo:
            processing._run_video_normalisation(asset)

    import shlex as _shlex

    recipe = excinfo.value.recipe
    tokens = _shlex.split(recipe)
    assert tokens[1] == '-i'
    assert tokens[2] == 'upload.mp4'
    assert tokens[-1] == 'upload.h264.mp4'


@pytest.mark.django_db
def test_video_h264_accepted_on_pi5_software_decode(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H.264 is accepted on Pi 5 via software decode — the
    Cortex-A76 handles 1080p H.264 without frame drops, and YouTube
    rarely serves HEVC so blocking H.264 on Pi 5 would prevent all
    YouTube downloads. (GH #3092)"""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'sample.mp4')
    with open(src, 'wb') as f:
        f.write(b'\x00' * 16)
    asset = _make_processing_asset('vid-h264-pi5-sw', src, mimetype='video')
    fake_summary = {
        'container': 'mp4',
        'video_codec': 'h264',
        'video_width': 1920,
        'video_height': 1080,
        'video_fps': 30.0,
        'audio_codec': 'aac',
        'duration_seconds': 60,
    }

    with (
        mock.patch.object(processing, '_notify') as mock_notify,
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert asset.metadata.get('video_codec') == 'h264'
    mock_notify.assert_called_once_with('vid-h264-pi5-sw')


@pytest.mark.django_db
def test_video_unsupported_codec_still_rejected_on_pi5(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VP9 / AV1 uploads on Pi 5 are still rejected even though H.264
    software-decode was added to the Pi 5 codec set. The rejection recipe
    tells the operator to re-encode to H.264 (accepted via software decode —
    faster to encode than HEVC and still plays fine on Cortex-A76)."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'sample.mp4')
    with open(src, 'wb') as f:
        f.write(b'\x00' * 16)
    asset = _make_processing_asset('vid-vp9-pi5', src, mimetype='video')
    fake_summary = {
        'container': 'mp4',
        'video_codec': 'vp9',
        'video_width': 1920,
        'video_height': 1080,
        'video_fps': 30.0,
        'audio_codec': 'aac',
        'duration_seconds': 60,
    }

    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
    ):
        with pytest.raises(processing.UnsupportedVideoCodecError) as excinfo:
            processing._run_video_normalisation(asset)

    import shlex as _shlex

    recipe = excinfo.value.recipe
    tokens = _shlex.split(recipe)
    assert tokens[-1] == 'upload.h264.mp4'
    assert 'libx264' in recipe
    assert 'libx265' not in recipe


@pytest_ffmpeg
@pytest.mark.django_db
def test_video_unsupported_codec_h264_board_recipe(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A board that supports H.264 (Pi 4) gets a libx264 recipe —
    libx264 is significantly faster than libx265 for the operator."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    src = path.join(asset_dir, 'sample.mpg')
    _make_video(src, codec='mpeg2video', container='mpeg', audio=None)
    asset = _make_processing_asset('vid-mpeg2', src, mimetype='video')

    with mock.patch.object(processing, '_notify'):
        with pytest.raises(processing.UnsupportedVideoCodecError) as excinfo:
            processing._run_video_normalisation(asset)

    recipe = excinfo.value.recipe
    assert 'libx264' in recipe
    assert 'libx265' not in recipe


def test_pi3_64_hw_decode_set_is_h264_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 64-bit Qt6 Pi 3 board (``pi3-64``) runs the same VideoCore IV
    silicon as the 32-bit ``pi3`` — H.264-only HW decode, no HEVC. The
    gate must reject HEVC for it just like the armhf stream."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi3-64')
    assert processing._hw_decoded_codecs() == frozenset({'h264'})


@pytest.mark.parametrize(
    'filename',
    [
        "O'Brien.mp4",
        'two words.mov',
        'evil; rm -rf $HOME.mp4',
        'tick`uname`.mp4',
        'sub$(whoami).mp4',
    ],
)
def test_ffmpeg_recipe_quotes_hostile_filenames(filename: str) -> None:
    """``_ffmpeg_reencode_recipe`` must round-trip any filename through
    ``shlex`` so a user-supplied ``upload_name`` can't break out of the
    recipe's quoting and inject commands the operator copy-pastes.

    Round-trip means: ``shlex.split(recipe)`` recovers the *original*
    filename byte-for-byte in the input slot. If the recipe still
    interpolated raw (the pre-fix ``f"'{filename}'"`` path), the
    embedded quote / metachar would either truncate the token or shell-
    interpret on paste."""
    import shlex as _shlex

    recipe = processing._ffmpeg_reencode_recipe(frozenset({'h264'}), filename)
    tokens = _shlex.split(recipe)
    # ffmpeg -i <input> -c:v libx264 ... <output>
    assert tokens[0] == 'ffmpeg'
    assert tokens[1] == '-i'
    assert tokens[2] == filename
    # Output filename ends with .h264.mp4 and is the recipe's last token.
    assert tokens[-1].endswith('.h264.mp4')


@pytest_ffmpeg
@pytest.mark.django_db
def test_video_unknown_codec_is_rejected(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffprobe failure (codec reported as 'unknown') must reject the
    upload — we won't pass through a clip we can't certify against
    the board's HW decode set."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi4-64')
    src = path.join(asset_dir, 'sample.mp4')
    _make_video(src, codec='libx264', container='mp4', audio='aac')
    asset = _make_processing_asset('vid-unknown', src, mimetype='video')

    fake_summary = {
        'container': 'unknown',
        'video_codec': 'unknown',
        'video_pixels': None,
        'video_width': None,
        'video_height': None,
        'video_fps': None,
        'audio_codec': 'unknown',
        'duration_seconds': None,
    }
    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
        pytest.raises(processing.UnsupportedVideoCodecError) as excinfo,
    ):
        processing._run_video_normalisation(asset)

    assert 'unknown' in str(excinfo.value)


@pytest.mark.django_db
def test_video_arm64_catch_all_rejects_everything(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catch-all ``arm64`` DEVICE_TYPE has no entry in the HW
    decode map (an unknown aarch64 SBC isn't guaranteed to expose a
    v4l2-request decoder mpv can address). Without a host_agent
    subtype publish, every video upload is rejected — operator has
    to install a board-specific image to get HW decode."""
    monkeypatch.setenv('DEVICE_TYPE', 'arm64')
    src = path.join(asset_dir, 'sample.mp4')
    # Create an empty placeholder file so the FileNotFoundError check
    # passes; we mock ffprobe below.
    with open(src, 'wb') as f:
        f.write(b'\x00')
    asset = _make_processing_asset('vid-arm64', src, mimetype='video')

    fake_summary = {
        'container': 'mp4',
        'video_codec': 'h264',
        'video_pixels': 32 * 32,
        'video_width': 32,
        'video_height': 32,
        'video_fps': 10.0,
        'audio_codec': 'aac',
        'duration_seconds': 1,
    }
    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
        # Subtype absent — Redis returns None.
        mock.patch(
            'anthias_common.board.get_board_subtype', return_value=None
        ),
        pytest.raises(processing.UnsupportedVideoCodecError) as excinfo,
    ):
        processing._run_video_normalisation(asset)

    msg = str(excinfo.value)
    # Catch-all branch must explain the board-subtype gap rather
    # than the misleading "Supported: none." that earlier revisions
    # surfaced.
    assert 'subtype' in msg.lower()
    assert 'board-specific image' in msg.lower()


@pytest.mark.django_db
def test_video_arm64_with_rockpi4_subtype_accepts_h264(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Rock Pi 4 running the catch-all arm64 image gets its
    ``{h264, hevc}`` set once ``host:board_subtype=rockpi4`` is
    published by anthias_host_agent."""
    monkeypatch.setenv('DEVICE_TYPE', 'arm64')
    src = path.join(asset_dir, 'sample.mp4')
    with open(src, 'wb') as f:
        f.write(b'\x00')
    asset = _make_processing_asset('vid-rockpi', src, mimetype='video')

    fake_summary = {
        'container': 'mp4',
        'video_codec': 'h264',
        'video_pixels': 32 * 32,
        'video_width': 32,
        'video_height': 32,
        'video_fps': 10.0,
        'audio_codec': 'aac',
        'duration_seconds': 1,
    }
    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
        mock.patch(
            'anthias_common.board.get_board_subtype', return_value='rockpi4'
        ),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert asset.metadata.get('video_codec') == 'h264'


# ---------------------------------------------------------------------------
# Low-RAM resolution gate (boards with < 1.5 GiB MemTotal). On-device
# measurement on a 1 GB Rock Pi 4 showed 4K HEVC OOM-kills the viewer
# container (dmesg ``global_oom`` on the docker container's bash); the
# codec gate rejects above-1080p uploads on those boards so an operator
# gets a clear failure pill + downscale recipe instead of a wedged
# device.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_video_low_ram_rejects_4k_with_resolution_message(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 4K HEVC upload on a low-RAM board (codec is in the supported
    set, but resolution exceeds 1920×1080) is rejected with a
    resolution-specific message and a recipe that includes the
    downscale ``-vf scale=`` clause. Validates the gate fires *only*
    on the resolution leg — codec is otherwise fine."""
    monkeypatch.setenv('DEVICE_TYPE', 'arm64')
    src = path.join(asset_dir, 'big.mp4')
    with open(src, 'wb') as f:
        f.write(b'\x00')
    asset = _make_processing_asset(
        'vid-4k-lowram',
        src,
        mimetype='video',
        metadata={'upload_name': 'beach-4k.mp4'},
    )

    fake_summary = {
        'container': 'mp4',
        'video_codec': 'hevc',
        'video_pixels': 3840 * 2160,
        'video_width': 3840,
        'video_height': 2160,
        'video_fps': 30.0,
        'audio_codec': 'aac',
        'duration_seconds': 60,
    }
    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
        mock.patch(
            'anthias_common.board.get_board_subtype', return_value='rockpi4'
        ),
        mock.patch(
            'anthias_server.processing.is_low_ram_device', return_value=True
        ),
        pytest.raises(processing.UnsupportedVideoCodecError) as excinfo,
    ):
        processing._run_video_normalisation(asset)

    msg = str(excinfo.value)
    assert '3840x2160' in msg
    assert '1080p' in msg
    assert '1.5 GiB' in msg
    # Recipe carries the downscale clause + a board-appropriate
    # codec. rockpi4 supports H.264 so libx264 is preferred.
    recipe = excinfo.value.recipe
    assert '-vf scale=1920:1080:force_original_aspect_ratio=decrease' in recipe
    assert 'libx264' in recipe
    # Metadata still captured (operator sees what they uploaded).
    asset.refresh_from_db()
    assert asset.metadata.get('video_width') == 3840


@pytest.mark.django_db
def test_video_low_ram_accepts_1080p(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1080p HEVC on the same low-RAM board passes the gate — exactly
    the boundary the on-device measurement said survives."""
    monkeypatch.setenv('DEVICE_TYPE', 'arm64')
    src = path.join(asset_dir, 'hd.mp4')
    with open(src, 'wb') as f:
        f.write(b'\x00')
    asset = _make_processing_asset('vid-1080-lowram', src, mimetype='video')

    fake_summary = {
        'container': 'mp4',
        'video_codec': 'hevc',
        'video_pixels': 1920 * 1080,
        'video_width': 1920,
        'video_height': 1080,
        'video_fps': 30.0,
        'audio_codec': 'aac',
        'duration_seconds': 15,
    }
    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
        mock.patch(
            'anthias_common.board.get_board_subtype', return_value='rockpi4'
        ),
        mock.patch(
            'anthias_server.processing.is_low_ram_device', return_value=True
        ),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert asset.metadata.get('video_codec') == 'hevc'


@pytest.mark.django_db
def test_video_high_ram_accepts_4k(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 4K upload on a high-RAM board (Pi 5, Pi 4 4GB) is NOT gated
    by resolution — the low-RAM cap only fires when MemTotal sits
    below the threshold."""
    monkeypatch.setenv('DEVICE_TYPE', 'pi5')
    src = path.join(asset_dir, 'big.mp4')
    with open(src, 'wb') as f:
        f.write(b'\x00')
    asset = _make_processing_asset('vid-4k-pi5', src, mimetype='video')

    fake_summary = {
        'container': 'mp4',
        'video_codec': 'hevc',
        'video_pixels': 3840 * 2160,
        'video_width': 3840,
        'video_height': 2160,
        'video_fps': 30.0,
        'audio_codec': 'aac',
        'duration_seconds': 60,
    }
    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
        # High-RAM device — gate inactive.
        mock.patch(
            'anthias_server.processing.is_low_ram_device', return_value=False
        ),
    ):
        processing._run_video_normalisation(asset)

    asset.refresh_from_db()
    assert asset.is_processing is False
    assert asset.metadata.get('video_width') == 3840


@pytest.mark.django_db
def test_video_low_ram_unsupported_codec_recipe_includes_downscale(
    asset_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the upload fails BOTH the codec gate and the resolution
    gate (e.g. 4K MPEG-2 on Rock Pi 4 1GB), the codec message wins
    (codec is the strictly stronger rejection — operator has to
    re-encode either way) but the recipe folds in the downscale so
    the operator's re-encode also lands within the 1080p envelope.
    Saves them an iteration of "re-encoded the codec, now it fails
    the resolution gate too"."""
    monkeypatch.setenv('DEVICE_TYPE', 'arm64')
    src = path.join(asset_dir, 'old.mpg')
    with open(src, 'wb') as f:
        f.write(b'\x00')
    asset = _make_processing_asset('vid-mpeg2-4k', src, mimetype='video')

    fake_summary = {
        'container': 'mpeg',
        'video_codec': 'mpeg2video',  # Not in rockpi4's HW set.
        'video_pixels': 3840 * 2160,
        'video_width': 3840,
        'video_height': 2160,
        'video_fps': 30.0,
        'audio_codec': 'mp2',
        'duration_seconds': 60,
    }
    with (
        mock.patch.object(processing, '_notify'),
        mock.patch.object(
            processing, '_ffprobe_summary', return_value=fake_summary
        ),
        mock.patch(
            'anthias_common.board.get_board_subtype', return_value='rockpi4'
        ),
        mock.patch(
            'anthias_server.processing.is_low_ram_device', return_value=True
        ),
        pytest.raises(processing.UnsupportedVideoCodecError) as excinfo,
    ):
        processing._run_video_normalisation(asset)

    msg = str(excinfo.value)
    # Codec message wins (strictly stronger rejection).
    assert 'codec' in msg.lower()
    assert "'mpeg2video'" in msg
    # Recipe folds in the downscale so a single re-encode satisfies
    # both gates.
    recipe = excinfo.value.recipe
    assert '-vf scale=1920:1080:force_original_aspect_ratio=decrease' in recipe


def test_ffmpeg_recipe_omits_scale_clause_by_default() -> None:
    """The codec-only rejection path passes ``cap_to_1080p=False``
    (the default). Recipe must NOT include a scale clause then — we
    don't want to suggest a needless downscale when an HD codec
    swap is all that's wanted."""
    recipe = processing._ffmpeg_reencode_recipe(frozenset({'h264'}), 'foo.mkv')
    assert '-vf scale' not in recipe
    assert '-c:v libx264' in recipe


def test_ffmpeg_recipe_includes_scale_clause_when_capping() -> None:
    """``cap_to_1080p=True`` injects the
    ``-vf scale=1920:1080:force_original_aspect_ratio=decrease`` clause
    between the input and the codec arguments, so the operator's
    re-encode lands inside the 1920×1080 envelope. The
    ``force_original_aspect_ratio=decrease`` keeps the aspect ratio
    intact for landscape, ultrawide, and portrait sources alike."""
    recipe = processing._ffmpeg_reencode_recipe(
        frozenset({'h264'}), 'foo.mkv', cap_to_1080p=True
    )
    assert '-vf scale=1920:1080:force_original_aspect_ratio=decrease' in recipe
    # Order matters — scale must be before the encoder so the encoder
    # sees the downscaled frames.
    scale_idx = recipe.index('-vf')
    encoder_idx = recipe.index('-c:v')
    assert scale_idx < encoder_idx


def test_handbrake_steps_h264_board_uses_stock_preset_no_encoder_change() -> (
    None
):
    """An H.264 board's walkthrough leans entirely on the stock
    ``Fast 1080p30`` preset (which already outputs H.264 MP4), so it
    must NOT tell the operator to touch the Video Encoder dropdown.
    The download link and the upload-back step are always present."""
    steps = processing._handbrake_steps(frozenset({'h264', 'hevc'}))
    joined = ' '.join(steps)
    assert processing.HANDBRAKE_URL in joined
    assert 'Fast 1080p30' in joined
    # No encoder change for an H.264 board — the preset's default is
    # already H.264.
    assert 'Video Encoder' not in joined
    assert 'H.265 (x265)' not in joined
    assert any('upload' in step.lower() for step in steps)


def test_handbrake_steps_hevc_only_board_switches_encoder_to_x265() -> None:
    """A Pi 5 (HEVC only — no H.264 in its set) can't use the preset's
    default H.264, so the walkthrough adds a Video-tab step switching
    the encoder to ``H.265 (x265)`` — still on the ``Fast 1080p30``
    preset for the 1080p MP4 envelope."""
    steps = processing._handbrake_steps(frozenset({'hevc'}))
    joined = ' '.join(steps)
    assert 'Fast 1080p30' in joined
    assert 'Video Encoder' in joined
    assert 'H.265 (x265)' in joined


def test_handbrake_steps_always_target_1080p_preset() -> None:
    """The stock ``Fast 1080p30`` preset caps output at 1080p, so it
    doubles as the low-RAM resolution fix — there's no separate
    Dimensions / Resolution-Limit step to spell out, and the steps are
    identical regardless of the source resolution."""
    h264 = processing._handbrake_steps(frozenset({'h264'}))
    hevc = processing._handbrake_steps(frozenset({'hevc'}))
    for steps in (h264, hevc):
        assert any('Fast 1080p30' in step for step in steps)
        assert not any('Resolution Limit' in step for step in steps)


def test_handbrake_steps_empty_when_board_has_no_hw_decode() -> None:
    """An empty supported set (unrecognised arm64 board) has no
    transcode target, so there are no HandBrake steps to offer —
    matching ``_ffmpeg_reencode_recipe`` returning an empty string."""
    assert processing._handbrake_steps(frozenset()) == []


def test_exceeds_low_ram_pixel_cap_unknown_dims_returns_false() -> None:
    """ffprobe-failed uploads (``video_width=None``) skip the
    resolution gate — the codec gate already collapsed to ``unknown``
    and will reject them; double-rejecting on dimensions we can't
    measure would just be noise."""
    with mock.patch(
        'anthias_server.processing.is_low_ram_device', return_value=True
    ):
        assert processing._exceeds_low_ram_pixel_cap(None, None) is False
        assert processing._exceeds_low_ram_pixel_cap(0, 0) is False
        assert processing._exceeds_low_ram_pixel_cap(1920, None) is False


def test_exceeds_low_ram_pixel_cap_high_ram_returns_false() -> None:
    """High-RAM devices never hit the cap, even for >1080p sources."""
    with mock.patch(
        'anthias_server.processing.is_low_ram_device', return_value=False
    ):
        assert processing._exceeds_low_ram_pixel_cap(3840, 2160) is False
        assert processing._exceeds_low_ram_pixel_cap(1920, 1080) is False


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


def test_ffprobe_summary_prefers_extension_match_in_synonym_list() -> None:
    """ffprobe's ``format_name`` for the QuickTime family is a
    synonym list (e.g. ``mov,mp4,m4a,3gp,3g2,mj2``). Operator-facing
    metadata should match what the operator uploaded: an ``.mp4``
    file surfaces as ``mp4`` (not ``mov``, the first token), and an
    ``.m4v`` file surfaces as ``m4v`` if ffprobe includes it. Falls
    back to the first ffprobe token only when the extension doesn't
    appear in the list at all (extension-less URI / genuinely exotic
    container)."""
    mp4_format_name = 'mov,mp4,m4a,3gp,3g2,mj2'
    fake = {
        'format': {'format_name': mp4_format_name},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264'}],
    }
    # .mp4 → operator sees 'mp4', not 'mov'.
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.mp4')
    assert summary['container'] == 'mp4'

    # .m4a → 'm4a' (also in the synonym list).
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.m4a')
    assert summary['container'] == 'm4a'

    # No extension match (.bin hides mp4 bytes) → first ffprobe
    # token wins so the metadata still reflects something concrete.
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.bin')
    assert summary['container'] == 'mov'

    # Made-up format name with a misleading filename extension →
    # reported verbatim, no extension fallback (the file genuinely
    # isn't mp4 despite the name).
    fake = {
        'format': {'format_name': 'unsupported_format'},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264'}],
    }
    with mock.patch.object(processing, '_ffprobe_streams', return_value=fake):
        summary = processing._ffprobe_summary('fixture.mp4')
    assert summary['container'] == 'unsupported_format'


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


def test_video_task_declares_codec_rejection_as_expected() -> None:
    """The codec/resolution gate raises ``UnsupportedVideoCodecError``
    as a deliberate, operator-facing rejection — not a fault. The video
    task must list it in ``throws`` so Celery logs it at INFO without a
    traceback and sentry-sdk's CeleryIntegration skips it (it returns
    early on ``isinstance(exc, task.throws)``), keeping the gate from
    flooding Sentry. The image task never raises it, so it must not be
    swept into the image task's ``throws``.
    """
    from anthias_server.celery_tasks import (
        normalize_image_asset,
        normalize_video_asset,
    )

    assert processing.UnsupportedVideoCodecError in tuple(
        getattr(normalize_video_asset, 'throws', ())
    ), (
        'normalize_video_asset expected throws to include '
        'UnsupportedVideoCodecError so the by-design codec rejection '
        'is not reported to Sentry'
    )
    assert processing.UnsupportedVideoCodecError not in tuple(
        getattr(normalize_image_asset, 'throws', ())
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


@pytest.mark.django_db
def test_normalize_on_failure_unsupported_codec_persists_recipe(
    asset_dir: str,
) -> None:
    """``UnsupportedVideoCodecError`` is the gate's user-facing
    exception. on_failure must:

    * write the bare message into ``metadata.error_message`` (no
      ``UnsupportedVideoCodecError:`` class-name prefix — that's the
      P1 review finding), and
    * mirror the exception's ``recipe`` attribute into
      ``metadata.error_recipe`` so the Edit modal can render it in a
      copyable ``<code>`` block, and
    * mirror the exception's ``handbrake`` steps into
      ``metadata.error_handbrake`` for the GUI alternative.
    """
    asset = _make_processing_asset(
        'vid-onfail',
        path.join(asset_dir, 'fixture.mpg'),
        mimetype='video',
    )
    task = processing._NormalizeAssetTask()
    handbrake_steps = processing._handbrake_steps(frozenset({'h264', 'hevc'}))
    exc = processing.UnsupportedVideoCodecError(
        "Video codec 'mpeg2video' is not hardware-decoded on this "
        'device. Supported: h264, hevc.',
        recipe="ffmpeg -i 'fixture.mpg' -c:v libx264 'fixture.mp4'",
        handbrake=handbrake_steps,
    )

    with mock.patch.object(processing, '_notify'):
        task.on_failure(exc, 'task-id', (asset.asset_id,), {}, None)

    asset.refresh_from_db()
    assert asset.is_processing is False
    msg = asset.metadata['error_message']
    assert 'mpeg2video' in msg
    assert 'UnsupportedVideoCodecError' not in msg
    assert asset.metadata['error_recipe'] == (
        "ffmpeg -i 'fixture.mpg' -c:v libx264 'fixture.mp4'"
    )
    assert asset.metadata['error_handbrake'] == handbrake_steps


@pytest.mark.django_db
def test_normalize_on_failure_clears_stale_error_recipe_and_handbrake(
    asset_dir: str,
) -> None:
    """A subsequent non-recipe failure must clear any stale
    ``error_recipe`` / ``error_handbrake`` from a previous run,
    otherwise the modal would show an outdated recipe and HandBrake
    steps alongside the new error message."""
    asset = _make_processing_asset(
        'img-clears',
        path.join(asset_dir, 'fixture.tiff'),
        mimetype='image',
        metadata={
            'error_recipe': "ffmpeg -i 'old.mpg' -c:v libx264 'old.mp4'",
            'error_handbrake': ['old step one', 'old step two'],
        },
    )
    task = processing._NormalizeAssetTask()

    with mock.patch.object(processing, '_notify'):
        task.on_failure(
            UnidentifiedImageError('cannot decode'),
            'task-id',
            (asset.asset_id,),
            {},
            None,
        )

    asset.refresh_from_db()
    assert 'error_recipe' not in asset.metadata
    assert 'error_handbrake' not in asset.metadata


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
