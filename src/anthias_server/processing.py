"""Asset normalisation pipeline.

Two Celery tasks that run on every fresh upload:

* ``normalize_image_asset`` — converts HEIC / HEIF / TIFF to lossless
  WebP via Pillow + pillow-heif. The Qt webview only ever needs to
  render formats it can already display.
* ``normalize_video_asset`` — probes the upload's container/codec with
  ffprobe and either passes it through (rename only) or transcodes to
  H.264 + AAC in MP4 with ffmpeg's ``-threads 2`` and a single -crf 23
  preset. The Pi's mpv/VLC pipeline only ever sees H.264/HEVC video
  this way.

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


# Container extensions whose H.264/HEVC payloads play directly in mpv
# / VLC on the Pi without remuxing. Anything outside this set falls
# through to a full transcode regardless of codec — a "passthrough"
# rename to .mkv that preserves a weird container would still need a
# downstream remux to land in MP4, and the viewer's media stack is
# happiest on .mp4. Keeping the list explicit also stops a typo'd
# extension from being silently retained.
_PASSTHROUGH_CONTAINERS = frozenset(
    {'mp4', 'm4v', 'mkv', 'mov', 'webm', 'ts', 'mpg', 'mpeg', 'flv', 'avi'}
)


# Video codecs ffmpeg labels these names that the viewer's mpv/VLC
# pipeline plays directly. Anything else (ProRes, MJPEG, VP9 at the
# wrong container, AV1 on hardware that can't decode it, etc.) → forced
# transcode to libx264 below.
_PASSTHROUGH_VIDEO_CODECS = frozenset({'h264', 'hevc'})

# Audio codecs the viewer can demux without a transcode. ``None`` is
# represented as the literal string ``'none'`` so a probe result with
# no audio stream still falls in the "passthrough OK" set.
_PASSTHROUGH_AUDIO_CODECS = frozenset(
    {'aac', 'mp3', 'opus', 'vorbis', 'ac3', 'none'}
)

# Image extensions we route through the conversion task. Anything not
# in this set is left as-is — the existing pipeline already handles
# JPEG/PNG/WebP/GIF/BMP via direct Qt webview rendering.
NORMALIZE_IMAGE_EXTS = frozenset({'.heic', '.heif', '.tif', '.tiff'})


def needs_image_normalisation(uri_or_filename: str) -> bool:
    """``True`` for HEIC/HEIF/TIFF uploads.

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
    """
    with Image.open(input_path) as image:
        # ``convert('RGBA')`` is a no-op when the source is already
        # RGBA (e.g. an HEIC with alpha) and a colour-correct upcast
        # otherwise. ``copy()`` so the underlying file handle can
        # close before we serialise out the WebP.
        rgba = image.convert('RGBA').copy()

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
        Asset.objects.filter(asset_id=asset_id).update(is_processing=False)
        _notify(asset_id)
        return

    base_no_ext = path.splitext(src_uri)[0]
    final_uri = f'{base_no_ext}.webp'
    # Stage to a sibling .tmp first so a crashed save doesn't leave a
    # half-written .webp behind for the viewer to choke on. cleanup()
    # already sweeps stale .tmp after 1h.
    staging = f'{final_uri}.tmp'

    try:
        _convert_image_to_webp(src_uri, staging)
    except UnidentifiedImageError as exc:
        # Pillow couldn't decode — almost always a corrupt upload.
        # Re-raise with a clearer name; on_failure formats the message.
        raise UnidentifiedImageError(
            f'could not decode image {src_uri!r}: {exc}'
        ) from exc

    # Atomic rename within the same dir — POSIX guarantees this is
    # observed as a single inode swap. os.replace overwrites an
    # existing .webp (e.g. a re-run of the task on the same asset),
    # which is the right semantics here.
    os.replace(staging, final_uri)

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


def _ffprobe_summary(input_path: str) -> dict[str, str]:
    """Reduce ffprobe's payload to the three dimensions we branch on.

    Returns a dict with ``container`` (lowercase ext, no dot),
    ``video_codec`` (or ``''`` if no video stream), and
    ``audio_codec`` (``'none'`` if no audio stream). Anything missing
    from the probe output is treated as 'unknown' so the caller can
    fall through to transcode.
    """
    try:
        probe = _ffprobe_streams(input_path)
    except (sh.TimeoutException, sh.ErrorReturnCode):
        return {
            'container': 'unknown',
            'video_codec': 'unknown',
            'audio_codec': 'unknown',
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
    fmt = (probe.get('format') or {}).get('format_name') or ''
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
    return {
        'container': container,
        'video_codec': video_codec,
        'audio_codec': audio_codec,
    }


def _video_can_passthrough(summary: dict[str, str]) -> bool:
    """``True`` if the file is in a format the viewer plays directly.

    The probe needs to answer "yes" to all three questions: is the
    container one we accept; is the video codec H.264/HEVC; is the
    audio codec one of the demuxer-compatible set (or absent). Any
    'unknown' answer (probe failed, exotic codec) triggers a
    transcode — better to spend the cycles than to let an
    unplayable file sit in the rotation.
    """
    if summary.get('container') not in _PASSTHROUGH_CONTAINERS:
        return False
    if summary.get('video_codec') not in _PASSTHROUGH_VIDEO_CODECS:
        return False
    if summary.get('audio_codec') not in _PASSTHROUGH_AUDIO_CODECS:
        return False
    return True


def _transcode_to_h264_mp4(input_path: str, output_path: str) -> None:
    """Run the canonical libx264 + AAC transcode.

    * ``-y`` and ``-nostdin`` keep ffmpeg non-interactive (it would
      otherwise prompt on overwrite or block waiting for input).
    * ``-threads 2`` caps CPU usage so the viewer keeps two cores
      free on Pi 4 / Pi 5; combined with the ``nice -n 19 ionice -c
      3`` wrapper on the celery worker this means a transcode
      effectively never disrupts active playback.
    * ``-preset medium -crf 23`` is libx264's well-known "transparent
      enough at moderate bitrate" default; not pushing for fast/slow
      keeps results stable.
    * ``-c:a aac -b:a 192k`` matches every Anthias-supplied default
      asset's audio profile.
    * ``-movflags +faststart`` shifts the moov atom to the front of
      the file so playback can begin before the file is fully buffered
      — relevant when the viewer is fed via an HTTP serve later.
    """
    sh.ffmpeg(
        '-y',
        '-nostdin',
        '-threads',
        '2',
        '-i',
        input_path,
        '-c:v',
        'libx264',
        '-preset',
        'medium',
        '-crf',
        '23',
        '-c:a',
        'aac',
        '-b:a',
        '192k',
        '-movflags',
        '+faststart',
        output_path,
        _timeout=NORMALIZE_VIDEO_TIME_LIMIT_S,
    )


def _resolve_duration_seconds(uri: str) -> int | None:
    """ffprobe-driven duration for the post-normalisation row.

    Mirrors ``probe_video_duration`` but inlined here so the
    normalisation task can update the row in a single write. Returns
    None if ffprobe is unavailable or probe failed; the caller skips
    duration-on-success in that case (matches existing behaviour).
    """
    delta = get_video_duration(uri)
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

    metadata = dict(asset.metadata or {})
    metadata['original_ext'] = src_ext
    metadata.pop('error_message', None)

    if _video_can_passthrough(summary):
        # No re-encode. Keep the file at its current uri; flip the
        # in-progress flag and write the duration if ffprobe could
        # answer for it.
        metadata['transcoded'] = False
        update: dict[str, Any] = {
            'is_processing': False,
            'metadata': metadata,
        }
        duration = _resolve_duration_seconds(src_uri)
        if duration is not None:
            update['duration'] = duration
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
    staging = f'{base_no_ext}.staging.mp4'
    # Edge case: source already lives at ``<base>.mp4`` (a non-h264
    # .mp4 fell through here). The staging name must NOT collide
    # with the source on rename — using a distinct suffix keeps the
    # input safe to read while ffmpeg writes to the staging file.
    if path.normpath(staging) == path.normpath(src_uri):
        staging = f'{base_no_ext}.staging.transcoded.mp4'

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
        _transcode_to_h264_mp4(src_uri, staging)
    except sh.TimeoutException as exc:
        # Time-limit overruns are surfaced as TimeoutException; let
        # on_failure land so is_processing clears.
        _drop_staging()
        raise RuntimeError(f'ffmpeg timed out for {src_uri!r}: {exc}') from exc
    except sh.ErrorReturnCode as exc:
        _drop_staging()
        raise RuntimeError(
            f'ffmpeg failed for {src_uri!r}: {exc.stderr!r}'
        ) from exc

    if not path.isfile(staging) or os.stat(staging).st_size == 0:
        # ffmpeg sometimes returns exit 0 but produces an empty file
        # (broken stream, silent codec mismatch). Reject the result
        # and clean up the empty file rather than promoting it.
        _drop_staging()
        raise RuntimeError(f'ffmpeg produced no output for {src_uri!r}')

    os.replace(staging, final_uri)

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
