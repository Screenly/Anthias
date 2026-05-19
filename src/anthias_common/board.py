"""Runtime board identification.

``bin/install.sh`` writes a coarse ``DEVICE_TYPE`` env var on every
device (``pi4-64``, ``pi5``, ``x86``, or the catch-all ``arm64`` /
``generic-arm64`` for every aarch64 SBC it doesn't recognise as a
Pi). For SoCs whose silicon offers HW decode that the catch-all
image can address (Rock Pi 4 → RK3399 via v4l2_request), the
``anthias_host_agent`` process publishes a more specific subtype to
Redis at ``host:board_subtype`` (e.g. ``'rockpi4'``) by reading
``/proc/device-tree/model`` on the host.

Both the server's asset processor (deciding whether to accept a
codec) and the viewer's hwdec dispatch (deciding which mpv
``--hwdec=`` value to ask for) need the same upgraded key. This
module owns the resolution so the two sides don't drift.
"""

from __future__ import annotations

import os

from anthias_common.utils import connect_to_redis

# DEVICE_TYPE values that trigger the host_agent subtype lookup.
# ``generic-arm64`` is the legacy label that pre-rename arm64 images
# still carry; both share the same Rock-Pi-via-host_agent upgrade
# path.
ARM64_DEVICE_TYPES = frozenset({'arm64', 'generic-arm64'})


def get_board_subtype() -> str | None:
    """Return the host_agent-published board subtype, or ``None``.

    Any failure (Redis down, key missing, decode error) returns
    ``None`` so the caller falls back to the raw DEVICE_TYPE.
    """
    try:
        r = connect_to_redis()
        value = r.get('host:board_subtype')
    except Exception:
        return None
    if isinstance(value, bytes):
        try:
            decoded = value.decode('utf-8')
        except UnicodeDecodeError:
            return None
        return decoded.strip().lower() or None
    if isinstance(value, str):
        return value.strip().lower() or None
    return None


def get_total_mem_kb() -> int | None:
    """Return the host's MemTotal in kibibytes from Redis, or ``None``.

    ``anthias_host_agent`` publishes ``host:total_mem_kb`` at startup
    by reading ``/proc/meminfo`` on the host. The server reads this
    instead of opening ``/proc/meminfo`` itself so the value is
    consistent across server and viewer (both observe whatever the
    host_agent measured).

    ``None`` means "unknown" — host_agent never ran, redis is down,
    or the key was written empty because /proc/meminfo couldn't be
    read. Callers treat unknown as "don't restrict" rather than
    locking the operator out from uploads on a measurement gap.
    """
    try:
        r = connect_to_redis()
        value = r.get('host:total_mem_kb')
    except Exception:
        return None
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8')
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


# Boards with less than this much MemTotal can't keep two QtWebEngine
# renderers resident *and* play 1080p+ video without OOM-thrashing
# through swap (measured 1 GB Rock Pi 4: ~440 MB idle viewer RSS, OOM
# loop on 4K HEVC load). 1.5 GiB is the cleanest cut between 1 GB and
# 2 GB SKUs in the supported fleet — Pi 2/Pi 3 1GB, Pi 4 1GB, Rock Pi
# 4 1GB fall below; every 2 GB+ SKU sits above. Tests can compare
# against this directly rather than re-hardcoding the threshold.
LOW_RAM_THRESHOLD_KB = 1_572_864  # 1.5 GiB in kibibytes


def is_low_ram_device() -> bool:
    """``True`` when the host has less than ``LOW_RAM_THRESHOLD_KB``.

    Returns ``False`` when total RAM is unknown — uploading is the
    operator action we'd rather over-accept than block on a missing
    measurement. The codec gate keeps its existing per-codec
    rejection regardless of this flag, so an "unknown RAM" device
    still gets the codec safety net.
    """
    total = get_total_mem_kb()
    if total is None:
        return False
    return total < LOW_RAM_THRESHOLD_KB


def resolve_device_key() -> str:
    """Return ``DEVICE_TYPE`` upgraded via the host_agent's published
    board subtype when applicable.

    Lowercased and whitespace-stripped for stable dict lookup. An
    unset or unrecognised ``DEVICE_TYPE`` returns an empty string —
    callers treat that as "no HW path known on this device" rather
    than guessing.
    """
    key = os.environ.get('DEVICE_TYPE', '').strip().lower()
    if key in ARM64_DEVICE_TYPES:
        sub = get_board_subtype()
        if sub:
            return sub
    return key
