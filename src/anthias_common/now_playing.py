"""The asset the viewer currently has on screen.

anthias-viewer writes this on every rotation; anthias-server reads it
when it renders the schedule table, so the operator can see which row
is live. Play order can't answer that once shuffle is on (#3177).

A published fact rather than a request-reply round trip: the table
re-renders every 5s per open browser, and asking the viewer each time
would put a blocking BLPOP on the render path and wake the display
loop for it. Same shape as ``cec:available`` and the SMART fact.

The TTL is a liveness signal, not a content one: a short window kept
alive by :func:`refresh` on a timer, like the display-resolution fact.
Deriving it from the asset's duration instead was tempting and wrong —
durations run to a year, so a viewer that died mid-rotation could have
gone on claiming a row for months, and a paused viewer would have
dropped the highlight off a picture that was still on screen.

What :func:`refresh` re-asserts is this process's own memory of what it
last put on screen, never whatever happens to be in Redis. Blind-
extending the key with EXPIRE looks equivalent and is not: a viewer
that restarts inherits its dead predecessor's claim and renews it on
every tick while its own screen is still showing the splash, and one
that crash-loops faster than the TTL renews it forever — the exact
failure the TTL exists to end. Re-asserting a remembered value also
restores the fact by itself if Redis is restarted or flushed under us,
which EXPIRE cannot do.

Every call is best-effort. The write sits in the display loop, where
an exception would take the screen down, and the read gates a page
render that must still work with Redis or the viewer down — it just
shows no highlight.

Callers pass their own client, as they do for the other facts in this
package (``smart``, ``storage_health``, ``undervoltage``): both the
viewer and the server already hold a long-lived one, and building a
fresh connection pool per table render would be waste.
"""

import logging
from time import monotonic
from typing import Any

from anthias_common.warn_once import WarnOnce

logger = logging.getLogger(__name__)

#: Redis key holding the asset_id the viewer is displaying.
NOW_PLAYING_KEY = 'viewer:now_playing_asset_id'

#: Pub/sub channel carrying the same value to anyone who wants it
#: pushed rather than polled — today the WebSocket consumer, which
#: nudges open browsers so the highlight arrives with the picture
#: rather than up to 5s later. Its own channel, not the viewer command
#: bus: the audience here is browsers, not the viewer.
NOW_PLAYING_CHANNEL = 'anthias.now_playing'

#: How long the fact outlives the last thing the viewer said. Same
#: 3-minute window as the display-resolution fact, and for the same
#: reason: it must survive an ordinary slow tick but not a dead viewer.
TTL_S = 180

#: Refresh cadence. Comfortably inside TTL_S so two missed ticks in a
#: row still don't expire a fact that is merely late.
REFRESH_INTERVAL_S = 60

#: Floor on how often a change is announced; the key write is never
#: skipped. ``duration`` may be 0 (v2 serializer: ``min_value=0``), so
#: rotation is not self-limiting, and every announcement costs each
#: open tab a full table render. The 5s poll used to be the ceiling on
#: that; this keeps one. A dropped nudge costs one poll of staleness.
MIN_ANNOUNCE_INTERVAL_S = 1.0

#: What THIS process last put on screen, or None if it hasn't put
#: anything there yet. Only :func:`refresh` reads it; the module
#: docstring says why it exists rather than trusting the key.
_believed: str | None = None

#: When the last announcement went out, for MIN_ANNOUNCE_INTERVAL_S.
_last_announced_at: float | None = None

#: Warn-once latch for this module's Redis calls.
latch = WarnOnce(logger)


def _announce(client: Any, payload: str) -> None:
    """Nudge the browsers, at most once per MIN_ANNOUNCE_INTERVAL_S.

    The second gate after the callers' dedup, for a value that moves
    faster than a browser can usefully redraw. No catch-up: the 5s
    poll already carries whatever a dropped nudge would have.
    """
    global _last_announced_at
    now = monotonic()
    if (
        _last_announced_at is not None
        and now - _last_announced_at < MIN_ANNOUNCE_INTERVAL_S
    ):
        return
    client.publish(NOW_PLAYING_CHANNEL, payload)
    # After the publish, not before: a failed one is caught upstream,
    # and advancing the floor anyway would drop the next genuine
    # change too.
    _last_announced_at = now


def publish(client: Any, asset_id: str | None) -> None:
    """Record — and announce — the asset now on screen."""
    global _believed
    if not asset_id:
        clear(client)
        return
    try:
        # SET always runs: it is what refreshes the TTL. The
        # announcement is what gets deduped, because every open browser
        # turns one into a full table re-render, and a single-asset
        # playlist would otherwise pay that on every loop for no news.
        # ``get=True`` returns the previous value (Redis >= 6.2).
        previous = client.set(NOW_PLAYING_KEY, asset_id, ex=TTL_S, get=True)
        _believed = asset_id
        if previous != asset_id:
            _announce(client, asset_id)
        latch.worked('publish')
    except Exception as exc:
        latch.warn('publish', 'Could not publish the now-playing asset', exc)


def refresh(client: Any) -> None:
    """Re-assert what this process last put on screen.

    Called on a timer rather than per rotation, because a rotation can
    be an hour long. A no-op until this process has displayed
    something, so a restarted viewer cannot keep its predecessor's
    claim alive while its own screen is still on the splash, and
    :func:`clear` retires the fact for good rather than for one tick.

    Silent by design: it re-states a value the browsers already have.
    """
    if _believed is None:
        return
    try:
        client.set(NOW_PLAYING_KEY, _believed, ex=TTL_S)
        latch.worked('refresh')
    except Exception as exc:
        latch.warn('refresh', 'Could not refresh the now-playing asset', exc)


def clear(client: Any) -> None:
    """Record that nothing is on screen.

    Announced only when something actually stopped: the viewer clears
    on every tick of an empty playlist, and an unconditional
    announcement would nudge every open browser into a table re-render
    several times a minute on an idle device.
    """
    global _believed
    _believed = None
    try:
        if client.delete(NOW_PLAYING_KEY):
            _announce(client, '')
        latch.worked('clear')
    except Exception as exc:
        latch.warn('clear', 'Could not clear the now-playing asset', exc)


def read(client: Any) -> str | None:
    """The asset_id the viewer last reported, or ``None``.

    ``None`` covers every unknown: the viewer hasn't reported yet, the
    fact expired because the viewer stopped saying it, or Redis is
    unreachable.
    """
    try:
        raw = client.get(NOW_PLAYING_KEY)
        latch.worked('read')
    except Exception as exc:
        latch.warn('read', 'Could not read the now-playing asset', exc)
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    return str(raw)
