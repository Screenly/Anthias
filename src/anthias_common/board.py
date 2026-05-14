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
