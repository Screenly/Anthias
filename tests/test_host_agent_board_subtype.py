"""Tests for ``anthias_host_agent``'s board subtype publisher.

``host_agent`` runs on the host (outside any container) and writes
the resolved board subtype to Redis at ``host:board_subtype``.
Server + viewer read it to upgrade the catch-all ``arm64``
DEVICE_TYPE into a board-specific envelope when the silicon
supports it. The detection table itself lives in
``anthias_common.device_helper.detect_board_subtype`` — shared with
``anthias_common.board``'s in-container fallback (used on balena,
where no host_agent service runs). We pin:

* the device-tree → subtype mapping for known boards (Rock Pi 4);
* unknown / empty / missing device-tree all collapse to ``None``;
* the Redis publish writes the resolved subtype (or empty string)
  exactly once per host_agent start;
* ``get_board_subtype``'s Redis-then-device-tree fallback order.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from anthias_common.board import get_board_subtype
from anthias_common.device_helper import detect_board_subtype
from anthias_host_agent.__main__ import (
    detect_total_mem_kb,
    set_board_subtype,
    set_total_mem_kb,
)


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
        'anthias_common.device_helper.open', mocked_open, create=True
    ):
        assert detect_board_subtype() == expected


def test_detect_board_subtype_no_devicetree() -> None:
    """A host without ``/proc/device-tree/model`` (dev container,
    a non-DT bootloader, balena's restricted /proc) collapses
    cleanly to ``None`` instead of raising. ``host_agent`` is
    started by systemd so an uncaught exception here would loop
    the unit and starve every other host-side feature."""
    with mock.patch(
        'anthias_common.device_helper.open',
        side_effect=FileNotFoundError(),
        create=True,
    ):
        assert detect_board_subtype() is None


def test_get_board_subtype_prefers_redis_value() -> None:
    """A host_agent-published value wins; the device tree is not
    consulted when Redis already answers."""
    fake_redis = mock.MagicMock()
    fake_redis.get.return_value = b'rockpi4'
    with (
        mock.patch(
            'anthias_common.board.connect_to_redis',
            return_value=fake_redis,
        ),
        mock.patch(
            'anthias_common.board.detect_board_subtype'
        ) as mocked_detect,
    ):
        assert get_board_subtype() == 'rockpi4'
    mocked_detect.assert_not_called()


@pytest.mark.parametrize('redis_value', [None, b'', b'   '])
def test_get_board_subtype_falls_back_to_device_tree(
    redis_value: bytes | None,
) -> None:
    """No host_agent (balena fleets) or an empty publish falls back
    to reading the device tree in-container — this is what upgrades
    the ``anthias-rockpi4`` balena fleet's codec gate from the empty
    arm64 envelope without a host-side daemon."""
    fake_redis = mock.MagicMock()
    fake_redis.get.return_value = redis_value
    mocked_open = mock.mock_open(read_data=b'Radxa ROCK Pi 4B\x00')
    with (
        mock.patch(
            'anthias_common.board.connect_to_redis',
            return_value=fake_redis,
        ),
        mock.patch(
            'anthias_common.device_helper.open', mocked_open, create=True
        ),
    ):
        assert get_board_subtype() == 'rockpi4'


def test_get_board_subtype_unknown_everywhere_returns_none() -> None:
    """Redis empty + unknown device tree → ``None`` (caller keeps the
    raw DEVICE_TYPE and the conservative empty arm64 codec set)."""
    fake_redis = mock.MagicMock()
    fake_redis.get.return_value = None
    mocked_open = mock.mock_open(read_data=b'OrangePi 3 LTS\x00')
    with (
        mock.patch(
            'anthias_common.board.connect_to_redis',
            return_value=fake_redis,
        ),
        mock.patch(
            'anthias_common.device_helper.open', mocked_open, create=True
        ),
    ):
        assert get_board_subtype() is None


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


def test_set_board_subtype_propagates_redis_failures() -> None:
    """Current contract: ``set_board_subtype`` propagates exceptions
    from ``rdb.set``. host_agent is a systemd unit with restart-on-
    failure, so a transient redis hiccup at startup loops the unit
    until redis is reachable rather than silently shipping a stale
    or empty subtype to downstream consumers. If we ever wrap this
    in try/except, update both this test and the function's
    docstring to reflect the new contract."""
    fake_redis = mock.MagicMock()
    fake_redis.set.side_effect = Exception('simulated redis hiccup')
    with mock.patch(
        'anthias_host_agent.__main__.detect_board_subtype',
        return_value='rockpi4',
    ):
        with pytest.raises(Exception, match='simulated redis hiccup'):
            set_board_subtype(fake_redis)


def test_subscriber_loop_calls_set_board_subtype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``subscriber_loop`` orchestration must call
    ``set_board_subtype`` *and* ``set_total_mem_kb`` before flipping
    ``host_agent_ready`` — otherwise a consumer that polls for
    ``host_agent_ready=true`` and immediately reads either
    ``host:board_subtype`` or ``host:total_mem_kb`` could observe a
    stale (or empty) value. The two host-shape publishers run before
    readiness; the order between them doesn't matter for consumers."""
    from anthias_host_agent import __main__ as ha

    fake_redis = mock.MagicMock()
    call_order: list[str] = []

    def fake_set_subtype(rdb: Any) -> None:
        call_order.append('subtype')

    def fake_set_total_mem(rdb: Any) -> None:
        call_order.append('total_mem')

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
    monkeypatch.setattr(ha, 'set_total_mem_kb', fake_set_total_mem)

    ha.subscriber_loop()

    assert call_order[-1] == 'ready', (
        'host_agent_ready must flip last so consumers polling on it '
        'never observe a stale host:* publish'
    )
    assert set(call_order[:-1]) == {'subtype', 'total_mem'}, (
        'subscriber_loop must call both publishers exactly once '
        'before flipping readiness'
    )


@pytest.mark.parametrize(
    ('meminfo_body', 'expected'),
    [
        # Pi 4 4 GB: typical /proc/meminfo on a healthy box. The
        # function reads the kB count, not the trailing unit token.
        (
            b'MemTotal:        3947648 kB\nMemFree:          200000 kB\n',
            3947648,
        ),
        # Rock Pi 4 1 GB: the on-device measurement that motivated
        # the low-RAM mode.
        (
            b'MemTotal:         990044 kB\nMemFree:           32500 kB\n',
            990044,
        ),
        # Tab whitespace instead of spaces — split() handles both.
        (b'MemTotal:\t8123456\tkB\n', 8123456),
        # Pre-MemTotal junk lines (e.g. kernel writes a leading
        # comment on some boards) — the loop scans until it finds
        # the line, so prefix garbage is tolerated.
        (b'Garbage: 0 kB\nMemTotal:    1572864 kB\n', 1572864),
    ],
)
def test_detect_total_mem_kb(meminfo_body: bytes, expected: int) -> None:
    """The kB count from /proc/meminfo is the source of truth for
    low-RAM gating. Tests pin the parsing against the kernel's actual
    formats (space- and tab-separated, decoy lines, …)."""
    mocked_open = mock.mock_open(read_data=meminfo_body.decode('utf-8'))
    with mock.patch(
        'anthias_host_agent.__main__.open', mocked_open, create=True
    ):
        assert detect_total_mem_kb() == expected


def test_detect_total_mem_kb_missing_line() -> None:
    """A truncated /proc/meminfo without a MemTotal line returns
    ``None`` so the caller treats RAM as unknown — same recovery
    path as a missing /proc/meminfo or unparseable value."""
    mocked_open = mock.mock_open(read_data='MemFree: 100000 kB\n')
    with mock.patch(
        'anthias_host_agent.__main__.open', mocked_open, create=True
    ):
        assert detect_total_mem_kb() is None


def test_detect_total_mem_kb_unparseable_value() -> None:
    """A non-integer in the MemTotal field returns ``None`` rather
    than crashing the host_agent unit."""
    mocked_open = mock.mock_open(
        read_data='MemTotal:        not-a-number kB\n'
    )
    with mock.patch(
        'anthias_host_agent.__main__.open', mocked_open, create=True
    ):
        assert detect_total_mem_kb() is None


def test_detect_total_mem_kb_unreadable_proc() -> None:
    """A host where /proc/meminfo can't be opened (container, balena
    restricted /proc) returns ``None``."""
    with mock.patch(
        'anthias_host_agent.__main__.open',
        side_effect=OSError('no such file'),
        create=True,
    ):
        assert detect_total_mem_kb() is None


def test_set_total_mem_kb_writes_value() -> None:
    """A successful detect publishes the integer as a string —
    redis-py serialises bytes/str natively; integers go through
    ``str(int)`` for round-trip parity with the get-side parser."""
    fake_redis = mock.MagicMock()
    with mock.patch(
        'anthias_host_agent.__main__.detect_total_mem_kb',
        return_value=990044,
    ):
        set_total_mem_kb(fake_redis)
    fake_redis.set.assert_called_once_with('host:total_mem_kb', '990044')


def test_set_total_mem_kb_writes_empty_string_on_unknown() -> None:
    """Mirrors set_board_subtype: "unknown RAM" is the empty string,
    distinguishable from "key never set" but treated identically by
    the server-side reader (``get_total_mem_kb`` returns ``None``
    for both)."""
    fake_redis = mock.MagicMock()
    with mock.patch(
        'anthias_host_agent.__main__.detect_total_mem_kb',
        return_value=None,
    ):
        set_total_mem_kb(fake_redis)
    fake_redis.set.assert_called_once_with('host:total_mem_kb', '')
