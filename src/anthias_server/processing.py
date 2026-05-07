"""Asset normalisation pipeline.

Two Celery tasks that run on every fresh upload:

* ``normalize_image_asset`` — converts every extension in
  ``NORMALIZE_IMAGE_EXTS`` (HEIC / HEIF / TIFF / BMP / ICO / TGA /
  JPEG 2000 family / AVIF) to lossless WebP via Pillow (+ pillow-heif
  for the HEIF family). The Qt webview only ever needs to render
  formats it can already display, and storage on the device's SD
  card is the load-bearing concern for the uncompressed sources
  (BMP especially). JPEG / PNG / WebP / GIF / SVG short-circuit
  through the no-op branch — they're already viewer-friendly *and*
  well-compressed.
* ``normalize_video_asset`` — probes the upload's container/codec with
  ffprobe and either passes it through (rename only) or transcodes
  with ffmpeg's ``-threads 2`` to a board-appropriate codec: libx264
  on legacy Pi 2/Pi 3 (mmal-vc4 path; no hardware HEVC) and libx265
  with the iOS-friendly ``hvc1`` tag on Pi 4-64 / Pi 5 / x86 (mpv
  path; HEVC hardware-decoded on Pi 4 / x86, software on Pi 5). The
  on-device player only ever sees a codec it can decode.

Both tasks follow the YouTube-download Celery pattern in
``anthias_server.celery_tasks``:

* The upload-path serializer flips ``is_processing=True`` and enqueues
  the task before returning. The viewer treats in-flight rows as
  not-displayable and silently skips them during rotation.
* On success the task atomically replaces the file at the row's
  ``uri``, refreshes the duration where applicable, writes
  ``metadata['original_ext']`` / ``metadata['transcoded']`` /
  ``metadata['converted']``, and clears ``is_processing``.
* On failure the row's ``metadata['error_message']`` is filled in and
  ``is_processing`` is cleared via the custom ``Task.on_failure``
  hook so an operator can edit / delete the row instead of being
  stuck on the "Processing" pill forever.

Tasks run inside the same ``anthias-celery`` worker that handles the
existing ``download_youtube_asset`` flow. The compose file wraps the
worker command with ``nice -n 19 ionice -c 3`` so a transcode never
starves the on-device viewer; the ffmpeg invocation here additionally
caps thread count to two cores.
"""

from __future__ import annotations

import json
import logging
import os
from os import path
from typing import Any

import sh
from celery import Task
from PIL import Image, UnidentifiedImageError

from anthias_common.utils import get_video_duration
from anthias_server.app.models import Asset


# Containers whose H.264/HEVC payloads play directly in mpv / VLC
# on the Pi without remuxing. Anything outside this set falls through
# to a full transcode regardless of codec — a "passthrough" rename
# preserving a weird container would still need a downstream remux
# to land in MP4, and the viewer's media stack is happiest on .mp4.
# Keeping the list explicit also stops a typo'd extension from being
# silently retained.
#
# The set carries BOTH the short extension labels (``ts``, ``mkv``,
# ``mpg``) AND the canonical ffprobe ``format_name`` tokens
# (``mpegts``, ``matroska``, ``mpeg``) because the same set is
# matched against two sources of truth:
#
#   * the upload's filename extension (extension fallback path in
#     ``_ffprobe_summary`` — short labels), and
#   * ffprobe's reported ``format.format_name`` (canonical names).
#
# A pure short-label set would force unnecessary transcodes whenever
# ffprobe's name (e.g. ``mpegts`` for an MPEG-TS upload) didn't
# match the extension's short label (``ts``). Listing both keeps the
# decision aligned across detection paths.
_PASSTHROUGH_CONTAINERS = frozenset(
    {
        # Short extension labels (matched against filename ext).
        'mp4',
        'm4v',
        'mkv',
        'mov',
        'webm',
        'ts',
        'mpg',
        'mpeg',
        'flv',
        'avi',
        # ffprobe ``format_name`` tokens not already covered above.
        # ``matroska`` for .mkv (ffprobe reports ``matroska,webm``).
        # ``mpegts`` for .ts (ffprobe reports ``mpegts`` not ``ts``).
        # ``mov`` / ``mp4`` / ``mpeg`` / ``flv`` / ``avi`` / ``webm``
        # are the canonical names *and* extension labels — only
        # listed once above.
        'matroska',
        'mpegts',
    }
)


# Audio codecs the viewer can demux without a transcode. ``None`` is
# represented as the literal string ``'none'`` so a probe result with
# no audio stream still falls in the "passthrough OK" set.
_PASSTHROUGH_AUDIO_CODECS = frozenset(
    {'aac', 'mp3', 'opus', 'vorbis', 'ac3', 'none'}
)


# ---------------------------------------------------------------------------
# Per-board transcode profile
# ---------------------------------------------------------------------------
#
# The right "video codec" for an Anthias device depends on what the
# on-device player can hardware-decode (or software-decode at real
# time). The matrix this PR locks in:
#
#   ┌──────────┬─────────────────┬──────────────┬──────────────┐
#   │ Board    │ Player          │ HEVC OK?     │ Target codec │
#   ├──────────┼─────────────────┼──────────────┼──────────────┤
#   │ pi2/pi3  │ VLC + mmal-vc4  │ no           │ H.264        │
#   │ pi4-64   │ mpv + V4L2 HEVC │ HW-decoded   │ HEVC         │
#   │ pi5      │ mpv + SW decode │ A76 SW @ 1080p │ HEVC       │
#   │ x86      │ mpv + va/nv/qsv │ HW-decoded   │ HEVC         │
#   │ unset    │ (dev / unknown) │ assume no    │ H.264        │
#   └──────────┴─────────────────┴──────────────┴──────────────┘
#
# Two reasons to actually emit HEVC instead of always-H.264:
#
#   1. Storage. Anthias devices have small SD cards / eMMC modules; an
#      HEVC re-encode at equivalent visual quality is roughly 30–50%
#      smaller than H.264. For a fleet rotating dozens of clips that
#      compounds.
#   2. Decode load. Pi 5 has no hardware video decoder at all; the CPU
#      handles every codec in software. HEVC's better compression at
#      the same quality means fewer bits the decoder has to chew
#      through, which trades coding-tool complexity for raw
#      bandwidth — a wash on Pi 5 in practice, but never worse.
#
# The mapping keys match ``DEVICE_TYPE`` (set by the image builder in
# the Dockerfile, read at celery-task time via ``os.environ``) rather
# than the runtime-detected ``get_device_type()``. The celery worker
# shares the env var with anthias-server; it does NOT mount
# ``/proc/device-tree/model`` from the host. The image builder also
# uses these exact strings (``pi2`` / ``pi3`` / ``pi4-64`` / ``pi5`` /
# ``x86``), so a build-time decision and the transcode-time decision
# always agree. Fallback to ``_DEFAULT_PROFILE`` (H.264) when the env
# var is unset — keeps the dev-environment path safe and gives an
# unknown future board the most-compatible codec.
_BoardProfile = dict[str, Any]


# ffmpeg encoder args. Each list is what gets passed between ``-i
# <input>`` and ``<output>`` for the video stream — audio always
# becomes AAC 192k via _AUDIO_TRANSCODE_ARGS. ``-tag:v hvc1`` on the
# HEVC encoder writes the iOS-friendly ``hvc1`` codec tag instead of
# ffmpeg's default ``hev1``; mpv/VLC handle either, but hvc1 is the
# broader-compat choice if we ever serve these files to a browser.
#
# CRF values are chosen to roughly match perceived quality across
# codecs: libx264 CRF 23 ≈ libx265 CRF 28. Both leave plenty of
# headroom for a fleet's typical image-and-text signage content.
_H264_VIDEO_ARGS = [
    '-c:v',
    'libx264',
    '-preset',
    'medium',
    '-crf',
    '23',
]

_HEVC_VIDEO_ARGS = [
    '-c:v',
    'libx265',
    '-preset',
    'medium',
    '-crf',
    '28',
    '-tag:v',
    'hvc1',
]

_AUDIO_TRANSCODE_ARGS = ['-c:a', 'aac', '-b:a', '192k']


_DEFAULT_PROFILE: _BoardProfile = {
    # Default lands on H.264 — safe on every Anthias-supported device,
    # and the fallback for ``DEVICE_TYPE`` unset (dev environment) or
    # an unrecognised value.
    'transcode_target': 'h264',
    'passthrough_video_codecs': frozenset({'h264'}),
    'video_args': _H264_VIDEO_ARGS,
}


_BOARD_PROFILES: dict[str, _BoardProfile] = {
    # Legacy 32-bit Pi boards: VLC + mmal-vc4 path. mmal hardware
    # decode is H.264-only, the CPU is too slow to software-decode
    # 1080p HEVC, so HEVC is *not* in the passthrough set — uploading
    # an HEVC clip to a pi2/pi3 must go through a libx264 transcode.
    'pi2': {
        'transcode_target': 'h264',
        'passthrough_video_codecs': frozenset({'h264'}),
        'video_args': _H264_VIDEO_ARGS,
    },
    'pi3': {
        'transcode_target': 'h264',
        'passthrough_video_codecs': frozenset({'h264'}),
        'video_args': _H264_VIDEO_ARGS,
    },
    # 64-bit Pi 4 with mpv + KMS (`--vo=drm`): the kernel's V4L2
    # stateful HEVC decoder driver (/dev/video10 family) is wired up
    # and mpv's ``--hwdec=auto-safe`` selects ``v4l2request`` for
    # hevc. Both H.264 and HEVC pass through.
    'pi4-64': {
        'transcode_target': 'hevc',
        'passthrough_video_codecs': frozenset({'h264', 'hevc'}),
        'video_args': _HEVC_VIDEO_ARGS,
    },
    # Pi 5: no hardware video decoder block at all (RP1 dropped it
    # vs. pi4). The Cortex-A76 quad-core software-decodes 1080p H.264
    # *and* 1080p HEVC at real time, so HEVC is fine. Picking HEVC
    # also saves disk: a typical 5-minute clip is ~30% smaller after
    # re-encode than the equivalent H.264 at perceptual parity.
    'pi5': {
        'transcode_target': 'hevc',
        'passthrough_video_codecs': frozenset({'h264', 'hevc'}),
        'video_args': _HEVC_VIDEO_ARGS,
    },
    # x86: mpv + ``--hwdec=auto-safe`` selects vaapi (Intel/AMD),
    # nvdec (NVIDIA), or qsv (Intel iGPU) and every modern x86
    # platform handles both H.264 and HEVC in hardware. Even on a
    # software-decode-only x86 box, the CPU has plenty of headroom.
    'x86': {
        'transcode_target': 'hevc',
        'passthrough_video_codecs': frozenset({'h264', 'hevc'}),
        'video_args': _HEVC_VIDEO_ARGS,
    },
}


def _resolve_board_profile() -> _BoardProfile:
    """Map the runtime ``DEVICE_TYPE`` env var to a transcode profile.

    The image builder writes ``DEVICE_TYPE=<board>`` into the server
    image's env at build time (see ``docker/Dockerfile.server.j2``);
    the celery worker inherits the same env. Looking it up here means
    a transcode pipeline running on a pi5 image always picks the pi5
    profile, even if the underlying CPU briefly looks different to
    /proc inspection (Balena / dev workflows can run amd64 builds on
    x86 hardware while still claiming a Pi target).

    Falls back to ``_DEFAULT_PROFILE`` (H.264) on:
      * unset env var (host dev environment, ``ENVIRONMENT=test``),
      * a future board name we haven't profiled yet.

    The H.264 default is the most compatible choice — every Anthias
    device, present and historic, plays libx264.
    """
    device_type = os.environ.get('DEVICE_TYPE', '').strip().lower()
    return _BOARD_PROFILES.get(device_type, _DEFAULT_PROFILE)


# Image extensions we route through the conversion task. The
# motivation differs by format:
#
#   * HEIC / HEIF — Qt webview can't render them at all on most
#     boards (libheif binding is server-side only via pillow-heif).
#   * TIFF — patchy browser support; a multi-page TIFF flattens
#     awkwardly. Normalising flattens it once, deterministically.
#   * BMP — uncompressed; a 4K BMP is ~30 MB vs ~1 MB as WebP.
#     Browsers do render BMP, but the on-disk size matters on a Pi
#     and BMP is a one-shot convert (Pillow built-in, no apt dep).
#   * ICO — Windows icons. Often multi-frame; the largest frame is
#     what we want, flattened to a single WebP.
#   * TGA — Truevision Targa (screenshot tools, game assets). No
#     browser support.
#   * JPEG 2000 (.jp2/.j2k/.jpx/.jpc/.jpf) — scanner output. No
#     browser support.
#   * AVIF — modern phone exports / Android camera output. Modern
#     Chromium renders AVIF, but the Qt5 WebEngine on legacy Pi 2/3
#     predates the AVIF support in Chromium 85, so converting on
#     upload guarantees the viewer renders correctly across the
#     fleet without per-board branching.
#
# JPEG / PNG / WebP / GIF / SVG stay untouched — already
# viewer-friendly *and* well-compressed.
#
# All formats above are handled by Pillow's built-in decoders (no
# extra apt or wheel dependency beyond pillow-heif, which is
# already required for HEIC/HEIF).
NORMALIZE_IMAGE_EXTS = frozenset(
    {
        '.heic',
        '.heif',
        '.tif',
        '.tiff',
        '.bmp',
        '.ico',
        '.tga',
        '.jp2',
        '.j2k',
        '.jpx',
        '.jpc',
        '.jpf',
        '.avif',
    }
)


def needs_image_normalisation(uri_or_filename: str) -> bool:
    """``True`` if the upload's extension is in ``NORMALIZE_IMAGE_EXTS``.

    The set covers HEIC / HEIF / TIFF / BMP / ICO / TGA / JPEG 2000
    family / AVIF — everything the pipeline knows how to convert to
    lossless WebP. Already-viewer-friendly formats (JPEG / PNG / WebP
    / GIF / SVG) return ``False`` and skip the Celery hop entirely.

    Covers both raw filenames (``foo.HEIC``) and the staged-upload
    URIs ``CreateAssetSerializerMixin`` writes (``<assetdir>/<id>.tif``).
    Case-insensitive — Pillow doesn't care, and operators routinely
    drag in ``.JPG`` / ``.HEIC`` from phone exports. Used by both the
    upload-time dispatch helpers below and by tests that need to
    assert "this filename would route through normalize".
    """
    return _ext(uri_or_filename) in NORMALIZE_IMAGE_EXTS


def dispatch_normalize_image(asset_id: str) -> None:
    """Queue ``normalize_image_asset`` for the just-persisted row.

    Lazy import of ``anthias_server.celery_tasks`` keeps this module
    importable from contexts (the API serializers, tests) that don't
    want celery's broker connection set up at import time. Mirrors
    ``anthias_common.youtube.dispatch_download``.
    """
    from anthias_server.celery_tasks import normalize_image_asset

    normalize_image_asset.delay(asset_id)


def dispatch_normalize_video(asset_id: str) -> None:
    """Queue ``normalize_video_asset`` for the just-persisted row."""
    from anthias_server.celery_tasks import normalize_video_asset

    normalize_video_asset.delay(asset_id)


def _row_or_none(asset_id: str) -> Asset | None:
    """Common entry-point guard for both normalisation tasks.

    A task firing for a row that's been deleted, or whose
    ``is_processing`` was cleared by a duplicate task / operator
    edit, must no-op rather than scribble over operator state.
    Mirrors ``download_youtube_asset``'s entry guard.
    """
    try:
        asset = Asset.objects.get(asset_id=asset_id)
    except Asset.DoesNotExist:
        return None
    if not asset.is_processing:
        return None
    return asset


def _ext(filename: str) -> str:
    """Lowercase trailing extension *with* the dot, or ``''``.

    Used to decide passthrough/transcode for video. ``os.path.splitext``
    returns the empty string when the filename has no extension, which
    is what we want in that case — don't synthesise one.
    """
    return path.splitext(filename)[1].lower()


# Operator-facing diagnostics surface via ``metadata.error_message``,
# which renders as a hover tooltip on the row's "Failed" pill — bytes
# repr (``b'...'``) reads as gibberish there. ``_STDERR_TAIL_BYTES``
# trims ffmpeg's pre-amble (build info, configuration, library
# versions) so the operator sees the actual error line, not 4 KB of
# noise.
_STDERR_TAIL_BYTES = 800


def _format_subprocess_stderr(exc: sh.ErrorReturnCode) -> str:
    """Decode the tail of a subprocess's stderr to a readable string.

    ``sh.ErrorReturnCode.stderr`` is ``bytes``; returning it via
    ``f'{exc.stderr!r}'`` would surface ``b'...'`` to the operator.
    Decode as UTF-8 with replacement (so a malformed byte doesn't
    crash the error path), strip whitespace, keep only the tail —
    ffmpeg's diagnostic always lands at the end. ``replace`` rather
    than ``ignore`` so we never silently swallow a broken byte that
    might have been the only signal.

    Trim happens *before* decode so the constant truly bounds bytes,
    not characters — multibyte UTF-8 sequences in the keep-window
    therefore can't push the decoded string over the limit. A
    decode-then-len trim would have surprised under non-ASCII
    output (rare in ffmpeg, but possible when an input filename
    appears in the diagnostic).
    """
    raw = exc.stderr or b''
    if isinstance(raw, str):
        text = raw
        if len(text.encode('utf-8')) > _STDERR_TAIL_BYTES:
            text = '…' + text[-_STDERR_TAIL_BYTES:]
        return text.strip()
    if len(raw) > _STDERR_TAIL_BYTES:
        # ``errors='replace'`` covers the case where the byte trim
        # cuts a multibyte UTF-8 sequence in half — the half-byte is
        # rendered as the replacement character rather than crashing.
        return (
            '…'
            + raw[-_STDERR_TAIL_BYTES:]
            .decode('utf-8', errors='replace')
            .strip()
        )
    return raw.decode('utf-8', errors='replace').strip()


def _set_processing_error(asset_id: str, message: str) -> None:
    """Persist a human-readable error and clear is_processing.

    Both tasks land here on a permanent failure (corrupt upload,
    encrypted PDF, broken HEIC). Writing ``metadata.error_message``
    instead of leaving the row stuck at ``is_processing=True`` is the
    contract called out by the issue's acceptance criteria. Operators
    surface the message via the v2 API's ``metadata`` field.
    """
    try:
        asset = Asset.objects.get(asset_id=asset_id)
    except Asset.DoesNotExist:
        return
    metadata = dict(asset.metadata or {})
    metadata['error_message'] = message
    Asset.objects.filter(asset_id=asset_id).update(
        is_processing=False,
        metadata=metadata,
    )


def _notify(asset_id: str) -> None:
    """Browser + viewer refresh nudge after a successful normalisation.

    Same trigger every other write path uses (see
    ``probe_video_duration`` / ``download_youtube_asset``). The
    publisher and notifier are imported lazily so this module stays
    importable from contexts that don't carry the Channels / Redis
    runtime (test collection on hosts without those wired up).
    """
    from anthias_common.utils import connect_to_redis
    from anthias_server.app.consumers import notify_asset_update

    try:
        connect_to_redis().publish('anthias.viewer', 'reload')
    except Exception:
        # The viewer poll picks up the change ~1 tick later; a Redis
        # flake here doesn't block the operator from seeing the new
        # asset.
        logging.exception('normalize task: viewer reload publish failed')
    try:
        notify_asset_update(asset_id)
    except Exception:
        logging.exception(
            'normalize task: notify_asset_update failed for %s', asset_id
        )


# ---------------------------------------------------------------------------
# Image normalisation: HEIC / HEIF / TIFF → lossless WebP
# ---------------------------------------------------------------------------


# pillow-heif registers itself with Pillow's plugin registry on import
# so ``Image.open`` recognises HEIC/HEIF MIME types. Importing it here
# (rather than inside the task body) means the registration cost is
# paid once at worker startup; subsequent images skip it. The import
# is wrapped because the package is *optional* — hosts running unit
# tests without the libheif1 apt package installed won't have the
# wheel resolved, and the image-conversion tests skip themselves
# rather than failing the whole suite. Production images install
# libheif1 + pillow-heif via the Dockerfile (see
# ``docker/Dockerfile.server.j2``).
try:  # pragma: no cover - import-time registration
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - graceful no-op on hosts w/o libheif
    logging.info(
        'pillow-heif not available; HEIC/HEIF uploads will fail until '
        'libheif1 + pillow-heif are installed in this environment.'
    )


class _NormalizeAssetTask(Task):  # type: ignore[type-arg]
    """Common ``on_failure`` for both normalisation tasks.

    Mirrors the YouTube task's failure handling: clears
    ``is_processing`` and writes ``metadata.error_message`` so the
    operator's table row never stays stuck on "Processing" after a
    crash. The message is the str() of the exception so it surfaces
    something concrete (``UnidentifiedImageError: cannot identify
    image file '/data/anthias_assets/abc.heic'``) without leaking a
    full traceback into the API response.
    """

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        asset_id = args[0] if args else kwargs.get('asset_id')
        if not asset_id:
            return
        try:
            _set_processing_error(asset_id, f'{type(exc).__name__}: {exc}')
            _notify(asset_id)
        except Exception:
            logging.exception(
                'normalize on_failure cleanup failed for %s', asset_id
            )


def _convert_image_to_webp(input_path: str, output_path: str) -> None:
    """Open ``input_path`` with Pillow, save lossless WebP to
    ``output_path``.

    Mode handling:
      * Always convert to RGBA so transparency present in HEIC / TIFF
        sources survives the round-trip. WebP supports an alpha
        channel; converting via ``RGB`` would silently flatten on a
        transparent background.
      * ``Image.open`` is lazy — the actual decode happens on first
        pixel access (``save`` triggers it). UnidentifiedImageError
        bubbles out of this function for ``on_failure`` to land on.

    Memory: keep the encode inside the ``with Image.open`` block.
    ``Image.convert('RGBA')`` already returns a new image with a
    full pixel buffer; calling ``.copy()`` on top of that doubled
    the memory cost — meaningful on a Pi 5 decoding a 50 MP HEIC
    where the pixel buffer is ~200 MB. By saving inside the
    context manager we hold exactly one decoded copy at a time.
    """
    with Image.open(input_path) as image:
        # ``convert('RGBA')`` is a no-op when the source is already
        # RGBA (e.g. an HEIC with alpha) and a colour-correct upcast
        # otherwise. The result is a new Image (its own pixel
        # buffer) that's safe to use after ``image`` closes — but
        # serialise inside the ``with`` so we never hold both the
        # source decoder state *and* the converted buffer at once.
        rgba = image.convert('RGBA')
        rgba.save(output_path, 'WEBP', lossless=True, method=6)


def _run_image_normalisation(asset: Asset) -> None:
    asset_id = asset.asset_id
    src_uri = asset.uri or ''
    if not src_uri or not path.isfile(src_uri):
        # Upload bytes never landed (cleanup() raced the operator,
        # disk pressure, ...). Fail clean.
        raise FileNotFoundError(f'image source missing: {src_uri!r}')

    src_ext = _ext(src_uri)
    if src_ext not in NORMALIZE_IMAGE_EXTS:
        # Defensive: caller routed something we don't convert.
        # Treat as a no-op success rather than re-encoding a JPEG.
        # Clearing ``error_message`` matters when the row is being
        # re-uploaded after a previous failed attempt — without it
        # the operator would still see the "Failed" pill on a row
        # that's now actually fine.
        metadata = dict(asset.metadata or {})
        metadata.pop('error_message', None)
        Asset.objects.filter(asset_id=asset_id).update(
            is_processing=False, metadata=metadata
        )
        _notify(asset_id)
        return

    base_no_ext = path.splitext(src_uri)[0]
    final_uri = f'{base_no_ext}.webp'
    # Stage to a sibling .tmp first so a crashed save doesn't leave a
    # half-written .webp behind for the viewer to choke on. cleanup()
    # already sweeps stale .tmp after 1h.
    staging = f'{final_uri}.tmp'

    def _drop_image_staging() -> None:
        # Mirror the video-pipeline cleanup contract: every failure
        # path through ``_convert_image_to_webp`` removes the staging
        # ``.webp.tmp`` before propagating, so a partial Pillow write
        # (disk pressure, libheif crash mid-decode) never leaves
        # debris for the operator to trip over.
        try:
            os.remove(staging)
        except OSError:
            pass

    try:
        _convert_image_to_webp(src_uri, staging)
    except UnidentifiedImageError as exc:
        # Pillow couldn't decode — almost always a corrupt upload.
        # Re-raise with a clearer name; on_failure formats the message.
        _drop_image_staging()
        raise UnidentifiedImageError(
            f'could not decode image {src_uri!r}: {exc}'
        ) from exc
    except Exception:
        # Any other failure (OSError / disk pressure, libheif crash,
        # WebP encoder rejecting the mode) must also clean up before
        # bubbling. ``except Exception`` rather than ``BaseException``
        # so KeyboardInterrupt / SystemExit still abort cleanly.
        _drop_image_staging()
        raise

    # Atomic rename within the same dir — POSIX guarantees this is
    # observed as a single inode swap. os.replace overwrites an
    # existing .webp (e.g. a re-run of the task on the same asset),
    # which is the right semantics here. A rename failure
    # (filesystem full, permissions, cross-device link mid-pipeline)
    # must still drop the staging file so the "no leftover .tmp"
    # contract holds — without the guard a stale .webp.tmp would
    # only get cleaned up by the cleanup() sweep an hour later.
    try:
        os.replace(staging, final_uri)
    except OSError:
        _drop_image_staging()
        raise

    # Drop the original now that the WebP has landed. cleanup() would
    # eventually sweep it as an orphan once the row's uri is updated,
    # but doing it inline saves the operator from seeing a stale
    # ``.heic`` ghost in /anthias_assets/ between the row update and
    # the next sweep.
    if final_uri != src_uri:
        try:
            os.remove(src_uri)
        except OSError:
            logging.exception(
                'normalize_image_asset: removing original %s failed',
                src_uri,
            )

    metadata = dict(asset.metadata or {})
    metadata['original_ext'] = src_ext
    metadata['converted'] = True
    metadata.pop('error_message', None)

    Asset.objects.filter(asset_id=asset_id).update(
        uri=final_uri,
        mimetype='image',
        is_processing=False,
        metadata=metadata,
    )

    _notify(asset_id)


# ---------------------------------------------------------------------------
# Video normalisation: ffprobe → passthrough or libx264/aac transcode
# ---------------------------------------------------------------------------


# How long a single transcode attempt is allowed to run. 30 minutes
# matches the ceiling called out in the issue; a 1080p H.264-source
# transcode of a typical 5-minute clip on a Pi 5 finishes in well
# under 2 minutes. The hard ceiling is the bound for the pathological
# case (mis-routed long-form upload, hung ffmpeg). Once exceeded,
# Celery kills the worker process and on_failure clears is_processing.
NORMALIZE_VIDEO_TIME_LIMIT_S = 60 * 30

# Wall-clock cap on the ffprobe call. A working ffprobe answers in
# under a second on small files; 60s covers a stalled-IO worst case
# and stops a hung process from blocking the worker indefinitely.
_FFPROBE_TIMEOUT_S = 60


def _ffprobe_streams(input_path: str) -> dict[str, Any]:
    """Return ffprobe's parsed JSON for ``input_path``.

    Uses ``-v error -show_format -show_streams -print_format json`` so
    we get a single document containing both container metadata and
    every stream's codec_name. Any non-zero exit (corrupt file, no
    streams, format detection refused) raises and the caller decides
    whether to error the row or fall back to the transcode branch.
    """
    out = sh.ffprobe(
        '-v',
        'error',
        '-show_format',
        '-show_streams',
        '-print_format',
        'json',
        input_path,
        _timeout=_FFPROBE_TIMEOUT_S,
    )
    parsed: dict[str, Any] = json.loads(str(out))
    return parsed


def _ffprobe_summary(input_path: str) -> dict[str, Any]:
    """Reduce ffprobe's payload to the dimensions we branch on.

    Returns a dict with four keys, all populated:
      * ``container`` — lowercase format token, ``'unknown'`` if
        ffprobe couldn't decide.
      * ``video_codec`` — lowercase codec name, ``'unknown'`` if
        the file has no video stream or the probe failed.
      * ``audio_codec`` — lowercase codec name, ``'none'`` when the
        file genuinely carries no audio stream, or ``'unknown'`` if
        the audio stream existed but ffprobe couldn't name its
        codec.
      * ``duration_seconds`` — integer seconds (floor 1) or
        ``None`` if ffprobe didn't report ``format.duration``.
        Pulled from the same probe payload so the runner doesn't
        re-shell ffprobe just for duration on the passthrough path.

    Any failure (timeout, ffprobe non-zero exit, ffprobe missing
    from PATH) collapses to all-'unknown' / ``duration_seconds=None``
    so the caller falls through to the transcode branch — better to
    spend the cycles re-encoding than to let an unplayable file sit
    in rotation.
    """
    try:
        probe = _ffprobe_streams(input_path)
    except (sh.TimeoutException, sh.ErrorReturnCode, sh.CommandNotFound):
        # ``CommandNotFound`` covers stripped-down images / dev
        # boxes without ffprobe in PATH — same recovery as a probe
        # that ran but couldn't decide: report 'unknown' across the
        # board so the runner falls through to the transcode (which
        # itself ultimately fails clean if ffmpeg is also missing).
        return {
            'container': 'unknown',
            'video_codec': 'unknown',
            'audio_codec': 'unknown',
            'duration_seconds': None,
        }
    streams = probe.get('streams') or []
    video = next(
        (s for s in streams if s.get('codec_type') == 'video'),
        None,
    )
    audio = next(
        (s for s in streams if s.get('codec_type') == 'audio'),
        None,
    )
    fmt_data = probe.get('format') or {}
    # Container resolution prefers ffprobe's ``format.format_name``
    # over the filename extension: a ``.mov`` file that's actually
    # MKV bytes (or a ``.bin`` extension hiding an mp4) would be
    # mis-classified as passthrough-eligible if we trusted the
    # filename. ffprobe reports a comma-joined list of synonyms
    # (e.g. ``mov,mp4,m4a,3gp,3g2,mj2``) — we accept the
    # passthrough decision if ANY token matches the supported set.
    # Falls back to the extension only when ffprobe couldn't
    # populate format_name (probe failed; shouldn't happen here
    # since we already returned 'unknown' above on probe error).
    fmt = fmt_data.get('format_name') or ''
    fmt_tokens = [t.strip().lower() for t in fmt.split(',') if t.strip()]
    if fmt_tokens:
        # Pick the first token that's in the passthrough set; if
        # none match, take the first reported token verbatim so
        # downstream branching produces a deterministic 'unknown'.
        container = next(
            (t for t in fmt_tokens if t in _PASSTHROUGH_CONTAINERS),
            fmt_tokens[0],
        )
    else:
        container = _ext(input_path).lstrip('.') or 'unknown'
    video_codec = ((video or {}).get('codec_name') or 'unknown').lower()
    if audio is None:
        audio_codec = 'none'
    else:
        audio_codec = (audio.get('codec_name') or 'unknown').lower()
    # Duration extracted from the same probe payload — the runner
    # uses this in the passthrough path so we don't shell ffprobe
    # twice (once for codec/container summary, once for duration)
    # on the common case. Floors to 1s so a sub-second clip can't
    # slot a 0s entry into the viewer rotation. Missing or
    # unparseable -> None.
    raw_duration = fmt_data.get('duration')
    duration_seconds: int | None
    if raw_duration is None:
        duration_seconds = None
    else:
        try:
            duration_seconds = max(1, int(float(raw_duration)))
        except (TypeError, ValueError):
            duration_seconds = None
    return {
        'container': container,
        'video_codec': video_codec,
        'audio_codec': audio_codec,
        'duration_seconds': duration_seconds,
    }


def _video_can_passthrough(
    summary: dict[str, Any],
    profile: _BoardProfile | None = None,
) -> bool:
    """``True`` if the file is in a format the *target board's* viewer
    plays directly.

    The probe needs to answer "yes" to all three questions: is the
    container one we accept; is the video codec one the board's
    player handles (H.264 only on pi2/pi3 — they have no HEVC
    hardware and an A53 CPU can't software-decode HEVC at 1080p; H.264
    + HEVC on pi4-64/pi5/x86); is the audio codec one of the
    demuxer-compatible set (or absent). Any 'unknown' answer (probe
    failed, exotic codec) triggers a transcode — better to spend the
    cycles than to let an unplayable file sit in the rotation.

    ``profile`` defaults to the board profile resolved from
    ``DEVICE_TYPE`` so callers don't have to thread it through. Tests
    pass a specific profile to assert per-board behaviour without
    mutating the env.
    """
    if profile is None:
        profile = _resolve_board_profile()
    if summary.get('container') not in _PASSTHROUGH_CONTAINERS:
        return False
    if summary.get('video_codec') not in profile['passthrough_video_codecs']:
        return False
    if summary.get('audio_codec') not in _PASSTHROUGH_AUDIO_CODECS:
        return False
    return True


def _transcode_to_target(
    input_path: str,
    output_path: str,
    profile: _BoardProfile | None = None,
) -> None:
    """Run a libx264 or libx265 transcode picked by the board profile.

    Profile decides codec + encoder args; the *invariants* are:

    * ``-y`` and ``-nostdin`` keep ffmpeg non-interactive (it would
      otherwise prompt on overwrite or block waiting for input).
    * ``-threads 2`` caps CPU usage so the viewer keeps two cores
      free on Pi 4 / Pi 5; combined with the ``nice -n 19 ionice -c
      3`` wrapper on the celery worker this means a transcode
      effectively never disrupts active playback. libx265 honours the
      same flag and parallelises within those two threads.
    * ``-c:a aac -b:a 192k`` matches every Anthias-supplied default
      asset's audio profile, regardless of video codec.
    * ``-movflags +faststart`` shifts the moov atom to the front of
      the file so playback can begin before the file is fully
      buffered — relevant when the viewer is fed via an HTTP serve
      later, and harmless otherwise.

    The ``profile`` parameter lets callers (read: tests) override the
    env-resolved profile so a single host can exercise both the
    libx264 and libx265 branches without mutating ``DEVICE_TYPE``.
    """
    if profile is None:
        profile = _resolve_board_profile()
    sh.ffmpeg(
        '-y',
        '-nostdin',
        '-threads',
        '2',
        '-i',
        input_path,
        *profile['video_args'],
        *_AUDIO_TRANSCODE_ARGS,
        '-movflags',
        '+faststart',
        output_path,
        _timeout=NORMALIZE_VIDEO_TIME_LIMIT_S,
    )


def _resolve_duration_seconds(uri: str) -> int | None:
    """ffprobe-driven duration for the post-transcode row.

    Used only on the transcode branch (where the file path changed
    so the summary's pre-transcode duration is no longer
    representative). The passthrough branch reuses the duration
    pulled from ``_ffprobe_summary`` to avoid a second ffprobe shell.

    Returns ``None`` when:
      * ffprobe is unavailable in this environment
        (``get_video_duration`` returns None on CommandNotFound),
      * the probe ran but couldn't extract a duration line, OR
      * the probe raised any exception.

    The exception-swallowing branch matters: ``get_video_duration``
    raises on ``sh.ErrorReturnCode_1`` ("Bad video format") and on
    bare ``Exception`` for unexpected failures. After a successful
    transcode the file is on disk and the row is otherwise ready —
    failing the *whole task* because the post-transcode duration
    probe stumbled would be an own-goal. Keep duration best-effort
    and let the operator edit the row's duration manually if
    needed.
    """
    try:
        delta = get_video_duration(uri)
    except Exception:
        logging.exception(
            'normalize_video_asset: post-transcode duration probe '
            'failed for %s; leaving duration unset',
            uri,
        )
        return None
    if delta is None:
        return None
    return max(1, int(delta.total_seconds()))


def _run_video_normalisation(asset: Asset) -> None:
    asset_id = asset.asset_id
    src_uri = asset.uri or ''
    if not src_uri or not path.isfile(src_uri):
        raise FileNotFoundError(f'video source missing: {src_uri!r}')

    src_ext = _ext(src_uri)
    summary = _ffprobe_summary(src_uri)
    profile = _resolve_board_profile()

    metadata = dict(asset.metadata or {})
    metadata['original_ext'] = src_ext
    metadata.pop('error_message', None)

    if _video_can_passthrough(summary, profile):
        # No re-encode. Keep the file at its current uri; flip the
        # in-progress flag and write the duration if ffprobe could
        # answer for it. Recording ``transcode_target`` even on the
        # passthrough path keeps an operator's metadata view
        # consistent — they can see "this device wanted hevc, the
        # upload already was hevc, no work needed" without inferring.
        metadata['transcoded'] = False
        metadata['transcode_target'] = profile['transcode_target']
        update: dict[str, Any] = {
            'is_processing': False,
            'metadata': metadata,
        }
        # Reuse the duration from ``_ffprobe_summary`` rather than
        # re-shelling ffprobe via ``get_video_duration``: the file
        # didn't move, so the summary's value is authoritative.
        # Saves one ffprobe invocation per passthrough row — the
        # common case on a per-board-codec-matched fleet.
        passthrough_duration = summary.get('duration_seconds')
        if isinstance(passthrough_duration, int):
            update['duration'] = passthrough_duration
        Asset.objects.filter(asset_id=asset_id).update(**update)
        _notify(asset_id)
        return

    # Transcode. Output lives next to the source as ``<base>.mp4``.
    # The staging file uses a `.staging.mp4` suffix rather than
    # ``.mp4.tmp`` because ffmpeg picks the muxer from the output
    # extension; ``.tmp`` makes it bail with "Unable to choose an
    # output format". The staging-file suffix sits inside the same
    # mtime guard as cleanup() so a crash mid-transcode still gets
    # GCed by the orphan-file sweep.
    base_no_ext = path.splitext(src_uri)[0]
    final_uri = f'{base_no_ext}.mp4'
    # ``staging`` deliberately uses a ``.staging.mp4`` suffix rather
    # than ``.mp4.tmp``: ffmpeg picks its muxer from the output
    # extension, and ``.tmp`` makes it bail with "Unable to choose an
    # output format". The suffix also guarantees ``staging != src_uri``
    # for the in-place transcode case (a non-h264 ``.mp4`` whose
    # ``base_no_ext`` matches): ffmpeg keeps reading from src_uri while
    # writing to a distinct path. ``os.replace`` then atomically swaps
    # the input out for the transcoded output.
    staging = f'{base_no_ext}.staging.mp4'

    def _drop_staging() -> None:
        # All transcode failure paths converge through this helper so
        # a partially-written staging file never lingers after a raise.
        # cleanup() would eventually GC it as an orphan, but doing it
        # inline keeps /anthias_assets/ free of debris an operator
        # might trip over.
        try:
            os.remove(staging)
        except OSError:
            pass

    try:
        _transcode_to_target(src_uri, staging, profile)
    except sh.TimeoutException as exc:
        # Time-limit overruns are surfaced as TimeoutException; let
        # on_failure land so is_processing clears.
        _drop_staging()
        raise RuntimeError(f'ffmpeg timed out for {src_uri!r}: {exc}') from exc
    except sh.ErrorReturnCode as exc:
        _drop_staging()
        # ``exc.stderr`` is bytes; ``!r`` would render it as
        # ``b'...'`` in the operator-facing metadata.error_message.
        # Decode + trim the tail for readability — ffmpeg's last few
        # lines of stderr are usually the diagnostic, the rest is
        # build-info noise.
        raise RuntimeError(
            f'ffmpeg failed for {src_uri!r}: {_format_subprocess_stderr(exc)}'
        ) from exc

    if not path.isfile(staging) or os.stat(staging).st_size == 0:
        # ffmpeg sometimes returns exit 0 but produces an empty file
        # (broken stream, silent codec mismatch). Reject the result
        # and clean up the empty file rather than promoting it.
        _drop_staging()
        raise RuntimeError(f'ffmpeg produced no output for {src_uri!r}')

    # Same rename-failure cleanup as the image pipeline: the atomic
    # rename normally succeeds in <1ms, but a filesystem-full /
    # permissions / cross-device error here would otherwise leave
    # the staging file hanging around. Mirror the contract by
    # dropping it on any OSError.
    try:
        os.replace(staging, final_uri)
    except OSError:
        _drop_staging()
        raise

    # Drop the original if it lived under a different name (e.g. a
    # ProRes .mov whose transcoded H.264 lands at the same base.mp4).
    if final_uri != src_uri:
        try:
            os.remove(src_uri)
        except OSError:
            logging.exception(
                'normalize_video_asset: removing original %s failed',
                src_uri,
            )

    duration = _resolve_duration_seconds(final_uri)

    metadata['transcoded'] = True
    # ``transcode_target`` records what we *aimed* to produce so an
    # operator can see "this row was re-encoded to hevc on a pi5
    # device" without re-probing the file. The actual codec landed in
    # the file is identical to this target — ffmpeg only deviates
    # silently if the encoder is unavailable, which is fatal at this
    # point (libx265 ships in the apt ffmpeg build for every Anthias
    # board, see the configure flags in image_builder).
    metadata['transcode_target'] = profile['transcode_target']
    update_dict: dict[str, Any] = {
        'uri': final_uri,
        'mimetype': 'video',
        'is_processing': False,
        'metadata': metadata,
    }
    if duration is not None:
        update_dict['duration'] = duration
    Asset.objects.filter(asset_id=asset_id).update(**update_dict)

    _notify(asset_id)
