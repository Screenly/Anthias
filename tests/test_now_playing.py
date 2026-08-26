"""Tests for the now-playing fact the viewer publishes.

The schedule table re-renders every 5s per open browser, so reading
this must never cost a viewer round trip, and writing it must never be
able to interrupt playback. Both directions are best-effort.
"""

from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
import redis

from anthias_common import now_playing


@pytest.fixture(autouse=True)
def _reset_warn_latch() -> Iterator[None]:
    """The warn-once latch is module state; leaking it between tests
    would make the first failure in a run behave differently from the
    rest."""
    now_playing.latch.reset()
    now_playing._believed = None
    now_playing._last_announced_at = None
    yield
    now_playing.latch.reset()
    now_playing._believed = None
    now_playing._last_announced_at = None


def _client(get_value: Any = None, previous: Any = None) -> Any:
    client = mock.MagicMock()
    client.get.return_value = get_value
    # SET ... GET returns the value the key held before the write.
    client.set.return_value = previous
    return client


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def test_publish_writes_the_asset_id_with_the_liveness_ttl() -> None:
    """The TTL says "the viewer spoke recently", not "this asset is
    this long" — durations run to a year, and a dead viewer must not
    keep claiming a row for the rest of one."""
    client = _client()
    now_playing.publish(client, 'abc123')
    client.set.assert_called_once_with(
        now_playing.NOW_PLAYING_KEY,
        'abc123',
        ex=now_playing.TTL_S,
        get=True,
    )


def test_publish_announces_a_change() -> None:
    """Open browsers are told immediately rather than finding out on
    their next 5s poll."""
    client = _client(previous='something-else')
    now_playing.publish(client, 'abc123')
    client.publish.assert_called_once_with(
        now_playing.NOW_PLAYING_CHANNEL, 'abc123'
    )


def test_publish_is_quiet_when_the_asset_has_not_changed() -> None:
    """A one-asset playlist rotates forever with no news. Every
    announcement costs every open browser a full table re-render, so
    repeats must not be announced — but the write still happens,
    because that is what refreshes the TTL."""
    client = _client(previous='abc123')
    now_playing.publish(client, 'abc123')
    client.set.assert_called_once()
    client.publish.assert_not_called()


def test_publish_rate_limits_the_announcement_not_the_key() -> None:
    """``duration`` may be 0 (v2 serializer: ``min_value=0``), so a
    playlist of zero-duration assets rotates as fast as the display
    loop turns, and each rotation is a genuine change the dedup can't
    absorb. Every announcement costs each open browser a full table
    render, so the nudge needs its own ceiling — but the SET must keep
    running, because it is what refreshes the liveness TTL."""
    client = _client(previous='something-else')

    for asset_id in ('a', 'b', 'c', 'd'):
        now_playing.publish(client, asset_id)

    assert client.set.call_count == 4
    assert client.publish.call_count == 1


def test_publish_announces_again_once_the_floor_has_passed() -> None:
    """The limit is a floor between nudges, not a budget — an ordinary
    rotation is far slower than this and must always announce."""
    client = _client(previous='something-else')

    now_playing.publish(client, 'a')
    assert now_playing._last_announced_at is not None
    now_playing._last_announced_at -= now_playing.MIN_ANNOUNCE_INTERVAL_S
    now_playing.publish(client, 'b')

    assert client.publish.call_count == 2


def test_publish_without_an_asset_id_clears_instead() -> None:
    client = _client()
    now_playing.publish(client, None)
    client.set.assert_not_called()
    client.delete.assert_called_once_with(now_playing.NOW_PLAYING_KEY)


def test_publish_swallows_redis_errors() -> None:
    """Best-effort: a Redis hiccup must not take the screen down."""
    client = _client()
    client.set.side_effect = redis.ConnectionError('boom')
    now_playing.publish(client, 'abc123')


# ---------------------------------------------------------------------------
# refresh — the liveness heartbeat
# ---------------------------------------------------------------------------


def test_refresh_does_nothing_before_this_process_has_published() -> None:
    """The heart of it: a viewer that restarts must not inherit its
    dead predecessor's claim and renew it every tick while its own
    screen is still showing the splash. A crash-looping viewer would
    renew it forever — the failure the TTL exists to end."""
    client = _client()
    now_playing.refresh(client)
    client.set.assert_not_called()
    client.expire.assert_not_called()


def test_refresh_reasserts_what_this_process_published() -> None:
    client = _client()
    now_playing.publish(client, 'abc123')
    client.reset_mock()

    now_playing.refresh(client)
    client.set.assert_called_once_with(
        now_playing.NOW_PLAYING_KEY, 'abc123', ex=now_playing.TTL_S
    )
    # Nothing changed, so the browsers have nothing to hear about.
    client.publish.assert_not_called()


def test_refresh_restores_the_fact_after_redis_loses_it() -> None:
    """SET, not EXPIRE: an unclean Redis restart or a flush would
    otherwise leave a pinned hour-long asset unhighlighted until it
    finally rotated."""
    client = _client()
    now_playing.publish(client, 'abc123')
    client.reset_mock()

    now_playing.refresh(client)
    args, kwargs = client.set.call_args
    assert args[1] == 'abc123'
    assert kwargs['ex'] == now_playing.TTL_S


def test_refresh_stops_after_clear() -> None:
    """clear() retires the fact for good, not for one tick."""
    client = _client()
    now_playing.publish(client, 'abc123')
    now_playing.clear(client)
    client.reset_mock()

    now_playing.refresh(client)
    client.set.assert_not_called()


def test_refresh_swallows_redis_errors() -> None:
    client = _client()
    now_playing.publish(client, 'abc123')
    client.set.side_effect = redis.ConnectionError('boom')
    now_playing.refresh(client)


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_announces_that_nothing_is_playing() -> None:
    client = _client()
    client.delete.return_value = 1
    now_playing.clear(client)
    client.publish.assert_called_once_with(now_playing.NOW_PLAYING_CHANNEL, '')


def test_clear_is_quiet_when_nothing_was_playing() -> None:
    """The viewer clears on every tick of an empty playlist, so an
    unconditional announcement would nudge every open browser into a
    table re-render several times a minute on an idle device."""
    client = _client()
    client.delete.return_value = 0
    now_playing.clear(client)
    client.publish.assert_not_called()


def test_clear_swallows_redis_errors() -> None:
    client = _client()
    client.delete.side_effect = redis.ConnectionError('boom')
    now_playing.clear(client)


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('stored', 'expected'),
    [
        ('abc123', 'abc123'),
        (b'abc123', 'abc123'),
        (None, None),
        ('', None),
    ],
)
def test_read_returns_the_published_id(
    stored: Any, expected: str | None
) -> None:
    assert now_playing.read(_client(stored)) == expected


def test_read_returns_none_when_redis_is_unreachable() -> None:
    """The table must still render when the viewer or Redis is down —
    it just shows no highlight."""
    client = _client()
    client.get.side_effect = redis.ConnectionError('boom')
    assert now_playing.read(client) is None


def test_the_warn_latch_re_arms_after_a_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A blip at container start must not silence a later, different
    fault for the life of the process — a WRONGTYPE from a key some
    other code wrote, or a decode failure, deserves its own warning."""
    client = _client()
    client.get.side_effect = redis.ConnectionError('boom')
    with caplog.at_level('DEBUG'):
        now_playing.read(client)  # warns
        now_playing.read(client)  # debug
        client.get.side_effect = None  # Redis comes back
        now_playing.read(client)  # re-arms the latch
        client.get.side_effect = ValueError('decode failed')
        now_playing.read(client)  # a new fault warns again
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 2


def test_read_warns_once_then_drops_to_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every table render lands here, per open browser. An unreachable
    Redis would otherwise emit ~12 WARNING lines a minute — the journal
    budget GH #3268 was about."""
    client = _client()
    client.get.side_effect = redis.ConnectionError('boom')
    with caplog.at_level('DEBUG'):
        for _ in range(5):
            now_playing.read(client)
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1
    assert len([r for r in caplog.records if r.levelname == 'DEBUG']) == 4
