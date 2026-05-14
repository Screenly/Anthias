"""Per-board playback envelope.

The asset processor renders every video upload into a single
"playback variant" sized to the board's hardware envelope (codec +
max resolution + max fps). The viewer never sees anything outside
that envelope, which gives us:

* one codec per rotation → one mpv hwdec path per board, no
  per-clip dispatch surprises;
* uniform output mode → cage's / `--vo=drm`'s output stays fixed
  for the rotation's lifetime, no mid-clip mode flips;
* deterministic playback — drop counts depend on the display, not
  on whichever happens-to-be-uploaded codec/resolution mix.

This module is the canonical source of truth for that envelope.
Three things consume it:

* `anthias_server.processing._run_video_normalisation` picks the
  target codec + scale + fps cap for the ffmpeg invocation;
* the new `regenerate_for_envelope_change` celery task in
  `anthias_server.celery_tasks` walks the catalog and re-renders
  variants whose recorded `metadata['envelope']` no longer matches;
* `anthias_viewer.media_player._pi_hwdec_for_uri` still ffprobes
  at launch time as a runtime safety net — but with every variant
  on disk inside the envelope, the dispatch resolves the same way
  every time.

V1 is board-driven (no display probing). A follow-up PR replaces
the body of `compute_envelope()` with a runtime resolver (probe
mpv hwdec list + /dev/video* + vainfo) returning the same
``PlaybackEnvelope`` type — call sites won't change.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from os import path
from typing import Any

logger = logging.getLogger(__name__)

# Cached envelope JSON lives next to ``anthias.conf`` so an
# operator with shell access can inspect / hand-edit it (same place
# they edit the rest of Anthias's persistent state).
_CACHE_FILENAME = 'playback-envelope.json'


@dataclass(frozen=True)
class PlaybackEnvelope:
    """The codec + dimensions + framerate ceiling for a board.

    Every playback variant on disk must match this envelope exactly
    on codec, and be ``<= max_width`` / ``<= max_height`` /
    ``<= max_fps`` on the rest. ``container_ext`` is always mp4 —
    keeping the container fixed simplifies the variant filename
    convention (`<id>.mp4`) and matches what every Anthias-supported
    player handles natively.
    """

    codec: str  # 'h264' or 'hevc'
    max_width: int
    max_height: int
    max_fps: int

    @property
    def container_ext(self) -> str:
        return 'mp4'

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlaybackEnvelope:
        """Reconstruct from a JSON-loaded dict.

        Raises ``ValueError`` if any required key is missing or the
        codec is outside the supported set. Cache corruption is
        handled at the call site by treating a load failure as
        "no cache" → trigger a fresh compute.
        """
        try:
            codec = str(data['codec']).lower()
            max_width = int(data['max_width'])
            max_height = int(data['max_height'])
            max_fps = int(data['max_fps'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'malformed envelope payload: {exc}') from exc
        if codec not in ('h264', 'hevc'):
            raise ValueError(f'unsupported envelope codec: {codec!r}')
        return cls(codec, max_width, max_height, max_fps)


# Board -> envelope. Keys must match the ``DEVICE_TYPE`` env var the
# image builder writes into every Anthias-managed container, NOT
# whatever ``get_device_type()`` infers from /proc/device-tree/model
# at runtime. Build-time and transcode-time decisions therefore
# always agree on a Balena / dev workflow that runs an amd64 build
# on x86 hardware while still claiming a Pi target.
#
# Pi 4 / Pi 5 / x86 collapse to one HEVC 4Kp60 envelope on purpose:
# fleet uniformity means a single upload produces bit-identical
# variants on all three boards (cross-device sha256 stays equal),
# and we never have to handle a per-clip codec switch in rotation.
# Pi 4's H.264 V3D M2M path technically reaches 1080p60 at HW, but
# its CPU-mediated copy is slower in practice than the dedicated
# HEVC block (see the drop-rate data in the PR description); the
# trade is a one-time libx265 re-encode at upload buying steady-
# state HW decode forever.
#
# Pi 2 / Pi 3 and arm64 land on H.264 1080p30: legacy Pi has no
# HEVC silicon at all (V3D-IV is H.264-only) and arm64's Rockchip
# / Cedrus / Amlogic decoders are not reachable through upstream
# mpv. Conservative SW fallback.
ENVELOPE_BY_DEVICE_TYPE: dict[str, PlaybackEnvelope] = {
    'pi2': PlaybackEnvelope('h264', 1920, 1080, 30),
    'pi3': PlaybackEnvelope('h264', 1920, 1080, 30),
    'pi4-64': PlaybackEnvelope('hevc', 3840, 2160, 60),
    'pi5': PlaybackEnvelope('hevc', 3840, 2160, 60),
    'x86': PlaybackEnvelope('hevc', 3840, 2160, 60),
    'arm64': PlaybackEnvelope('h264', 1920, 1080, 30),
    # ``generic-arm64`` is the legacy label some pre-rebrand images
    # carry; current builds report ``arm64`` (see
    # ``refactor: rename device_type generic-arm64 → arm64``). Same
    # conservative envelope — keep the entry so an unfinished
    # rolling upgrade doesn't fall through to ``_DEFAULT``.
    'generic-arm64': PlaybackEnvelope('h264', 1920, 1080, 30),
    # Rock Pi 4 (Radxa, RK3399 SoC). Resolved by the runtime SoC
    # probe via host_agent (``host:board_subtype`` in Redis) when
    # ``DEVICE_TYPE`` is ``arm64`` / legacy ``generic-arm64``. The
    # RK3399 has a stateless v4l2_request HEVC decoder (``rkvdec``)
    # AND a stateful Hantro H.264 block, but the Anthias arm64
    # image's stock ffmpeg + mpv (Debian's packages) don't include
    # ``--enable-v4l2-request`` and don't link against Rockchip's
    # MPP, so neither path is reachable today — this envelope is
    # deliberately conservative (H.264 1080p30, same as the
    # catch-all arm64 entry) until a follow-up enables one of those
    # paths in the viewer build. Listed as a distinct key anyway
    # so the host_agent's detection has a place to land + the
    # walker's ``metadata['envelope']`` field flips immediately
    # when a future image starts publishing HEVC for ``rockpi4``.
    'rockpi4': PlaybackEnvelope('h264', 1920, 1080, 30),
}

# Fallback when ``DEVICE_TYPE`` is unset (host dev shell,
# ``ENVIRONMENT=test``) or names a board we haven't profiled yet.
# H.264 1080p30 is the most-compatible choice — every player Anthias
# ships with handles it, and any board's CPU can software-decode it
# at real time.
_DEFAULT = PlaybackEnvelope('h264', 1920, 1080, 30)


def compute_envelope() -> PlaybackEnvelope:
    """Resolve the envelope for the current process.

    Reads ``DEVICE_TYPE`` from the environment (the image builder
    writes it in at build time) and returns the matching matrix
    entry. Unknown or empty values resolve to ``_DEFAULT``.

    Generic aarch64 SBCs (``arm64`` / legacy ``generic-arm64``) get
    upgraded via a Redis-published *board subtype* — the install
    script can't differentiate "Rock Pi 4 with HW HEVC via
    v4l2m2m" from "random Allwinner H6 board with no upstream HW
    path", but ``anthias_host_agent`` reads
    ``/proc/device-tree/model`` on the host (containers don't see
    that path by default and we don't want to mount it for one
    edge case) and writes the resolved subtype to Redis at
    ``host:board_subtype`` before flipping the
    ``host_agent_ready`` flag. When that key matches a matrix
    entry, it overrides the DEVICE_TYPE-derived key; otherwise
    the conservative arm64 envelope stands.
    """
    key = os.environ.get('DEVICE_TYPE', '').strip().lower()
    if key in _ARM64_KEYS:
        sub = _redis_board_subtype()
        if sub is not None and sub in ENVELOPE_BY_DEVICE_TYPE:
            key = sub
    return ENVELOPE_BY_DEVICE_TYPE.get(key, _DEFAULT)


# DEVICE_TYPE values that trigger the host_agent subtype lookup.
# ``arm64`` is the current name; ``generic-arm64`` covers pre-rename
# images that haven't been rebuilt yet.
_ARM64_KEYS = frozenset({'arm64', 'generic-arm64'})


def _redis_board_subtype() -> str | None:
    """Return the board subtype the host_agent published to Redis.

    ``anthias_host_agent.set_board_subtype`` runs on the host (not
    in a container) and writes one of the matrix keys (e.g.
    ``'rockpi4'``) to ``host:board_subtype`` based on
    ``/proc/device-tree/model``. We read it back here so the
    server's envelope reconciliation can use it without mounting
    host paths into the container.

    Returns ``None`` on any failure: Redis unreachable (first boot
    before redis container is up), key missing (host_agent hasn't
    run yet, or the SBC didn't match any known signature), or any
    decode error. The caller falls back to the DEVICE_TYPE-derived
    matrix entry.
    """
    try:
        from anthias_common.utils import connect_to_redis

        r = connect_to_redis()
        value = r.get('host:board_subtype')
    except Exception:
        return None
    # ``redis-py``'s ``.get`` is typed as ``Awaitable | str | bytes |
    # None`` because the same class drives sync + async clients.
    # We're on the sync path; narrow accordingly.
    if isinstance(value, bytes):
        try:
            decoded = value.decode('utf-8')
        except UnicodeDecodeError:
            return None
        return decoded.strip().lower() or None
    if isinstance(value, str):
        return value.strip().lower() or None
    return None


def _cache_path() -> str:
    """Absolute location of the persisted envelope JSON.

    Lives alongside ``anthias.conf`` in the user's config dir (the
    canonical Anthias persistent-state directory; an operator with
    shell access can inspect / hand-edit). Resolved via the
    ``AnthiasSettings`` singleton so we pick up the same ``$HOME``
    every other persistent-state path uses.
    """
    from anthias_server.settings import settings

    return path.join(settings.get_configdir(), _CACHE_FILENAME)


def load_cached() -> PlaybackEnvelope | None:
    """Read the envelope from disk.

    Returns ``None`` when:

    * the cache file doesn't exist (first start ever),
    * the JSON fails to parse, OR
    * the payload doesn't validate via ``PlaybackEnvelope.from_dict``.

    Cache corruption is treated as "no cache" by design: the caller
    will compute fresh and overwrite, so a hand-edit that breaks the
    file self-heals on next start. The corrupting state never
    propagates into the walker.
    """
    cache_path = _cache_path()
    try:
        with open(cache_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            'playback envelope cache at %s is unreadable (%s); '
            'treating as missing — fresh compute will overwrite',
            cache_path,
            exc,
        )
        return None
    try:
        return PlaybackEnvelope.from_dict(data)
    except ValueError as exc:
        logger.warning(
            'playback envelope cache at %s contains invalid payload '
            '(%s); treating as missing — fresh compute will overwrite',
            cache_path,
            exc,
        )
        return None


def save_cached(envelope: PlaybackEnvelope) -> None:
    """Persist the envelope to disk atomically.

    Writes via the standard temp-file-then-rename idiom so a crash
    mid-write never leaves a half-written JSON file the next
    ``load_cached`` would have to discard. ``os.replace`` is atomic
    on every filesystem Anthias supports.
    """
    cache_path = _cache_path()
    tmp_path = f'{cache_path}.tmp'
    payload = json.dumps(envelope.as_dict(), indent=2, sort_keys=True)
    with open(tmp_path, 'w') as f:
        f.write(payload)
        f.write('\n')
    os.replace(tmp_path, cache_path)
