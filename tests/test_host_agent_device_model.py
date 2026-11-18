"""Tests for the host's device-tree board model.

Non-Pi SBCs write no cpuinfo ``Model`` line and expose no DMI, so
``/proc/device-tree/model`` is the only thing that names them — and
the server container cannot read it, because Docker masks
``/sys/firmware`` in unprivileged containers. ``anthias_host_agent``
therefore publishes it to Redis at ``host:device_model`` and
``anthias_common.board`` reads it back. Without that hop the System
Info card labelled every SBC 'Generic aarch64 Device' and
``/api/v2/info`` reported ``device_model: null``.

We pin:

* the NUL-terminated device-tree string → clean model name;
* the host_agent publish (resolved value, or empty string on a host
  with no device tree);
* ``get_device_model``'s Redis-then-local-read order;
* that the resolved model becomes the System Info card's primary line
  without disturbing the Pi / x86 labels.
"""

from __future__ import annotations

from unittest import mock

import pytest

from anthias_common import device_helper
from anthias_common.board import get_device_model, get_device_model_parts
from anthias_common.device_helper import read_device_tree_model
from anthias_host_agent.__main__ import set_device_model


@pytest.mark.parametrize(
    ('model_bytes', 'expected'),
    [
        # What the kernel actually writes — NUL-terminated UTF-8.
        (b'FriendlyElec NanoPi R3S LTS\x00', 'FriendlyElec NanoPi R3S LTS'),
        (b'Radxa ROCK Pi 4B\x00', 'Radxa ROCK Pi 4B'),
        (
            b'Raspberry Pi 5 Model B Rev 1.0\x00',
            'Raspberry Pi 5 Model B Rev 1.0',
        ),
        # Stray whitespace / embedded newlines collapse to single
        # spaces so the card never renders a ragged label.
        (b'  NanoPi   R3S\n LTS \n\x00', 'NanoPi R3S LTS'),
        # Empty / NUL-only / whitespace-only are "no model".
        (b'\x00', ''),
        (b'   \n\x00', ''),
        (b'', ''),
    ],
)
def test_read_device_tree_model(model_bytes: bytes, expected: str) -> None:
    mocked_open = mock.mock_open(read_data=model_bytes)
    with mock.patch(
        'anthias_common.device_helper.open', mocked_open, create=True
    ):
        assert read_device_tree_model() == expected


def test_read_device_tree_model_no_devicetree() -> None:
    """x86 hosts — and every unprivileged container, where
    ``/sys/firmware`` is masked — have no readable tree. That must
    return '' rather than raise: this runs at page render and inside
    the systemd-managed host_agent, neither of which should die over
    a missing file."""
    with mock.patch(
        'anthias_common.device_helper.open',
        side_effect=FileNotFoundError(),
        create=True,
    ):
        assert read_device_tree_model() == ''


def test_set_device_model_publishes_resolved_model() -> None:
    fake_redis = mock.MagicMock()
    with mock.patch(
        'anthias_host_agent.__main__.read_device_tree_model',
        return_value='FriendlyElec NanoPi R3S LTS',
    ):
        set_device_model(fake_redis)
    fake_redis.set.assert_called_once_with(
        'host:device_model', 'FriendlyElec NanoPi R3S LTS'
    )


def test_set_device_model_publishes_empty_string_on_x86() -> None:
    """A host with no device tree still writes the key, so the reader
    can tell "host_agent ran and found nothing" from "never ran"."""
    fake_redis = mock.MagicMock()
    with mock.patch(
        'anthias_host_agent.__main__.read_device_tree_model',
        return_value='',
    ):
        set_device_model(fake_redis)
    fake_redis.set.assert_called_once_with('host:device_model', '')


def test_get_device_model_prefers_redis_value() -> None:
    """The published value wins and the local read is skipped — the
    server container's own read would come back empty anyway."""
    fake_redis = mock.MagicMock()
    fake_redis.get.return_value = b'FriendlyElec NanoPi R3S LTS'
    with (
        mock.patch(
            'anthias_common.board.connect_to_redis',
            return_value=fake_redis,
        ),
        mock.patch(
            'anthias_common.board.device_helper.read_device_tree_model'
        ) as mocked_read,
    ):
        assert get_device_model() == 'FriendlyElec NanoPi R3S LTS'
    mocked_read.assert_not_called()


@pytest.mark.parametrize('redis_value', [None, b'', b'   '])
def test_get_device_model_falls_back_to_local_read(
    redis_value: bytes | None,
) -> None:
    """Missing / empty key → read the tree directly. That succeeds on
    the host and in the privileged viewer; in an unprivileged
    container it yields '' and the caller keeps its generic label."""
    fake_redis = mock.MagicMock()
    fake_redis.get.return_value = redis_value
    with (
        mock.patch(
            'anthias_common.board.connect_to_redis',
            return_value=fake_redis,
        ),
        mock.patch(
            'anthias_common.board.device_helper.read_device_tree_model',
            return_value='Radxa ROCK Pi 4B',
        ),
    ):
        assert get_device_model() == 'Radxa ROCK Pi 4B'


def test_get_device_model_unavailable_everywhere_is_empty() -> None:
    fake_redis = mock.MagicMock()
    fake_redis.get.side_effect = Exception('redis down')
    with (
        mock.patch(
            'anthias_common.board.connect_to_redis',
            return_value=fake_redis,
        ),
        mock.patch(
            'anthias_common.board.device_helper.read_device_tree_model',
            return_value='',
        ),
    ):
        assert get_device_model() == ''


def test_device_model_parts_labels_sbc_by_board_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression this fixes: an SBC with no cpuinfo Model and no
    DMI now renders its board name instead of 'Generic aarch64
    Device'."""
    monkeypatch.setattr(
        device_helper, 'parse_cpu_info', lambda: {'cpu_count': 4}
    )
    monkeypatch.setattr(device_helper, '_read_sysfs', lambda _path: '')
    monkeypatch.setattr(device_helper, '_read_cpu_brand', lambda: '')
    with mock.patch(
        'anthias_common.board.device_helper.read_device_tree_model',
        return_value='FriendlyElec NanoPi R3S LTS',
    ):
        assert get_device_model_parts() == (
            'FriendlyElec NanoPi R3S LTS',
            '',
        )


def test_device_model_parts_pi_ignores_device_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Pi keeps its firmware Model line — that carries the board
    revision ('Rev 1.0'), which the device tree's model string
    doesn't."""
    monkeypatch.setattr(
        device_helper,
        'parse_cpu_info',
        lambda: {
            'cpu_count': 4,
            'model': 'Raspberry Pi 5 Model B Rev 1.0',
        },
    )
    with mock.patch(
        'anthias_common.board.device_helper.read_device_tree_model',
        return_value='Raspberry Pi 5 Model B',
    ):
        assert get_device_model_parts() == (
            'Raspberry Pi 5 Model B Rev 1.0',
            '',
        )
