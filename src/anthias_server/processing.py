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
* ``normalize_video_asset`` — runs ffprobe on the upload and records
  what it finds in ``metadata`` (codec, dimensions, fps, audio codec,
  container, duration). The file itself is never rewritten. Anthias
  does not transcode video on-device: the on-device libx265 /
  libx264 transcode path we tried in this PR's earlier revisions
  wedged a Pi 4's celery worker for 99 minutes on a single 4K60
  H.264 → HEVC pass before zombieing.

  What the upload gate holds instead is a per-board *allowlist* of
  codecs accepted at upload — deliberately not called a playability
  guarantee, because the entries do not all rest on the same evidence
  and three of them are known not to play cleanly (see the map's own
  note). Most rest on hardware decode; ``pi5``'s H.264 and ``rk3566``
  rest on measured software throughput, which is why such an entry
  also wants a resolution ceiling — ``rk3566`` has a measured one,
  ``pi5`` is deliberately unlisted until its 4K H.264 throughput is
  measured. ``_HW_DECODE_VIDEO_CODECS`` and ``_SW_DECODE_MAX_PIXELS``
  below hold the two halves. A rejected upload gets a re-encode
  recipe, and the metadata fields surface the codec / dims / fps on
  each row so the operator can see what they uploaded.

Both tasks follow the YouTube-download Celery pattern in
``anthias_server.celery_tasks``:

* The upload-path serializer flips ``is_processing=True`` and enqueues
  the task before returning. The viewer treats in-flight rows as
  not-displayable and silently skips them during rotation.
* On success the task writes the metadata fields and clears
  ``is_processing``. The image task additionally rewrites the file
  in place to WebP; the video task leaves the file unchanged.
* On failure the row's ``metadata['error_message']`` is filled in and
  ``is_processing`` is cleared via the custom ``Task.on_failure``
  hook so an operator can edit / delete the row instead of being
  stuck on the "Processing" pill forever.

Tasks run inside the same ``anthias-celery`` worker that handles the
existing ``download_youtube_asset`` flow. The compose file still
wraps the worker with ``nice -n 19 ionice -c 3`` and a memory limit;
those are defensive — none of the remaining task bodies are CPU-bound
now that the on-device video transcode is gone, but a Pillow decode
on a 100 MP TIFF can still pressure RAM on a 1 GB Pi 2.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import struct
from os import path
from typing import Any

import sh
from celery import Task
from PIL import Image, ImageOps, UnidentifiedImageError

from anthias_common.board import (
    ARM64_DEVICE_TYPES,
    is_low_ram_device,
    resolve_device_key,
)
from anthias_server.app.models import Asset

logger = logging.getLogger(__name__)

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
# JPEG / PNG / WebP / GIF / SVG are not *converted* — already
# viewer-friendly *and* well-compressed. The large ones among them
# still get resized on low-RAM boards; see
# ``DOWNSCALE_ONLY_IMAGE_EXTS`` below.
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


# Formats we deliberately do *not* transcode (they are already
# viewer-friendly and well-compressed) but which still routinely
# arrive far larger than any signage screen can show: a modern phone
# photo is a 12-50 MP JPEG, and design tools export oversized PNGs.
#
# On a low-RAM board those bypass ``NORMALIZE_IMAGE_EXTS`` entirely,
# so before this set existed nothing capped them: the file stayed at
# full resolution on disk and the *viewer* paid the decode at render
# time. Measured on a Pi 4 (787 MB, arm64): a 24 MP JPEG drives the
# viewer to ~120 MB RSS and 615 MB of swap — it survives, but only by
# swapping, and the margin disappears on the 512 MB boards.
#
# Deliberately narrow:
#   * GIF and animated WebP would need per-frame handling; a resize
#     that silently dropped the animation would be worse than leaving
#     the file alone.
#   * SVG is vector — there is no pixel buffer to cap.
# So this is exactly the two single-frame raster formats that carry
# real-world oversized photos.
DOWNSCALE_ONLY_IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png'})


def needs_low_ram_image_downscale(uri_or_filename: str) -> bool:
    """``True`` for an over-cap JPEG/PNG upload on a low-RAM board.

    Complements ``needs_image_normalisation``: that one asks "does
    this format need converting?", this one asks "is this file too
    big for this board to render comfortably?". A file can need the
    pipeline for either reason, and ``needs_image_processing`` below
    is the union the upload paths actually branch on.

    Unlike ``needs_image_normalisation`` (a pure extension check) this
    reads the file header to learn the dimensions. ``Image.open`` is
    lazy — ``.size`` comes from the format header without decoding a
    single pixel — so the cost is one small read even for a 90 MB
    source.

    Returns ``False`` (never blocks or delays an upload) when:
      * the board is not low-RAM — a Pi 4 / 5 / x86 renders these
        fine, and resizing an operator's photo unasked would be
        surprising;
      * the extension isn't one we resize in place;
      * the header can't be read at all (missing file, truncated
        upload, Pillow doesn't recognise it). A genuinely corrupt
        upload fails later with a real decode error and a message the
        operator can act on — guessing here would turn "corrupt file"
        into the far more confusing "silently skipped resize".

    **This function runs inside the upload request**, so it must not
    raise. In particular a decompression bomb has to be caught here:
    this module tightens ``Image.MAX_IMAGE_PIXELS``, so ``Image.open``
    raises ``DecompressionBombError`` — which derives straight from
    ``Exception``, *not* from ``OSError``/``ValueError`` — on a header
    declaring more than twice the cap. Uncaught, that surfaced as a 500
    on the upload instead of a handled asset.

    A bomb returns ``True`` rather than ``False``: routing it into the
    Celery task is what gets the operator a real answer. The task's
    ``_guard_and_fit_for_board`` rejects it deterministically and
    ``on_failure`` writes ``metadata.error_message`` and clears
    ``is_processing``, so the row shows a "Failed" pill explaining the
    dimensions — exactly how an over-cap HEIC/TIFF already behaves.
    Returning ``False`` would instead store the bomb silently and leave
    the viewer to choke on it at render time.
    """
    if _ext(uri_or_filename) not in DOWNSCALE_ONLY_IMAGE_EXTS:
        return False
    if not is_low_ram_device():
        return False
    try:
        with Image.open(uri_or_filename) as image:
            width, height = image.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        # Over the bomb cap. Hand it to the task, which rejects it with
        # an operator-visible message. ``DecompressionBombWarning`` is
        # listed because a warnings filter of ``error`` promotes it, and
        # it is a ``RuntimeWarning`` subclass that the tuple below would
        # otherwise miss too.
        return True
    except (OSError, UnidentifiedImageError, ValueError):
        return False
    return _exceeds_low_ram_pixel_cap(width, height)


def needs_image_processing(uri_or_filename: str) -> bool:
    """``True`` if this image upload needs the normalisation pipeline.

    The union the upload paths branch on: a file earns a Celery hop
    either because its format needs converting to WebP
    (``needs_image_normalisation``) or because it is too large for a
    low-RAM board and needs resizing in place
    (``needs_low_ram_image_downscale``).

    Kept separate from the two predicates so each stays independently
    testable — and so the extension-only check remains a cheap pure
    function for callers that only care about format.
    """
    return needs_image_normalisation(uri_or_filename) or (
        needs_low_ram_image_downscale(uri_or_filename)
    )


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


def _set_processing_error(
    asset_id: str,
    message: str,
    recipe: str = '',
    handbrake: list[str] | None = None,
) -> None:
    """Persist a human-readable error and clear is_processing.

    Both tasks land here on a permanent failure (corrupt HEIC,
    truncated TIFF, ffmpeg refusing an exotic codec, ffmpeg
    producing a zero-byte transcode). Writing ``metadata.error_message``
    instead of leaving the row stuck at ``is_processing=True`` is the
    contract called out by the issue's acceptance criteria. Operators
    surface the message via the v2 API's ``metadata`` field.

    ``recipe``, when present, persists alongside the message as
    ``metadata.error_recipe`` — the dashboard renders it in a
    copyable ``<code>`` block in the Edit Asset modal. ``handbrake``,
    the equivalent point-and-click steps, persists as
    ``metadata.error_handbrake`` for operators who'd rather not use a
    terminal. Empty values clear any stale entries from a prior
    failure.
    """
    try:
        asset = Asset.objects.get(asset_id=asset_id)
    except Asset.DoesNotExist:
        return
    metadata = dict(asset.metadata or {})
    metadata['error_message'] = message
    if recipe:
        metadata['error_recipe'] = recipe
    else:
        metadata.pop('error_recipe', None)
    if handbrake:
        metadata['error_handbrake'] = handbrake
    else:
        metadata.pop('error_handbrake', None)
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
            logger.exception('normalize task: viewer reload publish failed')
    try:
        notify_asset_update(asset_id)
    except Exception:
        logger.exception(
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
    logger.info(
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
    crash. Two message shapes:

    * ``UnsupportedVideoCodecError`` — the gate's user-facing
      exception. The message body is already operator-readable
      (no class-name prefix); any attached ``recipe`` lands in
      ``metadata.error_recipe`` so the modal can render it in a
      copyable ``<code>`` block, and the equivalent HandBrake steps
      land in ``metadata.error_handbrake``.
    * Anything else (corrupt HEIC, ffmpeg subprocess error, ...) —
      prefix with the exception class so the operator sees a
      concrete signal (``UnidentifiedImageError: cannot identify
      image file '/data/.../abc.heic'``) without leaking the full
      traceback.
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
            if isinstance(exc, UnsupportedVideoCodecError):
                _set_processing_error(
                    asset_id,
                    str(exc),
                    recipe=exc.recipe,
                    handbrake=exc.handbrake,
                )
            else:
                _set_processing_error(asset_id, f'{type(exc).__name__}: {exc}')
            _notify(asset_id)
        except Exception:
            logger.exception(
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

    The decompression-bomb cap and the low-RAM downscale both live in
    ``_guard_and_fit_for_board``, shared with the in-place JPEG/PNG
    downscale path.
    """
    with Image.open(input_path) as image:
        _guard_and_fit_for_board(image)
        # Bake the EXIF Orientation into the pixels before we drop the
        # metadata. WebP output carries no Orientation tag, so a
        # portrait HEIC / AVIF (Orientation 6/8 — e.g. a phone photo)
        # would otherwise be stored rotated 90° (issue 3232). See
        # ``_bake_exif_orientation`` for which decoders make this a
        # no-op versus load-bearing.
        _bake_exif_orientation(image)
        # ``convert('RGBA')`` is a no-op when the source is already
        # RGBA (e.g. an HEIC with alpha) and a colour-correct upcast
        # otherwise. The result is a new Image (its own pixel
        # buffer) that's safe to use after ``image`` closes — but
        # serialise inside the ``with`` so we never hold both the
        # source decoder state *and* the converted buffer at once.
        rgba = image.convert('RGBA')
        rgba.save(output_path, 'WEBP', lossless=True, method=6)


def _guard_and_fit_for_board(image: Image.Image) -> None:
    """Reject decompression bombs, then fit ``image`` to the board.

    Mutates ``image`` in place (via ``thumbnail``) and is shared by
    both image paths — the WebP conversion and the JPEG/PNG in-place
    downscale — so the bomb cap and the low-RAM budget can never drift
    apart between them.

    Bomb guard: an attacker can craft a tiny file (a few KB on disk)
    that decodes to billions of pixels and exhausts worker memory.
    Pillow ships ``MAX_IMAGE_PIXELS`` (default ~89 MP) which raises
    ``DecompressionBombError`` past 2x that threshold, but it warns
    only at the first level — and pillow-heif's own decoder can bypass
    the check entirely on certain HEIF/AVIF inputs. The explicit
    ``image.size`` cap rejects oversized inputs deterministically
    before any pixel buffer is allocated. ``image.size`` is read from
    the format header and doesn't trigger a decode, so the guard is
    cheap even on a malicious file. A 50 MP cap is well above any
    phone camera output but tiny compared to the ~10 GP payloads
    typical bomb fixtures advertise.

    Low-RAM fit: at full resolution a ~24 MP image drives peak RSS to
    ~770 MB (measured); a Pi 2/3 normalisation container is capped
    around 540 MiB, so an encode OOM-kills and can reboot the board.
    A 1080p board can't display more detail anyway, so this is
    lossless for signage. Unlike the video path (which *rejects*
    over-cap uploads — a 4K clip can't be re-encoded on-device), an
    image downscales cleanly. ``thumbnail`` shrinks in place,
    preserves aspect ratio, and for JPEG uses libjpeg draft decoding
    so even the decode of the oversized source stays cheap.
    """
    width, height = image.size
    if width * height > _MAX_IMAGE_PIXELS:
        raise ValueError(
            f'image dimensions {width}x{height} exceed cap '
            f'{_MAX_IMAGE_PIXELS} pixels — refusing to decode'
        )
    if not _exceeds_low_ram_pixel_cap(width, height):
        return
    # Floor (not round) both target dimensions so the box area stays
    # at or below the cap — ``thumbnail`` fits within the box, so the
    # result is guaranteed <= _LOW_RAM_MAX_PIXELS.
    scale = (_LOW_RAM_MAX_PIXELS / (width * height)) ** 0.5
    image.thumbnail(
        # ``max(1, …)`` guards a pathological aspect ratio (a crafted
        # <5 px tall image that still slips under the 50 MP bomb cap)
        # from flooring a side to 0, which ``thumbnail`` rejects.
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.Resampling.LANCZOS,
    )


def _bake_exif_orientation(image: Image.Image) -> None:
    """Rotate ``image``'s pixels per its EXIF Orientation, in place.

    Both output paths drop EXIF (WebP carries no Orientation tag, and
    the in-place downscale saves without one deliberately), so the
    rotation has to live in the pixels or a portrait phone photo would
    display sideways — issue 3232.

    Whether this is load-bearing depends on the decoder, which is
    worth stating because it is easy to test the wrong leg and
    conclude the fix works when it never ran:

      * **JPEG / AVIF** — Pillow does *not* auto-apply Orientation on
        load, so this call is what actually rotates. Verified for JPEG
        on the armhf and arm64 testbeds via display capture, and for
        AVIF locally (a 400x200 source tagged Orientation=6 comes back
        200x400 only after this call).
      * **TIFF** — Pillow's TIFF reader already applies Orientation
        while loading, so this is a defensive no-op there. Verified on
        the armhf and arm64 testbeds: a stored-landscape TIFF tagged
        Orientation=6 loads already transposed, and
        ``exif_transpose`` reports no change.
      * **HEIC / HEIF** — also a defensive no-op, for a different
        reason. Real camera HEICs encode rotation in the HEIF ``irot``
        box, not as EXIF Orientation, and pillow-heif applies ``irot``
        at decode *and* normalises the EXIF Orientation tag to 1. So
        the buffer is already upright and there is nothing left to
        transpose. Verified against two genuine iPhone 12 Pro files
        (``irot`` 90 and 270, stored 3024x4032) on both the pinned
        pillow-heif 1.4.0 and 1.5.0: Pillow reports the already-rotated
        portrait size, the tag reads 1, and ``exif_transpose`` is a
        no-op. Note the corollary — an EXIF Orientation *injected* into
        an HEIC is normalised away by pillow-heif, so a synthetic
        fixture built that way tests nothing. Use a real camera file.

    Must run *after* any low-RAM downscale so a constrained board
    rotates the already-shrunk buffer. ``in_place`` transposes the
    open image without allocating a second full pixel buffer.

    NOSONAR: ``in_place`` is valid from Pillow 9.5 (we pin 12.3.0 and
    mypy passes); SonarCloud's bundled Pillow stubs are stale and flag
    it as python:S930 — a false positive.
    """
    ImageOps.exif_transpose(image, in_place=True)  # NOSONAR


# EXIF Orientation values whose rotation swaps width and height (90°
# or 270°, with or without a mirror). 1-4 are identity/flips that keep
# the aspect the way it is stored.
_TRANSPOSING_ORIENTATIONS = frozenset({5, 6, 7, 8})


def _display_size(image: Image.Image) -> tuple[int, int]:
    """``image``'s dimensions *as the operator sees them*.

    A phone photo is usually stored landscape with EXIF Orientation=6,
    meaning "rotate 90° CW to display" — so a 6000x4000 stored buffer
    is a 4000x6000 portrait to the person who took it. Reporting the
    stored numbers back to them in ``metadata.original_resolution``
    (which exists precisely to answer "why is my photo softer than the
    file I uploaded?") would name an orientation they never saw.

    Header-only: both ``size`` and ``getexif()`` read the format
    header, so this triggers no pixel decode. A missing or unreadable
    Orientation tag falls through to the stored size, which is correct
    for every format that has no tag at all.
    """
    width, height = image.size
    try:
        orientation = image.getexif().get(0x0112)
    except (OSError, ValueError, TypeError, struct.error):
        # Malformed EXIF must never fail an upload; the stored size is
        # a safe answer and the resize itself still runs.
        return width, height
    if orientation in _TRANSPOSING_ORIENTATIONS:
        return height, width
    return width, height


def _downscale_image_in_place(input_path: str, output_path: str) -> None:
    """Resize an over-cap JPEG/PNG to the board's budget, same format.

    The counterpart to ``_convert_image_to_webp`` for the formats we
    deliberately don't transcode. JPEG and PNG are already
    viewer-friendly, so re-encoding them to lossless WebP would be
    actively harmful — a photographic JPEG balloons several times over
    as lossless WebP. Instead we keep the format (and therefore the
    asset's URI and extension) and only shed pixels.

    EXIF is deliberately **not** carried over to the output. The
    orientation is baked into the pixels first, so re-attaching the
    original EXIF block would leave an Orientation tag describing a
    rotation that has already been applied, and the viewer's
    ``QImageReader::setAutoTransform`` would rotate a second time —
    turning a correct portrait into a sideways one. Dropping EXIF
    keeps the viewer's auto-transform a no-op, which is the same
    contract the WebP path already relies on.

    Quality 90 with ``optimize`` is visually indistinguishable from
    the source at signage viewing distance while keeping the file
    small; the resize has already discarded far more information than
    the re-encode does. PNG stays lossless, so it only takes
    ``optimize``.

    **A resized PNG can be larger on disk than its source, and that is
    accepted deliberately.** Measured on the Pi 2 testbed: a flat
    3000x2400 RGBA PNG went 608 KB -> 3.52 MB (5.8x) because lossless
    resampling replaces large single-colour runs — which PNG's filters
    compress almost to nothing — with gradients that they cannot. A
    photographic PNG shrinks normally (9.28 MB -> 3.44 MB).

    A "keep whichever file is smaller" guard would be the obvious
    reflex and would be **wrong**: the objective here is the decode
    buffer, not the file. Keeping that 608 KB source means the viewer
    still allocates 3000x2400x4 = ~28 MB of ARGB at render time, which
    is the entire problem this function exists to solve; the resized
    3.52 MB file decodes to ~8 MB. Trading a few MB of SD card for tens
    of MB of RAM on a 512 MB board is the right way round. The growth
    is logged so it stays observable rather than surprising.
    """
    source_bytes = 0
    try:
        source_bytes = os.path.getsize(input_path)
    except OSError:
        pass
    with Image.open(input_path) as image:
        source_format = (image.format or '').upper()
        _guard_and_fit_for_board(image)
        _bake_exif_orientation(image)
        if source_format == 'PNG':
            # PNG keeps its mode (and any alpha channel) as-is.
            image.save(output_path, 'PNG', optimize=True)
            _log_if_output_grew(input_path, output_path, source_bytes)
            return
        # JPEG cannot hold an alpha channel: a source with one (rare
        # but legal for a CMYK/LA JPEG, and reachable if Pillow picked
        # a mode like ``P`` or ``LA``) has to be flattened or ``save``
        # raises. Convert only when needed so the common RGB path
        # allocates nothing extra.
        if image.mode not in ('RGB', 'L'):
            flattened = image.convert('RGB')
            flattened.save(output_path, 'JPEG', quality=90, optimize=True)
            _log_if_output_grew(input_path, output_path, source_bytes)
            return
        image.save(output_path, 'JPEG', quality=90, optimize=True)
        _log_if_output_grew(input_path, output_path, source_bytes)


def _log_if_output_grew(
    input_path: str, output_path: str, source_bytes: int
) -> None:
    """Log when a resize made the file bigger, so it stays observable.

    Expected for a flat/low-entropy PNG (see ``_downscale_image_in_place``)
    and accepted: the point is the decode buffer, not the file. Logged
    rather than silently tolerated so "my asset directory grew after
    upload" is answerable from the journal instead of a mystery.
    """
    if not source_bytes:
        return
    try:
        output_bytes = os.path.getsize(output_path)
    except OSError:
        return
    if output_bytes <= source_bytes:
        return
    logger.info(
        'normalize_image_asset: %s grew on resize, %d -> %d bytes '
        '(%.1fx) — expected for low-entropy PNG; the decode buffer is '
        'what shrank',
        input_path,
        source_bytes,
        output_bytes,
        output_bytes / source_bytes,
    )


def _run_image_normalisation(asset: Asset) -> None:
    asset_id = asset.asset_id
    src_uri = asset.uri or ''
    if not src_uri or not path.isfile(src_uri):
        # Upload bytes never landed (cleanup() raced the operator,
        # disk pressure, ...). Fail clean.
        raise FileNotFoundError(f'image source missing: {src_uri!r}')

    src_ext = _ext(src_uri)
    # Two distinct jobs land in this task. A format in
    # ``NORMALIZE_IMAGE_EXTS`` gets converted to WebP (and its URI
    # changes extension). A JPEG/PNG that is merely too large for a
    # low-RAM board gets resized in place, keeping its format and URI —
    # re-encoding a photographic JPEG as lossless WebP would inflate
    # it several times over, so "convert" is the wrong tool there.
    downscale_only = src_ext not in NORMALIZE_IMAGE_EXTS
    if downscale_only and not needs_low_ram_image_downscale(src_uri):
        # Defensive: caller routed something we neither convert nor
        # need to shrink. Treat as a no-op success.
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

    src_width = src_height = 0
    if downscale_only:
        # Same extension in and out, so the row's ``uri`` never moves
        # and the viewer needs no cache invalidation beyond the mtime
        # bump it already watches.
        final_uri = src_uri
        # Record the pre-resize dimensions for the metadata below.
        # Header-only read (``Image.open`` is lazy), and any failure
        # here is not worth failing the task over — the resize itself
        # will raise a real decode error a moment later if the file is
        # genuinely broken.
        try:
            with Image.open(src_uri) as probe:
                src_width, src_height = _display_size(probe)
        except (OSError, UnidentifiedImageError, ValueError):
            logger.warning(
                'normalize_image_asset: could not read dimensions of %s',
                src_uri,
            )
    else:
        base_no_ext = path.splitext(src_uri)[0]
        final_uri = f'{base_no_ext}.webp'
    # Stage to a sibling .tmp first so a crashed save doesn't leave a
    # half-written file behind for the viewer to choke on. cleanup()
    # already sweeps stale .tmp after 1h. For the in-place downscale
    # this staging step is what keeps the operation atomic: the
    # original stays readable until the replace lands, so a crash
    # mid-encode can never destroy the only copy of the upload.
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
        if downscale_only:
            _downscale_image_in_place(src_uri, staging)
        else:
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
            logger.exception(
                'normalize_image_asset: removing original %s failed',
                src_uri,
            )

    metadata = dict(asset.metadata or {})
    metadata['original_ext'] = src_ext
    # ``converted`` means "the bytes were re-encoded to a different
    # format"; it stays False for an in-place downscale so existing
    # consumers of the flag don't start seeing JPEGs reported as
    # converted. ``downscaled`` records the resize independently, and
    # carries the original dimensions so an operator asking "why is my
    # photo softer than the file I uploaded?" can see what happened.
    metadata['converted'] = not downscale_only
    if downscale_only:
        metadata['downscaled'] = True
        if src_width and src_height:
            metadata['original_resolution'] = f'{src_width}x{src_height}'
    metadata.pop('error_message', None)

    Asset.objects.filter(asset_id=asset_id).update(
        uri=final_uri,
        mimetype='image',
        is_processing=False,
        metadata=metadata,
    )

    _notify(asset_id)


# ---------------------------------------------------------------------------
# Video normalisation: ffprobe → metadata write + HW-decode codec gate
# ---------------------------------------------------------------------------


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
    # ffprobe reports a comma-joined synonym list in
    # ``format.format_name`` (e.g. ``mov,mp4,m4a,3gp,3g2,mj2`` for
    # the QuickTime family). The first token is ffprobe's canonical
    # name but it's not always the operator-friendly one — for the
    # QuickTime family it's ``mov``, so an ``.mp4`` upload would
    # otherwise surface as ``container=mov`` in the asset row.
    # Prefer the file extension's token when it appears anywhere in
    # the synonym list so the operator UI matches what the operator
    # uploaded; fall back to the first token when nothing matches
    # (extension-less URI, or genuinely exotic container).
    fmt = fmt_data.get('format_name') or ''
    fmt_tokens = [t.strip().lower() for t in fmt.split(',') if t.strip()]
    if fmt_tokens:
        ext_token = _ext(input_path).lstrip('.')
        container = ext_token if ext_token in fmt_tokens else fmt_tokens[0]
    else:
        container = _ext(input_path).lstrip('.') or 'unknown'
    video_codec = ((video or {}).get('codec_name') or 'unknown').lower()
    # Width × height for the metadata surface. ffprobe returns
    # ``width`` / ``height`` only for video streams, and only when
    # the demuxer could decide. A missing or unparseable value
    # collapses to None.
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


_VIDEO_METADATA_KEYS = (
    'container',
    'video_codec',
    'video_width',
    'video_height',
    'video_fps',
    'audio_codec',
)


# Per-board set of video codecs accepted at upload. This gate must
# stay in sync with what each board's player can actually keep up
# with: pi2/pi3 through GStreamer's V4L2 elements
# (``GstFbdevMediaPlayer`` — bcm2835 codec, H.264 only), every other
# board through the viewer's in-process QtMultimedia + libavcodec
# pipeline (``MPVMediaPlayer`` — a legacy name; there is no mpv
# binary).
#
# Read it as a per-board codec allowlist, not a playback certificate.
# The failure it exists to prevent is real — accept a codec the board
# cannot decode in real time and playback degrades silently at the
# viewer (drops / black screen) — but an entry certifies the *codec*.
# Resolution is bounded separately and by two independent rules:
# ``_LOW_RAM_MAX_PIXELS`` for any board under
# ``LOW_RAM_THRESHOLD_KB`` of RAM, and ``_SW_DECODE_MAX_PIXELS`` for a
# board whose throughput ceiling has been measured. A board in neither
# — a 2 GB+ board with no measured ceiling — takes its accepted codecs
# at any resolution.
#
# THREE ENTRIES BELOW ARE KNOWN NOT TO PLAY CLEANLY, and they are
# listed as open defects, not as measurement gaps — the measurements
# exist and say these fail:
#
#   * ``x86`` accepts ``hevc``, but HEVC through VAAPI black-screens on
#     x86 — QMediaPlayer emits FormatError and decodes 0 frames. That
#     is issue #3072, still open. The *download* path already steers
#     around it (see
#     test_download_youtube_asset_x86_prefers_h264_format); a direct
#     HEVC upload walks straight through this gate into the failure
#     the issue describes. Of the three this is the worst: x86 is the
#     tier we recommend, and the failure is silent.
#   * ``rockpi4`` accepts ``hevc``, measured dropping ~22 % of frames
#     at 1080p30 (docs/board-enablement.md).
#   * ``pi4-64`` accepts ``h264`` with no ceiling, so 4K H.264 passes
#     on a high-RAM Pi 4 although the Pi 4 decoder tops out at 1080p
#     H.264 and anything above it falls back to software.
#
# Each wants either the codec removed or a measured cap added. Both
# are behaviour changes that would start rejecting uploads operators
# make today, on boards in the field, so they are a product call and
# a separate change — not something to slip into a docs pass. Until
# then this comment is the honest statement of what the map is: an
# allowlist carrying three entries we know are wrong.
#
# NOT a hardware-decode certificate, despite the name — which is
# historical, from when it was one. Some entries are deliberately
# software-decoded where a measurement showed the CPU keeps up:
# ``pi5``'s h264 (Cortex-A76) and all of ``rk3566`` (Cortex-A55), both
# noted at their entries. Software decode runs out of headroom with
# pixel count in a way hardware decode does not, so such an entry also
# wants a measured ceiling in ``_SW_DECODE_MAX_PIXELS`` — ``rk3566``
# has one; ``pi5`` is still unmeasured and deliberately unlisted there,
# see that map's own note. Adding a codec here on the assumption the
# silicon decodes it is therefore not safe — check the entry's note.
#
# Empty / missing entry means "nothing is certified to play here", not
# "this board decodes nothing in hardware" — every video upload is
# rejected either way, but the distinction is the whole reason the
# operator message says the board could not be identified rather than
# "Supported: none." The catch-all ``arm64`` DEVICE_TYPE lands here
# when no board subtype resolved; such an SBC may well have a working
# v4l2_request decoder, we just have no measurement or model match to
# say which codecs, so we refuse rather than ship a clip that might
# SW-decode at play time.
_HW_DECODE_VIDEO_CODECS: dict[str, frozenset[str]] = {
    'pi2': frozenset({'h264'}),
    'pi3': frozenset({'h264'}),
    # pi3-64: 64-bit Qt6 image on the same VideoCore IV silicon as pi3 —
    # H.264-only HW decode (no HEVC). Decoded in-process by
    # AnthiasViewer's QtMultimedia pipeline (ffmpeg/libavcodec backend →
    # V4L2 bcm2835-codec), not the Qt5 GStreamer fbdev path.
    'pi3-64': frozenset({'h264'}),
    'pi4-64': frozenset({'h264', 'hevc'}),
    # hevc: hardware-decoded via v4l2-request (BCM2712 VideoCore VII).
    # h264: software-decoded — Cortex-A76 handles 1080p H.264 without
    # frame drops, and YouTube rarely serves HEVC so excluding h264
    # would block all YouTube downloads on Pi 5.
    'pi5': frozenset({'hevc', 'h264'}),
    'rockpi4': frozenset({'h264', 'hevc'}),
    # RK3566 (4x Cortex-A55 @ 1.8 GHz) — NanoPi R3S/R3S LTS, Radxa
    # Zero 3, Orange Pi 3B and friends. h264: software-decoded, same
    # reasoning as the pi5 entry above. Measured in the arm64 viewer
    # image on a NanoPi R3S LTS against 20 s noise-heavy 1080p30
    # clips: H.264 decodes at 2.56x real time, HEVC at only 1.15x —
    # too thin to survive the web UI running alongside, so HEVC stays
    # out. Decode throughput alone would not justify this: frames can
    # decode faster than real time while presentation lags behind on
    # the Qt6 scene graph. So it was also played end-to-end on the
    # board's own display (cage/wayland, 4K panel) — the viewer
    # container sat at 200-240 % of 400 % CPU at 1080p30 against
    # 100-130 % at 720p30, with successive screen captures differing,
    # i.e. the pipeline keeps advancing with headroom left. What is
    # still *not* measured is presented-fps: neither this stack nor
    # the Qt path exposes a frame-drop counter, so drops under heavier
    # real-world content remain an operator observation. Instrumenting
    # that is the open item; see docs/board-enablement.md. The VPU can't help: the image's libavcodec exposes only the
    # stateful *_v4l2m2m wrappers, and RK3566's rkvdec is a stateless
    # request-API decoder, the same mismatch documented for RK3399 in
    # docs/board-enablement.md.
    'rk3566': frozenset({'h264'}),
    'x86': frozenset({'h264', 'hevc'}),
}


# Measured resolution ceilings for software decode. Distinct from the
# low-RAM cap below, which is about *memory* — a 1 GB board OOMs
# allocating the decode pipeline. This one is about *CPU throughput*: a
# board with ample RAM can still be unable to decode 4K in real time.
#
# A board is listed only once someone has measured its ceiling on
# hardware, so absence means "no measurement", NOT "hardware-decodes
# everything it accepts". ``pi5`` is the standing example: it accepts
# h264 knowing it decodes in software (see its entry above), and is
# unlisted only because nobody has timed 4K H.264 on a Cortex-A76.
# Adding a board here needs a number off a real device — an invented
# ceiling would reject uploads that play fine.
_SW_DECODE_MAX_PIXELS: dict[str, int] = {
    # 2.56x real time at 1080p (see the rk3566 note above); 4K is ~4x
    # the pixel work, which lands under real time before the web UI
    # takes its share.
    'rk3566': 1920 * 1080,
}


# Pixel cap for low-RAM boards (< 1.5 GiB MemTotal — Pi 2/Pi 3 1GB,
# Pi 4 1GB, Rock Pi 4 1GB, generic-arm64 1GB SKUs). 1080p = 2 073 600
# pixels is the line where a 1 GB board can keep a QtWebEngine
# renderer resident *and* play video without OOM-thrashing. 4K = 8 294
# 400 pixels is 4× this and triggered an OOM-kill in on-device
# testing (Rock Pi 4 1GB, dmesg: ``global_oom`` on the docker
# container's bash process). Resolution above this cap is rejected
# at upload — the operator gets the same "Failed" pill + recipe UX
# the codec gate already uses.
_LOW_RAM_MAX_PIXELS = 1920 * 1080


def _exceeds_low_ram_pixel_cap(width: int | None, height: int | None) -> bool:
    """``True`` when this board is low-RAM and the asset exceeds 1080p.

    Shared by the video gate (which *rejects* over-cap uploads) and the
    image normaliser (which *downscales* to the cap). Returns ``False``
    when the host's MemTotal couldn't be measured (host_agent never ran,
    Redis down) — same "don't block on a measurement gap" principle as
    ``is_low_ram_device`` itself. Returns ``False`` when the dimensions
    are unknown (``None``): for video that upload already collapsed to
    ``video_codec='unknown'`` and the codec gate rejects it; image
    callers always pass a decoded ``Image.size`` so never hit this.
    """
    if not is_low_ram_device():
        return False
    if width is None or height is None or width <= 0 or height <= 0:
        return False
    return width * height > _LOW_RAM_MAX_PIXELS


def _pixel_cap_rejection(
    width: int | None, height: int | None, device_key: str
) -> str | None:
    """Operator-facing reason this resolution can't play here, or ``None``.

    Two independent ceilings, memory then throughput, both answered
    by the same "re-encode at 1080p" recipe. Returning the message
    (rather than a bool per cap) keeps the caller to one rejection
    branch while each cap still explains its own cause — an operator
    told "the board OOMs" would go buy RAM for a board that is
    actually just too slow.

    ``device_key`` is passed in rather than resolved here so the codec
    and resolution decisions cannot disagree about what board this is.
    On arm64 ``resolve_device_key`` reads the subtype from Redis, and a
    transient miss between two calls would accept a 4K H.264 upload on
    an RK3566: the codec check would see ``rk3566`` and allow h264
    while this one saw bare ``arm64`` and found no ceiling.
    """
    if _exceeds_low_ram_pixel_cap(width, height):
        return (
            f'Video resolution {width}x{height} exceeds the 1080p cap '
            'on this device. Boards with less than 1.5 GiB of RAM OOM '
            'when decoding above 1920x1080 alongside the web UI.'
        )
    cap = _SW_DECODE_MAX_PIXELS.get(device_key)
    if (
        cap is not None
        and width is not None
        and height is not None
        and width > 0
        and height > 0
        and width * height > cap
    ):
        return (
            f'Video resolution {width}x{height} exceeds the 1080p cap '
            'on this device. This board decodes video in software and '
            'only keeps up in real time at 1920x1080 or below.'
        )
    return None


def _hw_decoded_codecs(device_key: str) -> frozenset[str]:
    """Video codecs the board named by ``device_key`` accepts.

    Mostly the board's hardware-decode set — hence the name — but not
    exclusively: ``pi5`` and ``rk3566`` accept H.264 on software
    throughput. So this is the *accepted* set, not a set certified to
    decode in hardware, and not one certified to play either: three
    entries are known not to (x86 HEVC, Rock Pi 4 HEVC, Pi 4 4K
    H.264), listed as open defects at the map. A software-decoded
    entry also wants a resolution ceiling, which
    ``_pixel_cap_rejection`` applies from ``_SW_DECODE_MAX_PIXELS`` —
    for the boards measured so far, which is ``rk3566`` and not yet
    ``pi5``.

    Callers resolve the key with
    ``anthias_common.board.resolve_device_key`` — so a Rock Pi 4 running
    the catch-all ``arm64`` image still picks up its ``{h264, hevc}``
    set once ``anthias_host_agent`` publishes
    ``host:board_subtype=rockpi4``. An unknown / unrecognised key
    returns the empty set so every video gets rejected. Taking the key
    as an argument (rather than resolving it here) is what lets one
    gate run every check against a single snapshot of the board
    identity; see ``_pixel_cap_rejection``.
    """
    return _HW_DECODE_VIDEO_CODECS.get(device_key, frozenset())


# Preferred yt-dlp ``vcodec`` sort key per board. Distinct from the
# accepted-codec gate above: Pi 5 accepts H.264 via software decode
# (Cortex-A76 handles 1080p without frame drops) but HEVC is still the
# hardware path, so downloads should bias toward it when available.
# Boards not listed here default to ``h264`` — it is more widely
# available on YouTube and is their primary hardware decode path.
_PREFERRED_DOWNLOAD_VCODEC: dict[str, str] = {
    'pi5': 'hevc',
}


def preferred_download_vcodec() -> str:
    """yt-dlp ``vcodec`` sort preference for the current board.

    Returns the codec string to place first in ``format_sort`` so
    yt-dlp biases downloads toward the board's best playback path.
    Falls back to ``'h264'`` for unknown boards and for every board
    whose primary path is H.264 — hardware on most of them, measured
    software throughput on ``pi5`` and ``rk3566``. Either way H.264 is
    what the board plays best, which is all this sort key decides.
    """
    return _PREFERRED_DOWNLOAD_VCODEC.get(resolve_device_key(), 'h264')


def _ffmpeg_reencode_recipe(
    supported: frozenset[str],
    source_filename: str = '',
    cap_to_1080p: bool = False,
) -> str:
    """Return an ``ffmpeg`` command line the operator can run on
    their workstation to transcode an unsupported upload into a
    codec (and, optionally, resolution) this board accepts.

    Prefers libx264 when H.264 is in the board's supported set —
    libx264 is roughly 5-10× faster than libx265 at comparable
    quality, which matters when the operator is doing the encode by
    hand. Falls back to libx265 + ``-tag:v hvc1`` for HEVC-only boards.
    Returns an empty string when the board has no accepted set at all
    — there's nothing the operator can transcode to that would land in
    a supported pipe. ("Accepted", not "hardware-decoded": ``pi5`` and
    ``rk3566`` accept H.264 on measured software throughput.)

    ``source_filename``, when supplied, substitutes the bare upload
    filename (no path) for the ``INPUT`` placeholder and reuses its
    stem for ``OUTPUT.mp4`` so the operator can copy the recipe
    verbatim into their terminal without hand-editing it.

    ``cap_to_1080p`` injects ``-vf scale=1920:1080:force_original_aspect_ratio=decrease``
    so the recipe also downscales an over-resolution source onto the
    1920×1080 envelope. ``force_original_aspect_ratio=decrease``
    means the output fits *inside* 1920×1080 (no padding, no
    stretch) — a 4K 16:9 source becomes exactly 1920×1080, a 4K 21:9
    ultrawide lands at 1920×823, a portrait 1080×1920 lands at
    608×1080 (height-bound). Set by either resolution ceiling — the
    low-RAM memory cap or the software-decode throughput cap; omitted
    when neither applies, so we don't suggest a needless re-encode when
    an HD codec swap is all that's wanted.
    """
    scale_clause = (
        '-vf scale=1920:1080:force_original_aspect_ratio=decrease '
        if cap_to_1080p
        else ''
    )
    if 'h264' in supported:
        template = (
            'ffmpeg -i {input} '
            + scale_clause
            + '-c:v libx264 -preset medium -crf 23 '
            '-c:a aac -b:a 192k -movflags +faststart {output}'
        )
        target_suffix = 'h264'
    elif 'hevc' in supported:
        template = (
            'ffmpeg -i {input} '
            + scale_clause
            + '-c:v libx265 -preset medium -crf 28 '
            '-tag:v hvc1 -c:a aac -b:a 192k -movflags +faststart '
            '{output}'
        )
        target_suffix = 'hevc'
    else:
        return ''
    if source_filename:
        # ``shlex.quote`` produces a safe shell-quoted form for any
        # filename — single quotes, spaces, ``;``, ``$()``, etc. all
        # land inside literal quoting that the operator can paste
        # without re-editing. The output filename carries the
        # target-codec suffix (``sample.h264.mp4`` / ``sample.hevc.mp4``)
        # so a recipe whose input shares the output stem doesn't ask
        # the operator to overwrite their source.
        in_quoted = shlex.quote(source_filename)
        stem, _ = path.splitext(source_filename)
        out_quoted = shlex.quote(f'{stem}.{target_suffix}.mp4')
    else:
        in_quoted = 'INPUT'
        out_quoted = f'OUTPUT.{target_suffix}.mp4'
    return template.format(input=in_quoted, output=out_quoted)


# Anchor for the GUI alternative offered alongside the ffmpeg recipe.
# HandBrake is a free, open-source, cross-platform (Windows / macOS /
# Linux) point-and-click transcoder — the path for operators who'd
# rather not touch a terminal.
HANDBRAKE_URL = 'https://handbrake.fr/'


def _handbrake_steps(supported: frozenset[str]) -> list[str]:
    """Return a decision-free, point-and-click HandBrake walkthrough —
    for operators who'd rather not paste a command into a terminal.

    Built around HandBrake's stock ``Fast 1080p30`` preset (General
    category): one click produces an H.264 MP4 capped at 1920x1080,
    which every board the viewer supports plays and which is the
    standard envelope for digital signage. That single preset also
    satisfies the low-RAM 1080p ceiling, so — unlike the ffmpeg recipe
    — there's no separate downscale step to spell out.

    The only board-specific tweak is the encoder: an HEVC-only board
    (Pi 5 before the H.264 software-decode fallback was added) can't
    use the preset's default H.264, so the operator flips ``Video
    Encoder`` to ``H.265 (x265)`` on the Video tab. HandBrake ships no
    H.265-at-1080p MP4 preset, so the encoder swap is the cleanest
    route to a 1080p HEVC MP4.

    Returns an empty list when the board has no accepted set at all —
    there's nothing to transcode to, exactly as the recipe returns an
    empty string. Step text embeds the download URL verbatim so the
    list stands alone when surfaced as plain text via the v2 API.
    """
    if 'h264' in supported:
        # H.264 board (Pi 2/3/4, Pi 5, x86, ...): the stock preset
        # already outputs an accepted codec — no encoder change needed.
        prefers_h264 = True
    elif 'hevc' in supported:
        prefers_h264 = False
    else:
        return []
    steps = [
        (
            f'Download and install HandBrake — it is free — from '
            f'{HANDBRAKE_URL} (Windows, macOS, and Linux).'
        ),
        (
            'Open HandBrake. When it asks for a source, pick your video '
            'file (or drag the file onto the window).'
        ),
        (
            'In the Presets panel on the right, choose "Fast 1080p30" '
            '(under the "General" group). This makes a 1080p MP4 — the '
            'format this screen plays.'
        ),
    ]
    if not prefers_h264:
        # Pi 5 and friends reject H.264; nudge the encoder to HEVC.
        steps.append(
            'Click the "Video" tab and change "Video Encoder" to '
            '"H.265 (x265)" — this screen needs HEVC rather than the '
            "preset's default H.264."
        )
    steps.extend(
        [
            (
                'Click "Browse" next to "Save As" at the bottom and pick '
                'where to save the converted file.'
            ),
            'Click the green "Start Encode" button at the top.',
            'When it finishes, upload the new MP4 to Anthias as a new asset.',
        ]
    )
    return steps


class UnsupportedVideoCodecError(Exception):
    """Raised by ``_run_video_normalisation`` when a video upload is
    outside this board's accepted set.

    Not "cannot be hardware-decoded": ``pi5`` and ``rk3566`` accept
    H.264 on measured software throughput, and the same exception
    carries the resolution rejections — an accepted codec above the
    board's pixel cap (``rk3566`` H.264 over 1080p) fails here too.

    Carries the suggested ``recipe`` (an ``ffmpeg`` command the
    operator can run to fix the upload) plus ``handbrake`` (the
    equivalent point-and-click HandBrake steps) as attributes so
    ``_NormalizeAssetTask.on_failure`` can persist them alongside the
    human-readable message — the UI surfaces all three in different
    spots (message inline, recipe in a copyable ``<code>`` block, the
    HandBrake steps as a numbered list for terminal-shy operators).
    """

    def __init__(
        self,
        message: str,
        recipe: str = '',
        handbrake: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.recipe = recipe
        self.handbrake = handbrake or []


def _run_video_normalisation(asset: Asset) -> None:
    """Probe the upload, record what ffprobe finds in ``metadata``,
    and reject the asset if its codec is outside this board's accepted
    set.

    "Accepted" is not the same as "plays cleanly": the map keeps
    three entries that are documented as failing or dropping frames
    (x86 HEVC, Rock Pi 4 HEVC, 4K H.264 on ``pi4-64``) because
    removing them would reject uploads that existing fleets already
    run. See ``_HW_DECODE_VIDEO_CODECS`` for the per-entry evidence.

    The file is never rewritten. Anthias does not re-encode video
    on-device — most boards the viewer supports hardware-decode their
    accepted codec set (H.264 + HEVC on most boards; HEVC only on
    Pi 5; H.264 only on Pi 2 / Pi 3), while ``pi5``'s H.264 and
    ``rk3566`` are accepted on measured software throughput — and the
    on-device libx265 / libx264 transcode path tried in earlier
    revisions wedged a Pi 4's celery worker for 99 minutes on a
    single 4K60 H.264 → HEVC pass before zombieing.

    Uploading a codec outside the board's accepted set is rejected —
    no playback path on this board has been shown to keep up with it,
    so shipping it would mean drops or a black screen at the viewer
    with nothing to warn the operator first. (Not "outside the
    hardware set": ``pi5`` and ``rk3566`` accept H.264 on measured
    software throughput.) The metadata fields written before the
    rejection let the operator see what they uploaded (codec / dims /
    fps) alongside the error message.
    """
    asset_id = asset.asset_id
    src_uri = asset.uri or ''
    if not src_uri or not path.isfile(src_uri):
        raise FileNotFoundError(f'video source missing: {src_uri!r}')

    summary = _ffprobe_summary(src_uri)

    metadata = dict(asset.metadata or {})
    metadata.pop('error_message', None)
    for key in _VIDEO_METADATA_KEYS:
        value = summary.get(key)
        if value is not None:
            metadata[key] = value

    update_dict: dict[str, Any] = {
        'mimetype': 'video',
        'metadata': metadata,
    }
    duration_seconds = summary.get('duration_seconds')
    if isinstance(duration_seconds, int) and duration_seconds > 0:
        update_dict['duration'] = duration_seconds

    src_codec = (summary.get('video_codec') or '').lower()
    # One snapshot of the board identity for the whole gate. On arm64
    # this is a Redis read, so resolving per-check would let the codec
    # and resolution decisions land on different boards.
    device_key = resolve_device_key()
    supported = _hw_decoded_codecs(device_key)
    video_width = summary.get('video_width')
    video_height = summary.get('video_height')
    # ``upload_name`` is stashed by the dashboard / API at upload
    # time — the on-disk file gets renamed to ``<uuid>.<ext>`` but
    # the recipe wants a name the operator can paste straight into
    # their workstation terminal. YouTube / pre-rebrand rows that
    # don't carry the field fall through to a stable
    # ``upload<ext>`` placeholder so the recipe still teaches the
    # operator the input extension. Resolved once so both the codec-
    # and the resolution-rejection paths share the same recipe stem.
    upload_name = metadata.get('upload_name') or (
        f'upload{path.splitext(src_uri)[1]}'
    )

    if src_codec in supported:
        cap_message = _pixel_cap_rejection(
            video_width, video_height, device_key
        )
        if cap_message is not None:
            # Codec is fine but the resolution is past what this board
            # can play — either it OOMs allocating the decode pipeline
            # (on-device: Rock Pi 4 1GB, 4K HEVC, kernel ``global_oom``
            # restart-looping the viewer container) or it can't decode
            # that many pixels in real time in software. Reject at
            # upload with a downscale recipe so the operator sees a
            # clear failure and a copy-pasteable fix instead of a
            # device stuck in an OOM cycle or dropping most frames.
            Asset.objects.filter(asset_id=asset_id).update(**update_dict)
            recipe = _ffmpeg_reencode_recipe(
                supported, upload_name, cap_to_1080p=True
            )
            handbrake = _handbrake_steps(supported)
            raise UnsupportedVideoCodecError(
                cap_message, recipe=recipe, handbrake=handbrake
            )
        update_dict['is_processing'] = False
        Asset.objects.filter(asset_id=asset_id).update(**update_dict)
        _notify(asset_id)
        return

    # Codec is outside the board's accepted set (or ffprobe couldn't
    # read it). Commit the metadata we *did* gather so the operator's
    # asset-list row carries the rejected codec / dims / fps, then
    # raise so ``_NormalizeAssetTask.on_failure`` fills in
    # ``error_message`` and clears ``is_processing``.
    Asset.objects.filter(asset_id=asset_id).update(**update_dict)
    display_codec = (
        src_codec if src_codec and src_codec != 'unknown' else 'unknown'
    )
    # If the upload would *also* fail a resolution ceiling, fold the
    # downscale into the codec recipe so the operator doesn't have to
    # re-upload twice (once for the codec swap, once for the resolution
    # shrink). The message remains codec-focused because the codec is
    # the strictly stronger rejection.
    #
    # Both ceilings count, not just the low-RAM one: the codec we are
    # steering the operator towards is by definition in ``supported``,
    # and on a board with a software-decode ceiling that target codec is
    # the software-decoded one. A 4K HEVC upload to an RK3566 would
    # otherwise get a 4K H.264 recipe, and the re-upload would come
    # straight back out of the throughput cap above.
    cap = (
        _pixel_cap_rejection(video_width, video_height, device_key) is not None
    )
    recipe = _ffmpeg_reencode_recipe(supported, upload_name, cap_to_1080p=cap)
    handbrake = _handbrake_steps(supported)
    if supported:
        supported_str = ', '.join(sorted(supported))
        message = (
            f'Video codec {display_codec!r} is not accepted on this '
            f'device. Accepted: {supported_str}.'
        )
    else:
        # Empty ``supported`` means no allowlist was found for this
        # key, which happens three ways: the catch-all ``arm64`` with
        # no subtype resolved; ``arm64`` with a subtype the map does
        # not know yet (host_agent ahead of the server mid-rollout);
        # and an unset or unrecognised DEVICE_TYPE, which
        # ``resolve_device_key`` passes through to a key the map has
        # no entry for. The first two are the same deployment and get
        # the same advice, which is why the branch below keys on the
        # environment DEVICE_TYPE rather than on the resolved key —
        # only that deployment has a host agent worth checking. Either
        # way say *why* rather than the misleading "Supported: none.",
        # which reads like the board decodes nothing at all.
        #
        # The advice is deliberately not "re-flash with the
        # board-specific image": there is no such image — every SBC
        # runs the generic arm64 build, including the Rock Pi 4 balena
        # fleet (see docs/board-enablement.md).
        #
        # It is also deliberately per-deployment rather than a bare
        # "check the host agent". The two installs fail here for
        # different reasons and only one of them is actionable:
        #
        # * compose / bare metal — anthias_host_agent publishes
        #   ``host:board_subtype`` to Redis. A stopped agent OR an
        #   unreachable Redis both land here, so the message names both
        #   rather than treating a running agent as proof the board is
        #   unprofiled.
        # * balena — ships no host_agent at all, and the in-container
        #   device-tree fallback reads nothing because Docker masks
        #   ``/sys/firmware`` in the unprivileged server container. So
        #   there is no subtype source on that fleet and no action the
        #   operator can take; saying so beats sending them after a
        #   service their device has never had.
        #
        # Read the *environment* DEVICE_TYPE, not the resolved key.
        # resolve_device_key() returns the subtype when Redis has one,
        # so a subtype the codec map doesn't know yet — mid-rollout,
        # host_agent ahead of the server — would look "unset or
        # unrecognised" here and hide the compose/balena diagnosis
        # from a deployment that is exactly the arm64 catch-all that
        # text is written for.
        is_generic_arm64 = (
            os.environ.get('DEVICE_TYPE', '').strip().lower()
            in ARM64_DEVICE_TYPES
        )
        message = (
            f'Video codec {display_codec!r} cannot be verified for '
            'playback on this device — Anthias could not identify '
            'this board, so it has no measured playback envelope for '
            'it and cannot certify that any codec plays here.'
            + (
                ' On a docker-compose install, check that '
                'anthias-host-agent and Redis are both running. On '
                'balena, the generic arm64 fleet (Rock Pi 4) ships no '
                'host agent and has no other subtype source, so those '
                'devices always land here; balena fleets built from an '
                'identified image (pi4-64, pi5) resolve their own key '
                'and never reach this message. Otherwise this board '
                'has not been profiled yet — please open an issue '
                'asking for it.'
                if is_generic_arm64
                else ' DEVICE_TYPE is unset or unrecognised on this '
                'install, so no board profile could be selected at all.'
            )
        )
    raise UnsupportedVideoCodecError(
        message, recipe=recipe, handbrake=handbrake
    )
