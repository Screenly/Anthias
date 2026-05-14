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
from anthias_server.playback_envelope import (
    _ARM64_KEYS,
    PlaybackEnvelope,
    _redis_board_subtype,
    compute_envelope,
)


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


# Containers that the mp4 muxer/demuxer family treats as one. ffprobe
# reports an MP4 file's ``format_name`` as
# ``mov,mp4,m4a,3gp,3g2,mj2`` — ``mov`` first — because the demuxer
# is the same code path for every member of the QuickTime family.
# For envelope-vs-source comparison we collapse those to "mp4": the
# variant we render is an mp4 (always), and any source whose
# format_name claims any of these synonyms is mp4-compatible bytes.
# Without this, every mp4 upload would re-encode at the container
# gate even though the bytes are already exactly what we'd produce.
_MP4_FAMILY_CONTAINERS = frozenset(
    {'mp4', 'mov', 'm4v', 'm4a', '3gp', '3g2', 'mj2'}
)


# ---------------------------------------------------------------------------
# Per-board envelope: see anthias_server.playback_envelope
# ---------------------------------------------------------------------------
#
# Goal: every clip the viewer plays must be hardware-decoded on the
# target board. The envelope (codec + max_width + max_height +
# max_fps) is the canonical per-board contract — every variant on
# disk matches it. ``compute_envelope()`` reads ``DEVICE_TYPE`` and
# returns the right ``PlaybackEnvelope`` for the current process.
#
# Three rules govern passthrough vs transcode for a fresh upload:
#
#   1. Container must be mp4 (we always write mp4 — keeps the
#      variant filename convention `<id>.mp4` exception-free).
#   2. Source codec must equal envelope.codec exactly. The mpv
#      hwdec dispatch on Pi resolves per-codec (v4l2m2m-copy for
#      h264, drm-copy for hevc), so codec drift = wrong hwdec.
#   3. Source dimensions must fit inside the envelope cap; source
#      fps must not exceed it. The fps cap is one-way — we never
#      up-convert a sub-cap source by emitting ``-r``.
#
# When all three hold, we copy the source bytewise into the variant
# slot and preserve it as the sibling ``.original.<ext>``. When any
# fail, we run ffmpeg with the smallest set of flags that brings
# the output inside the envelope: ``-vf scale=...`` (only when over
# resolution) plus ``-r <cap>`` (only when over fps) plus the codec
# choice. The source survives at ``.original.<ext>`` either way, so
# an envelope change in the future re-renders from the master.


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
#
# ``-preset superfast`` is a deliberate Anthias-specific choice: the
# upload-time walker runs once per asset, so a 5-10× speedup vs
# ``medium`` saves the operator real time on every upload, and the
# slight increase in bitrate (typically 10-20%) is invisible on
# typical signage content (logos, photos, slow-motion product
# shots). On low-end SBCs the speedup is the difference between
# "operator sees the asset in rotation within 30 s" and "operator
# waits 5+ minutes per 4K clip" — measured live on the Rock Pi 4
# during this PR's validation. The viewer-side decode is HW
# regardless of encoder preset, so playback latency / smoothness
# is unaffected.
_H264_VIDEO_ARGS = [
    '-c:v',
    'libx264',
    '-preset',
    'superfast',
    '-crf',
    '23',
]

_HEVC_VIDEO_ARGS = [
    '-c:v',
    'libx265',
    '-preset',
    'superfast',
    '-crf',
    '28',
    '-tag:v',
    'hvc1',
]

_AUDIO_TRANSCODE_ARGS = ['-c:a', 'aac', '-b:a', '192k']


def _video_args_for_codec(codec: str) -> list[str]:
    """ffmpeg ``-c:v`` args for the envelope's codec.

    The dispatch is exhaustive — ``PlaybackEnvelope.from_dict``
    already rejects any codec outside ``{'h264', 'hevc'}``, so this
    can assume one of the two. A future envelope value would land a
    ``KeyError`` here, which is the right failure mode (loud, at the
    transcode boundary, not silent in the file on disk).
    """
    return {'h264': _H264_VIDEO_ARGS, 'hevc': _HEVC_VIDEO_ARGS}[codec]


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


def stamp_processing_start(asset_id: str) -> None:
    """Mark ``metadata['processing_started_at']`` to the current UTC.

    Read by the periodic reconciler (``reconcile_stuck_processing``)
    to age rows that have been ``is_processing=True`` longer than the
    longest reasonable transcode. Stored as an ISO-8601 string in the
    existing JSON metadata bag (no schema migration).

    Public (no leading underscore): the dispatch entry points for
    every upload path call this — including from outside this module
    (``anthias_common.youtube.dispatch_download``,
    ``anthias_server.celery_tasks.reconcile_stuck_processing``).
    Treat the symbol as a stable internal API; the reconciler depends
    on the field being written at every dispatch.

    Concurrency: the read-modify-write below is a known race against
    arbitrary other writers of ``Asset.metadata`` — a worker landing
    ``error_message`` between our SELECT and UPDATE, an operator
    PATCH, etc. would be clobbered by our UPDATE. The wider codebase
    accepts the same race in sibling helpers (see ``_set_processing_error``,
    the normalize-task success path). We accept it here because the
    practical window is microscopic: stamps fire at *dispatch* time,
    *before* the worker picks the task up, and the dispatching code
    has just created or filter-updated the same row a few statements
    earlier. ``select_for_update()`` would close it on Postgres /
    MySQL but is a no-op on SQLite — Anthias's only deployed backend
    — so we'd be paying complexity for zero practical safety today.

    The ``transaction.atomic()`` wrap gives the SELECT / UPDATE pair
    a transaction boundary on backends that support it; the savepoint
    is cheap and survives a future switch to a multi-writer backend
    without code changes here.

    No-op for a row that doesn't exist: a row that disappeared
    between dispatch enqueue and this update is silently dropped via
    the ``filter().first() is None`` guard. The two-query
    SELECT-then-UPDATE shape is deliberate — Django doesn't expose a
    cross-backend "merge into JSONField" primitive, so we read the
    existing bag, patch one key, and write it back.
    """
    from django.db import transaction
    from django.utils import timezone

    now_iso = timezone.now().isoformat()
    with transaction.atomic():
        asset = Asset.objects.filter(asset_id=asset_id).first()
        if asset is None:
            return
        metadata = dict(asset.metadata or {})
        metadata['processing_started_at'] = now_iso
        Asset.objects.filter(asset_id=asset_id).update(metadata=metadata)


def dispatch_normalize_image(asset_id: str) -> None:
    """Queue ``normalize_image_asset`` for the just-persisted row.

    Lazy import of ``anthias_server.celery_tasks`` keeps this module
    importable from contexts (the API serializers, tests) that don't
    want celery's broker connection set up at import time. Mirrors
    ``anthias_common.youtube.dispatch_download``.

    Stamps ``metadata.processing_started_at`` so the reconciler can
    age a row that the worker never picked up (crashed worker, OOM
    kill, dispatch enqueue racing a redis flake).
    """
    from anthias_server.celery_tasks import normalize_image_asset

    stamp_processing_start(asset_id)
    normalize_image_asset.delay(asset_id)


def dispatch_normalize_video(asset_id: str) -> None:
    """Queue ``normalize_video_asset`` for the just-persisted row.

    See ``dispatch_normalize_image`` for the metadata stamp rationale.
    """
    from anthias_server.celery_tasks import normalize_video_asset

    stamp_processing_start(asset_id)
    normalize_video_asset.delay(asset_id)


def dispatch_pending_normalize(serializer: Any, asset_id: str) -> None:
    """Branch on ``serializer._pending_normalize`` and dispatch.

    Single hand-off point shared by every API version's create view
    (v1 / v1.1 / v1.2 / v2). ``prepare_asset`` stamps the attribute
    *and* flips ``is_processing=True`` for image/video uploads that
    need normalisation; the create view then calls this helper after
    persistence so the matching Celery task picks the row up. v1 and
    v1.1 used to skip this dispatch entirely (the per-version create
    views grew the branching inline as v1.2 / v2 were added, and the
    legacy endpoints were left behind), which left every video
    uploaded through those endpoints stuck at ``is_processing=True``
    forever — see GH #2870. Centralising the branch here means the
    next added or renamed version inherits the dispatch automatically.

    ``serializer`` is typed as ``Any`` because the four versions use
    four different serializer classes (``CreateAssetSerializerV1_1``,
    ``CreateAssetSerializerV1_2``, ``CreateAssetSerializerV2``) which
    share the ``_pending_normalize`` attribute via the
    ``CreateAssetSerializerMixin`` but don't share a single declared
    base for typing. Missing-attribute is treated as "no normalisation
    needed" so a hypothetical future serializer that doesn't route
    through the mixin still wouldn't crash this code path.
    """
    pending = getattr(serializer, '_pending_normalize', None)
    if pending == 'image':
        dispatch_normalize_image(asset_id)
    elif pending == 'video':
        dispatch_normalize_video(asset_id)


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
    # Normalise to bytes so both the str-input and bytes-input
    # paths share the same byte-precise trim. ``sh.ErrorReturnCode``
    # surfaces stderr as bytes in practice, but the typing on the
    # sh side is loose — converting once here means a multibyte
    # character in either branch is bounded by the same byte
    # budget instead of one branch trimming by characters.
    if isinstance(raw, str):
        raw = raw.encode('utf-8', errors='replace')
    if len(raw) > _STDERR_TAIL_BYTES:
        # ``errors='replace'`` covers the case where the byte trim
        # cuts a multibyte UTF-8 sequence in half — the half-byte
        # renders as the replacement character rather than crashing.
        return (
            '…'
            + raw[-_STDERR_TAIL_BYTES:]
            .decode('utf-8', errors='replace')
            .strip()
        )
    return raw.decode('utf-8', errors='replace').strip()


def _set_processing_error(asset_id: str, message: str) -> None:
    """Persist a human-readable error and clear is_processing.

    Both tasks land here on a permanent failure (corrupt HEIC,
    truncated TIFF, ffmpeg refusing an exotic codec, ffmpeg
    producing a zero-byte transcode). Writing ``metadata.error_message``
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
    # Disable the row alongside clearing is_processing. The viewer's
    # scheduling.generate_asset_list filters on ``is_enabled`` + date
    # window only — it doesn't check ``metadata.error_message`` —
    # so without flipping is_enabled here, a failed conversion would
    # still get queued for playback even though the file at
    # ``Asset.uri`` is the unconverted (and likely unplayable)
    # original. The operator can re-enable from the dashboard once
    # they've fixed or replaced the upload; the new "Failed" pill
    # in _asset_row.html replaces the active toggle anyway, so the
    # operator notices before they re-enable.
    Asset.objects.filter(asset_id=asset_id).update(
        is_processing=False,
        is_enabled=False,
        metadata=metadata,
    )


def _notify(asset_id: str, *, reload_viewer: bool = True) -> None:
    """Browser refresh nudge, plus optional viewer playlist reload.

    Two notification kinds, both best-effort:

    * **Browser** — ``notify_asset_update`` posts to the dashboard
      WebSocket so the operator's table picks up the new
      title/duration/state without waiting for the 5s poll. Always
      fires.
    * **Viewer** — Redis-publish ``anthias.viewer:reload`` so the
      on-device viewer reloads its playlist. Only fires when the
      row has reached its terminal state — i.e. ``is_processing``
      just cleared and the file at ``Asset.uri`` is the one the
      viewer should play. Setting ``reload_viewer=False`` lets
      intermediate hops (e.g. ``download_youtube_asset`` writing
      the title before chaining into ``normalize_video_asset``)
      update the dashboard without churning the viewer through a
      reload it's only going to do again moments later.

    The publisher and notifier are imported lazily so this module
    stays importable from contexts that don't carry the Channels /
    Redis runtime (test collection on hosts without those wired up).
    """
    from anthias_server.app.consumers import notify_asset_update

    if reload_viewer:
        from anthias_common.utils import connect_to_redis

        try:
            connect_to_redis().publish('anthias.viewer', 'reload')
        except Exception:
            # The viewer poll picks up the change ~1 tick later; a
            # Redis flake here doesn't block the operator from
            # seeing the new asset.
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


# Hard cap on decoded image dimensions. A signage upload at the
# resolutions this fleet actually plays back tops out around 4K
# (~8 MP) and the largest legitimate phone-camera HEIC is ~50 MP.
# Setting the cap here protects the worker from decompression-bomb
# inputs that decode to billions of pixels (Pillow's default
# DecompressionBombWarning fires at ~89 MP with a softer
# DecompressionBombError at 2× — both are easy to bypass on certain
# HEIF/AVIF inputs through pillow-heif). _convert_image_to_webp
# checks ``image.size`` against this constant *before* any decode,
# so an oversized input is rejected from the format header rather
# than after a multi-GB allocation.
_MAX_IMAGE_PIXELS = 50_000_000

# Tighten Pillow's *global* default to the same value so any path
# that goes through ``Image.open`` outside the upload pipeline
# (e.g. a future viewer-side helper, a test fixture mistake) gets
# the same protection for free. Pillow's check warns at this
# threshold and raises DecompressionBombError at 2× — by that
# point ``_convert_image_to_webp``'s explicit guard has already
# rejected the input. Setting it here means lowering the default
# applies process-wide on the celery worker.
Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS


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

    Decompression-bomb guard: an attacker can craft a tiny image
    file (e.g. a few KB on disk) that decodes to billions of
    pixels and exhausts worker memory. Pillow ships
    ``MAX_IMAGE_PIXELS`` (default ~89 MP) which raises
    ``DecompressionBombError`` past 2× that threshold, but it warns
    only at the first level — and pillow-heif's own decoder can
    bypass the check entirely on certain HEIF/AVIF inputs. We add
    an explicit ``image.size`` cap at ``_MAX_IMAGE_PIXELS`` so the
    pipeline rejects oversized inputs deterministically before any
    pixel buffer is allocated. A 50 MP cap is well above any phone
    camera output (modern flagships top out around 200 MP only on
    the sensor — JPEG/HEIC files compress to a max of ~50 MP at the
    common 4:3 aspect ratios) but tiny compared to the ~10 GP
    payloads typical bomb fixtures advertise.
    """
    with Image.open(input_path) as image:
        # Reject decompression bombs *before* any decode work
        # happens. ``image.size`` is read from the format header
        # and doesn't trigger pixel decode, so this guard is cheap
        # even on a malicious file.
        width, height = image.size
        if width * height > _MAX_IMAGE_PIXELS:
            raise ValueError(
                f'image dimensions {width}x{height} exceed cap '
                f'{_MAX_IMAGE_PIXELS} pixels — refusing to decode'
            )
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

    Returns a dict with these keys, all populated:
      * ``container`` — lowercase format token, ``'unknown'`` if
        ffprobe couldn't decide.
      * ``video_codec`` — lowercase codec name, ``'unknown'`` if
        the file has no video stream or the probe failed.
      * ``video_pixels`` — ``width * height`` for the first video
        stream, ``None`` if no video stream or dimensions missing.
        Convenience for callers comparing against a total-pixel
        budget.
      * ``video_width`` / ``video_height`` — per-axis dimensions
        of the first video stream, ``None`` if no video stream or
        dimensions missing. The envelope passthrough check uses
        these axis-by-axis (an ultrawide 5760×1080 source has fewer
        total pixels than 4K but exceeds the width of a 3840×2160
        envelope and must transcode).
      * ``video_fps`` — the first video stream's average frame rate
        as a float, or ``None`` if no video stream or
        ``r_frame_rate`` was unparseable. Used by the playback-
        envelope transcode to decide whether to emit
        ``-r envelope.max_fps`` (only when source > cap; the cap
        is one-way and never up-converts sub-cap content).
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
            'video_pixels': None,
            'video_width': None,
            'video_height': None,
            'video_fps': None,
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
    # Width × height for resolution-gated passthrough (Pi 4 H.264).
    # ffprobe returns ``width`` / ``height`` only for video streams,
    # and only when the demuxer could decide. A missing or
    # unparseable value collapses to None so the gate falls back to
    # "transcode" rather than passing through a clip we can't size.
    try:
        vw = int((video or {}).get('width') or 0)
        vh = int((video or {}).get('height') or 0)
    except (TypeError, ValueError):
        vw = vh = 0
    video_width: int | None = vw if vw > 0 else None
    video_height: int | None = vh if vh > 0 else None
    video_pixels: int | None = vw * vh if vw > 0 and vh > 0 else None
    # Average frame rate, used by the envelope transcode to decide
    # whether to emit ``-r``. ffprobe writes ``r_frame_rate`` as a
    # rational ``num/den`` string (e.g. ``30000/1001`` for NTSC,
    # ``60/1`` for true 60 fps). Anything unparseable collapses to
    # ``None`` so the caller treats it as "we can't tell" and skips
    # the fps gate (the codec / resolution gates still fire).
    video_fps: float | None = None
    raw_fps = (video or {}).get('r_frame_rate')
    if raw_fps and isinstance(raw_fps, str) and '/' in raw_fps:
        num_str, _, den_str = raw_fps.partition('/')
        try:
            num, den = float(num_str), float(den_str)
            if den > 0:
                video_fps = num / den
        except ValueError:
            video_fps = None
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
        'video_pixels': video_pixels,
        'video_width': video_width,
        'video_height': video_height,
        'video_fps': video_fps,
        'audio_codec': audio_codec,
        'duration_seconds': duration_seconds,
    }


def _video_can_passthrough(
    summary: dict[str, Any],
    envelope: PlaybackEnvelope | None = None,
) -> bool:
    """``True`` if the file is already inside the envelope and we can
    skip the ffmpeg pass — copy bytes into the variant slot as-is.

    All four gates must pass:

    1. **Container** is mp4. The variant convention is fixed
       (`<id>.mp4`), so any non-mp4 source needs a remux at minimum
       and falls through to the transcode branch.
    2. **Video codec** equals the envelope's codec exactly. The mpv
       hwdec dispatch on Pi resolves per-codec; codec drift between
       variant on disk and the viewer's expected hwdec is exactly
       the silent-SW-fallback bug `bb27b186` closed.
    3. **Dimensions** fit ``envelope.max_width / max_height``.
       Probe failure (``video_pixels = None``) is treated as
       "don't passthrough" — we don't gamble on an unsized clip.
    4. **Frame rate** is at-or-under ``envelope.max_fps``. The cap
       is one-way: sub-cap content keeps its source rate, only
       over-cap content gets clamped via ``-r``.

    Plus the existing audio gate — anything outside the demuxer-
    compatible audio set triggers a re-encode regardless.

    ``envelope`` defaults to ``compute_envelope()`` for production
    callers; tests pass a specific value to exercise per-board rules
    without mutating ``DEVICE_TYPE``.
    """
    if envelope is None:
        envelope = compute_envelope()
    src_container = summary.get('container')
    if envelope.container_ext == 'mp4':
        # The mp4 muxer/demuxer family is one codepath in ffmpeg; any
        # synonym in ``_MP4_FAMILY_CONTAINERS`` is mp4-compatible
        # bytes for our purposes. See the constant's docstring for
        # why ffprobe routinely reports ``mov`` for true mp4 files.
        if src_container not in _MP4_FAMILY_CONTAINERS:
            return False
    elif src_container != envelope.container_ext:
        return False
    if summary.get('video_codec') != envelope.codec:
        return False
    # Dimension cap: per-axis ceiling, not a total-pixel budget. A
    # 5760×1080 ultrawide has fewer total pixels than 4K but exceeds
    # the 3840 width cap and must transcode. Probe failure on either
    # axis → bail to transcode.
    src_w = summary.get('video_width')
    src_h = summary.get('video_height')
    if src_w is None or src_h is None:
        return False
    if src_w > envelope.max_width or src_h > envelope.max_height:
        return False
    # FPS cap is one-way. ``video_fps = None`` means we couldn't size
    # the source, so we can't certify it; fall through to transcode
    # where the source rate is preserved (no ``-r`` flag emitted when
    # source is at-or-under cap).
    src_fps = summary.get('video_fps')
    if src_fps is None or src_fps > envelope.max_fps:
        return False
    if summary.get('audio_codec') not in _PASSTHROUGH_AUDIO_CODECS:
        return False
    return True


def _decode_hwaccel_args(source_codec: str | None) -> list[str]:
    """Return ffmpeg flags to use the board's hardware decoder for
    the *input* file, or ``[]`` if no HW path is available.

    The walker spends most of its wall-clock in the decode + encode
    pipeline; encoding HEVC is libx265 software on every supported
    SBC (no Pi or Rockchip SoC has HEVC HW encode), but the *decode*
    half of the pipeline can be offloaded to the same silicon mpv
    uses for playback. Per board / source-codec:

    * Pi 4 (V3D V4L2 M2M + rpi-hevc-dec): both H.264 and HEVC
      via ``-hwaccel drm`` (the +rpt1 ffmpeg's v4l2_request path).
    * Pi 5 (Hantro G2): HEVC only — no upstream H.264 HW path.
    * Rock Pi 4 (rkvdec + Hantro VPU, both via v4l2_request):
      both H.264 and HEVC via ``-hwaccel drm``.
    * x86 (VAAPI on Intel / AMD): both via
      ``-hwaccel vaapi -hwaccel_device /dev/dri/renderD128``.

    Pi 2 / Pi 3 / catch-all ``arm64`` get ``[]`` — no upstream HW
    decode path mpv can address in this build.

    Failure modes (kernel driver weird, /dev/video* permissions,
    device busy) raise inside ffmpeg, where the caller catches them
    and retries with the args stripped — see ``_transcode_to_target``.
    """
    if not source_codec:
        return []
    # Reuse the playback_envelope subtype probe so the walker and
    # the viewer agree on what board they're targeting.
    key = os.environ.get('DEVICE_TYPE', '').strip().lower()
    if key in _ARM64_KEYS:
        sub = _redis_board_subtype()
        if sub is not None:
            key = sub
    # ``drm`` hwaccel is what ffmpeg uses for v4l2_request stateless
    # decoders; vaapi for x86's iGPU. ``drm`` is also what Pi 4's
    # V3D path lands on under the +rpt1 ffmpeg build.
    if key in ('pi4-64', 'rockpi4'):
        return ['-hwaccel', 'drm']
    if key == 'pi5' and source_codec == 'hevc':
        return ['-hwaccel', 'drm']
    if key == 'x86':
        return [
            '-hwaccel',
            'vaapi',
            '-hwaccel_device',
            '/dev/dri/renderD128',
        ]
    return []


def _transcode_to_target(
    input_path: str,
    output_path: str,
    envelope: PlaybackEnvelope | None = None,
    source_summary: dict[str, Any] | None = None,
) -> None:
    """Run a libx264 or libx265 transcode that lands the output
    inside the playback envelope.

    Envelope decides codec + encoder args. The source summary, if
    provided, lets us add the *smallest* set of extra flags to
    bring the output within the envelope:

    * ``-vf scale=...`` only when source width or height exceeds the
      envelope cap. The expression preserves aspect ratio by
      letting the longer axis hit the cap and computing the other
      from the source's ratio (``-2`` on width = even-aligned auto,
      libx265 / libx264 both reject odd dimensions). Sub-cap
      sources are untouched.
    * ``-r envelope.max_fps`` only when source fps > cap. The cap
      is one-way — we never up-convert a sub-cap source. A
      ``video_fps = None`` (probe couldn't read r_frame_rate) is
      treated as "we don't know, don't emit -r" so the source's
      native rate is preserved.

    Invariants regardless of the optional flags:

    * ``-y`` and ``-nostdin`` keep ffmpeg non-interactive.
    * ``-threads 2`` caps CPU usage so two cores stay free for the
      viewer; combined with the celery worker's
      ``nice -n 19 ionice -c 3`` wrapper, a transcode effectively
      never disrupts active playback.
    * ``-c:a aac -b:a 192k`` matches every Anthias-supplied
      default asset's audio profile.
    * ``-movflags +faststart`` shifts the moov atom to the front
      of the file so playback can begin before the file is fully
      buffered.

    The ``envelope`` parameter lets callers (read: tests) override
    the env-resolved value to exercise both codec branches without
    mutating ``DEVICE_TYPE``.
    """
    if envelope is None:
        envelope = compute_envelope()
    vf_args: list[str] = []
    fps_args: list[str] = []
    if source_summary is not None:
        src_w = source_summary.get('video_width')
        src_h = source_summary.get('video_height')
        if (
            src_w is not None
            and src_h is not None
            and (src_w > envelope.max_width or src_h > envelope.max_height)
        ):
            # Scale the longer axis to its cap; let ffmpeg compute
            # the other from the source aspect (``-2`` rounds to
            # even, libx264/libx265 both reject odd dims). When the
            # source aspect is wider than the envelope, width is
            # the binding axis; when taller, height is. The two
            # ``min(...)`` arms encode both cases.
            vf_args = [
                '-vf',
                (
                    f"scale='if(gt(a,{envelope.max_width}/{envelope.max_height}),"
                    f"{envelope.max_width},-2)':"
                    f"'if(gt(a,{envelope.max_width}/{envelope.max_height}),"
                    f"-2,{envelope.max_height})'"
                ),
            ]
        src_fps = source_summary.get('video_fps')
        if src_fps is not None and src_fps > envelope.max_fps:
            fps_args = ['-r', str(envelope.max_fps)]
    src_codec = (source_summary or {}).get('video_codec')
    hwaccel_args = _decode_hwaccel_args(src_codec)

    def _run(hw: list[str]) -> None:
        sh.ffmpeg(
            '-y',
            '-nostdin',
            '-threads',
            '2',
            *hw,
            '-i',
            input_path,
            *vf_args,
            *fps_args,
            *_video_args_for_codec(envelope.codec),
            *_AUDIO_TRANSCODE_ARGS,
            '-movflags',
            '+faststart',
            output_path,
            _timeout=NORMALIZE_VIDEO_TIME_LIMIT_S,
        )

    if hwaccel_args:
        try:
            _run(hwaccel_args)
            return
        except sh.ErrorReturnCode as exc:
            # HW decode init can fail at runtime even when the board
            # nominally supports it (kernel driver mismatch, device
            # busy with another ffmpeg, /dev/dri permission quirk,
            # source bitstream the v4l2_request decoder doesn't
            # accept). Retry once with the args stripped — software
            # decode is slower but always works. We log the original
            # failure so an operator chasing a slow walker can see
            # the HW path is failing and fix the root cause.
            logging.warning(
                'ffmpeg HW decode (%s) failed for %r; falling back '
                'to software decode. Underlying ffmpeg stderr: %s',
                ' '.join(hwaccel_args),
                input_path,
                _format_subprocess_stderr(exc),
            )
    _run([])


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
    """Render the asset's playback variant against the current
    envelope, preserving the original next to it.

    Three on-disk states the function moves between, idempotently:

    * **Fresh upload** — ``asset.uri`` points at the file the upload
      serializer wrote; no ``.original.*`` sibling yet;
      ``metadata['original_uri']`` is absent. We rename the upload
      to ``.original.<ext>`` and render the variant from it.
    * **Legacy asset** (pre-envelope rollout) — ``asset.uri`` points
      at an in-place-rewritten variant; no sibling, no envelope key
      in metadata. Same treatment as fresh: rename the variant to
      ``.original.<ext>`` and render fresh. The "original" we
      preserve is whatever the asset already has; we can't get back
      the pre-Anthias upload but we never lose more than the next
      envelope-change step would have lost anyway.
    * **Re-render** — ``.original.<ext>`` already exists and is the
      authoritative source. We read from it and overwrite the
      variant at ``asset.uri`` (or its envelope-shape variant
      path). Original stays bit-identical across re-renders.

    On success: ``asset.uri`` points at the variant;
    ``metadata['original_uri']`` points at the original sibling;
    ``metadata['envelope']`` records which envelope the variant was
    rendered against (the walker reads this to decide if an asset
    is stale on the next envelope change).
    """
    asset_id = asset.asset_id
    src_uri = asset.uri or ''
    if not src_uri or not path.isfile(src_uri):
        raise FileNotFoundError(f'video source missing: {src_uri!r}')

    envelope = compute_envelope()

    metadata = dict(asset.metadata or {})
    metadata.pop('error_message', None)

    # Step 1: settle the original. If we already have one on disk
    # (re-render), read from there; otherwise treat ``asset.uri`` as
    # the original-to-preserve and rename it into the sibling slot.
    # The rename is atomic on every filesystem Anthias supports;
    # ``asset.uri`` reads from the new location for the rest of the
    # function regardless of which branch ran.
    base_no_ext = path.splitext(src_uri)[0]
    src_ext = _ext(src_uri)
    metadata['original_ext'] = src_ext
    final_uri = f'{base_no_ext}.{envelope.container_ext}'

    existing_original = metadata.get('original_uri')
    if existing_original and path.isfile(existing_original):
        source_for_render = existing_original
    else:
        # Move asset.uri to .original.<ext>. ``.original`` is the
        # marker; the trailing extension is whatever the upload
        # originally was (mp4, mov, mkv, …).
        original_path = f'{base_no_ext}.original{src_ext}'
        try:
            os.rename(src_uri, original_path)
        except OSError as exc:
            raise RuntimeError(
                f'failed to preserve source as .original.* '
                f'(from {src_uri!r} to {original_path!r}): {exc}'
            ) from exc
        metadata['original_uri'] = original_path
        source_for_render = original_path
        # Persist ``original_uri`` to the DB *before* attempting the
        # render so that ``cleanup`` recognises the renamed file as
        # claimed if the render fails mid-flight (disk-full, ffmpeg
        # crash, worker SIGKILL). Without this commit, the sweep
        # below would treat the freshly renamed ``.original.<ext>``
        # as an orphan -- ``Asset.uri`` still points at the variant
        # path which no longer exists on disk -- and silently delete
        # the source bytes on its next 1h tick. Tested live on the
        # Pi 4 with a disk-full mid-walker run.
        Asset.objects.filter(asset_id=asset_id).update(metadata=metadata)

    # Re-probe the source from its (possibly new) location. The
    # passthrough decision below reads codec / dims / fps from this.
    summary = _ffprobe_summary(source_for_render)

    passthrough = _video_can_passthrough(summary, envelope)
    duration: int | None
    if passthrough:
        # Variant is identical to the original (codec + dims + fps
        # all match envelope, container is mp4). Copy bytes into the
        # variant slot. We deliberately keep both files: cross-
        # device fleet sha256 needs the variant as-is, and operators
        # asking "what was uploaded" need the original. Disk cost
        # is bounded — the file is the same content twice.
        try:
            import shutil

            shutil.copyfile(source_for_render, final_uri)
        except OSError as exc:
            raise RuntimeError(
                f'failed to copy original to variant slot '
                f'({source_for_render!r} → {final_uri!r}): {exc}'
            ) from exc
        # Summary's duration is authoritative — bytes didn't change.
        passthrough_duration = summary.get('duration_seconds')
        duration = (
            passthrough_duration
            if isinstance(passthrough_duration, int)
            else None
        )
    else:
        # Transcode. Render through a staging file then atomic-
        # replace into ``final_uri``. The ``.staging.mp4`` suffix
        # avoids the ``.tmp`` muxer-detection issue ffmpeg has and
        # sits inside ``cleanup()``'s mtime guard so a crashed
        # transcode gets GCed as an orphan.
        staging = f'{base_no_ext}.staging.mp4'

        def _drop_staging() -> None:
            try:
                os.remove(staging)
            except OSError:
                pass

        try:
            _transcode_to_target(
                source_for_render,
                staging,
                envelope=envelope,
                source_summary=summary,
            )
        except sh.TimeoutException as exc:
            _drop_staging()
            raise RuntimeError(
                f'ffmpeg timed out for {source_for_render!r}: {exc}'
            ) from exc
        except sh.ErrorReturnCode as exc:
            _drop_staging()
            raise RuntimeError(
                f'ffmpeg failed for {source_for_render!r}: '
                f'{_format_subprocess_stderr(exc)}'
            ) from exc

        if not path.isfile(staging) or os.stat(staging).st_size == 0:
            _drop_staging()
            raise RuntimeError(
                f'ffmpeg produced no output for {source_for_render!r}'
            )

        try:
            os.replace(staging, final_uri)
        except OSError:
            _drop_staging()
            raise

        duration = _resolve_duration_seconds(final_uri)

    metadata['transcoded'] = not passthrough
    # ``transcode_target`` is kept for back-compat with the v1
    # serializer metadata surface (api/serializers/mixins.py and
    # operator diagnostic views read it). It now duplicates
    # ``envelope.codec`` — both record "what the on-disk variant's
    # codec is meant to be". Slated for deprecation after one
    # release cycle once the envelope field is fully wired into the
    # serializers.
    metadata['transcode_target'] = envelope.codec
    # ``envelope`` records which envelope this variant was rendered
    # against. The walker (``regenerate_for_envelope_change``) reads
    # this on server start: if the current envelope differs, the
    # asset is queued for re-render from its ``.original.*`` sibling.
    metadata['envelope'] = envelope.as_dict()
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
