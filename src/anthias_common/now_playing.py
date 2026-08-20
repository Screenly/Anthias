"""The asset the viewer currently has on screen.

anthias-viewer writes this on every rotation; anthias-server reads it
when it renders the schedule table, so the operator can see which row
is live. Play order can't answer that once shuffle is on (#3177).

A published fact rather than a request-reply round trip: the table
re-renders every 5s per open browser, and asking the viewer each time
would put a blocking BLPOP on the render path and wake the display
loop for it. Same shape as ``cec:available`` and the SMART fact.

The TTL is a liveness signal, not a content one: a short window kept
alive by :func:`refresh` on a timer, exactly like the display-resolution
and SMART facts. Deriving it from the asset's duration instead was
tempting and wrong — durations run to a year, so a viewer that died
mid-rotation could have gone on claiming a row for months, and a
paused viewer would have dropped the highlight off a picture that was
still on screen. Tying it to "the viewer said something recently"
makes both cases right without either knowing about the other.

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
from typing import Any

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

_warned: set[str] = set()


def _warn_once(key: str, message: str, exc: Exception) -> None:
    """Log at WARNING the first time, DEBUG thereafter.

    A local copy of the helper in :mod:`anthias_common.undervoltage`
    and :mod:`anthias_common.storage_health`, for the same reason they
    keep their own: sibling modules with no dependency between them.

    Redis being unreachable is a property of the device, not of one
    call, and every render of the schedule table lands here — 12 lines
    a minute per open browser. GH #3268 measured that class of
    repetition evicting crash diagnostics from the volatile journal
    inside a day.
    """
    if key in _warned:
        logger.debug('%s: %s', message, exc)
        return
    _warned.add(key)
    logger.warning('%s: %s', message, exc)


def publish(client: Any, asset_id: str | None) -> None:
    """Record — and announce — the asset now on screen."""
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
        if previous != asset_id:
            client.publish(NOW_PLAYING_CHANNEL, asset_id)
    except Exception as exc:
        _warn_once('publish', 'Could not publish the now-playing asset', exc)


def refresh(client: Any) -> None:
    """Keep the fact alive while the viewer still has that asset up.

    Called on a timer rather than per rotation, because a rotation can
    be an hour long. ``EXPIRE`` on a missing key is a no-op, so this
    can never resurrect a fact that :func:`clear` retired.
    """
    try:
        client.expire(NOW_PLAYING_KEY, TTL_S)
    except Exception as exc:
        _warn_once('refresh', 'Could not refresh the now-playing asset', exc)


def clear(client: Any) -> None:
    """Record that nothing is on screen.

    Announced only when something actually stopped: the viewer clears
    on every tick of an empty playlist, and an unconditional
    announcement would nudge every open browser into a table re-render
    several times a minute on an idle device.
    """
    try:
        if client.delete(NOW_PLAYING_KEY):
            client.publish(NOW_PLAYING_CHANNEL, '')
    except Exception as exc:
        _warn_once('clear', 'Could not clear the now-playing asset', exc)


def read(client: Any) -> str | None:
    """The asset_id the viewer last reported, or ``None``.

    ``None`` covers every unknown: the viewer hasn't reported yet, the
    fact expired because the viewer stopped saying it, or Redis is
    unreachable.
    """
    try:
        raw = client.get(NOW_PLAYING_KEY)
    except Exception as exc:
        _warn_once('read', 'Could not read the now-playing asset', exc)
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    return str(raw)
