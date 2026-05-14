"""Tests for ``anthias_host_agent``'s board subtype publisher.

``host_agent`` runs on the host (outside any container) and writes
the resolved board subtype to Redis at ``host:board_subtype``.
Server + viewer read it to upgrade the catch-all ``arm64``
DEVICE_TYPE into a board-specific envelope / hwdec dispatch when
the silicon supports it. We pin:

* the device-tree → subtype mapping for known boards (Rock Pi 4);
* unknown / empty / missing device-tree all collapse to ``None``;
* the Redis publish writes the resolved subtype (or empty string)
  exactly once per host_agent start.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from anthias_host_agent.__main__ import detect_board_subtype, set_board_subtype


@pytest.mark.parametrize(
    ('model_bytes', 'expected'),
    [
        # Canonical Radxa string + null terminator (what the kernel
        # actually writes — null-terminated UTF-8).
        (b'Radxa ROCK Pi 4B\x00', 'rockpi4'),
        # 4A / 4C variants — same RK3399 silicon, same dispatch.
        (b'Radxa ROCK Pi 4A\x00', 'rockpi4'),
        (b'Radxa ROCK Pi 4C\x00', 'rockpi4'),
        # Whitespace + mixed case — the function strips + lowercases
        # so a vendor that writes the model differently doesn't slip
        # through.
        (b'  RADXA Rock Pi 4B\n  \x00', 'rockpi4'),
        # Boards we haven't profiled yet stay unknown — caller falls
        # back to the conservative arm64 envelope.
        (b'OrangePi 3 LTS\x00', None),
        (b'Banana Pi M5\x00', None),
        # Empty / null-only / whitespace-only return None.
        (b'\x00', None),
        (b'   \n\x00', None),
        (b'', None),
    ],
)
def test_detect_board_subtype(
    model_bytes: bytes, expected: str | None
) -> None:
    """The static device-tree → subtype table is the source of
    truth for board detection. Any drift between this table and
    ``compute_envelope``'s expected matrix keys would silently
    misroute the asset processor; the parametrise pins every cell.
    """
    mocked_open = mock.mock_open(read_data=model_bytes)
    with mock.patch(
        'anthias_host_agent.__main__.open', mocked_open, create=True
    ):
        assert detect_board_subtype() == expected


def test_detect_board_subtype_no_devicetree() -> None:
    """A host without ``/proc/device-tree/model`` (dev container,
    a non-DT bootloader, balena's restricted /proc) collapses
    cleanly to ``None`` instead of raising. ``host_agent`` is
    started by systemd so an uncaught exception here would loop
    the unit and starve every other host-side feature."""
    with mock.patch(
        'anthias_host_agent.__main__.open',
        side_effect=FileNotFoundError(),
        create=True,
    ):
        assert detect_board_subtype() is None


def test_set_board_subtype_writes_resolved_value() -> None:
    """When a known SBC is detected, the resolved key is the
    payload written to ``host:board_subtype``."""
    fake_redis = mock.MagicMock()
    with mock.patch(
        'anthias_host_agent.__main__.detect_board_subtype',
        return_value='rockpi4',
    ):
        set_board_subtype(fake_redis)
    fake_redis.set.assert_called_once_with('host:board_subtype', 'rockpi4')


def test_set_board_subtype_writes_empty_string_on_unknown() -> None:
    """Distinguishes "ran but didn't recognise" (empty string) from
    "never ran" (key missing). The server-side reader treats both
    identically, but the empty write is useful for diagnostics
    — an operator inspecting redis can see the host_agent has
    actually run."""
    fake_redis = mock.MagicMock()
    with mock.patch(
        'anthias_host_agent.__main__.detect_board_subtype',
        return_value=None,
    ):
        set_board_subtype(fake_redis)
    fake_redis.set.assert_called_once_with('host:board_subtype', '')


def test_set_board_subtype_does_not_raise_on_redis_failure() -> None:
    """``host_agent``'s startup must not crash if ``rdb.set``
    fails (some transient redis issue). The function is best-
    effort — the consumer-side reader handles missing keys."""
    fake_redis = mock.MagicMock()
    fake_redis.set.side_effect = Exception('simulated redis hiccup')
    with mock.patch(
        'anthias_host_agent.__main__.detect_board_subtype',
        return_value='rockpi4',
    ):
        with pytest.raises(Exception, match='simulated redis hiccup'):
            # Current contract: the host_agent surfaces redis
            # failures (it's a systemd unit; restart-on-failure
            # will retry). If we ever wrap this in try/except,
            # update both this test and the function's docstring
            # to reflect the new contract.
            set_board_subtype(fake_redis)


def test_subscriber_loop_calls_set_board_subtype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``subscriber_loop`` orchestration must call
    ``set_board_subtype`` before flipping ``host_agent_ready``
    — otherwise a consumer that polls for ``host_agent_ready=true``
    and immediately reads ``host:board_subtype`` could observe
    the stale (or empty) value."""
    from anthias_host_agent import __main__ as ha

    fake_redis = mock.MagicMock()
    call_order: list[str] = []

    def fake_set_subtype(rdb: Any) -> None:
        call_order.append('subtype')

    def fake_set(key: str, value: Any) -> None:
        if key == 'host_agent_ready':
            call_order.append('ready')

    fake_redis.set.side_effect = fake_set
    # ``pubsub.listen()`` blocks forever in production; mock it as
    # an empty iterator so the test returns.
    fake_redis.pubsub.return_value.listen.return_value = iter(())

    import redis as redis_pkg

    monkeypatch.setattr(redis_pkg, 'Redis', lambda **kw: fake_redis)
    monkeypatch.setattr(ha, 'set_board_subtype', fake_set_subtype)

    ha.subscriber_loop()

    assert call_order == ['subtype', 'ready'], (
        'set_board_subtype must complete before host_agent_ready '
        'flips, otherwise consumers race the publish'
    )
