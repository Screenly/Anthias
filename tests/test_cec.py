"""Tests for the kernel-CEC layer (``anthias_server.lib.cec``).

The uABI constants below are asserted against values **verified on real
hardware**, not transcribed from a header: they were recovered by
scanning the ioctl request space on a Pi 5 and recording which numbers
the ``vc4_hdmi`` driver accepted. An earlier off-by-two in
``CEC_ADAP_G_CONNECTOR_INFO``'s ``nr`` silently invalidated a whole
fleet probe, so these are pinned.
"""

import struct
from typing import Any
from unittest import mock

import pytest

from anthias_server.lib import cec

# ---------------------------------------------------------------------------
# uABI encoding — pinned against hardware-verified values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('CEC_ADAP_G_CAPS', 0xC04C6100),
        ('CEC_ADAP_G_PHYS_ADDR', 0x80026101),
        ('CEC_ADAP_G_LOG_ADDRS', 0x805C6103),
        ('CEC_ADAP_S_LOG_ADDRS', 0xC05C6104),
        ('CEC_TRANSMIT', 0xC0386105),
        ('CEC_ADAP_G_CONNECTOR_INFO', 0x8044610A),
    ],
)
def test_ioctl_request_numbers_match_hardware(
    name: str, expected: int
) -> None:
    assert getattr(cec, name) == expected


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        # Three different enumerations in linux/cec.h that do not share
        # numbering. Pinned because using the audio-system value for
        # log_addr_type made the adapter claim LA 5 (Audio System)
        # instead of LA 4 (Playback Device 1), which can make a TV
        # enable ARC and mute its own speakers.
        ('CEC_LOG_ADDR_TYPE_PLAYBACK', 3),
        ('CEC_OP_PRIM_DEVTYPE_PLAYBACK', 4),
        ('CEC_OP_ALL_DEVTYPE_PLAYBACK', 0x10),
    ],
)
def test_playback_device_constants_match_the_kernel(
    name: str, expected: int
) -> None:
    assert getattr(cec, name) == expected


@pytest.mark.parametrize(
    ('fmt', 'size'),
    [
        ('_FMT_CAPS', 76),
        ('_FMT_LOG_ADDRS', 92),
        ('_FMT_MSG', 56),
    ],
)
def test_struct_layouts_match_the_kernel(fmt: str, size: int) -> None:
    """A short struct silently truncates the ioctl payload; a long one
    is rejected outright. Both sizes come from linux/cec.h and were
    confirmed by the same hardware scan."""
    assert struct.calcsize(getattr(cec, fmt)) == size


# ---------------------------------------------------------------------------
# Adapter properties
# ---------------------------------------------------------------------------


def _adapter(**kwargs: Any) -> cec.CecAdapter:
    defaults: dict[str, Any] = {
        'node': '/dev/cec0',
        'driver': 'vc4_hdmi',
        'name': 'vc4-hdmi-0',
        'capabilities': cec.CEC_CAP_TRANSMIT | cec.CEC_CAP_LOG_ADDRS,
        'physical_address': 0x1000,
        'drm_card': 1,
        'drm_connector_id': 35,
    }
    defaults.update(kwargs)
    return cec.CecAdapter(**defaults)


def test_valid_physical_address_means_a_live_link() -> None:
    """1.0.0.0 is what the Pi 5 testbed reports on the HDMI port with a
    monitor attached — the driver takes it from the sink's EDID."""
    assert _adapter(physical_address=0x1000).has_link is True


def test_invalid_physical_address_means_no_link() -> None:
    """f.f.f.f is the Pi 4's answer on both ports with nothing attached,
    and the Pi 5's answer on its empty second port."""
    assert _adapter(physical_address=0xFFFF).has_link is False


def test_physical_address_is_rendered_in_cec_notation() -> None:
    assert _adapter(physical_address=0x1000).physical_address_str == '1.0.0.0'
    assert _adapter(physical_address=0xFFFF).physical_address_str == 'f.f.f.f'
    assert _adapter(physical_address=0x1234).physical_address_str == '1.2.3.4'


def test_adapter_without_transmit_capability_is_not_usable() -> None:
    assert _adapter(capabilities=cec.CEC_CAP_LOG_ADDRS).can_transmit is False


def test_live_filters_out_dead_and_untransmittable_adapters() -> None:
    """The Pi 4/5 shape: two adapters, only one with a display."""
    live = _adapter(node='/dev/cec0', physical_address=0x1000)
    dead = _adapter(node='/dev/cec1', physical_address=0xFFFF)
    with mock.patch.object(
        cec.CecAdapter, 'enumerate', return_value=[live, dead]
    ):
        assert cec.CecAdapter.live() == [live]


def test_enumerate_skips_adapters_it_cannot_probe() -> None:
    """A half-broken adapter must not stop us driving the working one."""
    good = _adapter(node='/dev/cec1')

    def _probe(node: str) -> cec.CecAdapter:
        if node == '/dev/cec0':
            raise cec.CecError('cannot open')
        return good

    with (
        mock.patch(
            'anthias_server.lib.cec.glob.glob',
            return_value=['/dev/cec0', '/dev/cec1'],
        ),
        mock.patch.object(cec.CecAdapter, '_probe', side_effect=_probe),
    ):
        assert cec.CecAdapter.enumerate() == [good]


# ---------------------------------------------------------------------------
# Aggregation across multiple displays
# ---------------------------------------------------------------------------


def test_no_adapters_reports_no_adapter() -> None:
    with mock.patch.object(cec.CecAdapter, 'enumerate', return_value=[]):
        assert cec.power_status() == cec.PowerStatus.NO_ADAPTER


def test_adapters_but_no_link_reports_no_link() -> None:
    dead = _adapter(physical_address=0xFFFF)
    with mock.patch.object(cec.CecAdapter, 'enumerate', return_value=[dead]):
        assert cec.power_status() == cec.PowerStatus.NO_LINK


@pytest.mark.parametrize(
    ('states', 'expected'),
    [
        ([cec.PowerStatus.ON], cec.PowerStatus.ON),
        ([cec.PowerStatus.ON, cec.PowerStatus.ON], cec.PowerStatus.ON),
        (
            [cec.PowerStatus.STANDBY, cec.PowerStatus.STANDBY],
            cec.PowerStatus.STANDBY,
        ),
        # Two monitors that disagree must not be reported as either.
        (
            [cec.PowerStatus.ON, cec.PowerStatus.STANDBY],
            cec.PowerStatus.UNKNOWN,
        ),
        # One answered, one is a plain monitor: the answer still counts.
        (
            [cec.PowerStatus.ON, cec.PowerStatus.NO_PEER],
            cec.PowerStatus.ON,
        ),
        # Nothing answered at all.
        (
            [cec.PowerStatus.NO_PEER, cec.PowerStatus.NO_PEER],
            cec.PowerStatus.NO_PEER,
        ),
    ],
)
def test_power_status_aggregates_conservatively(
    states: list[Any], expected: Any
) -> None:
    adapters = [
        _adapter(node=f'/dev/cec{i}', physical_address=0x1000)
        for i in range(len(states))
    ]
    with (
        mock.patch.object(cec.CecAdapter, 'enumerate', return_value=adapters),
        mock.patch.object(cec.CecAdapter, 'power_status', side_effect=states),
    ):
        assert cec.power_status() == expected


@pytest.mark.parametrize(
    'error', [OSError('boom'), cec.CecError('boom'), TimeoutError('wedged')]
)
def test_a_failed_query_is_an_error_not_a_missing_peer(
    error: Exception,
) -> None:
    """ "We could not ask" must never be reported as "we asked and nothing
    answered". They look identical to a user and mean opposite things —
    one is a broken adapter, the other is a perfectly normal monitor.
    Collapsing them is what made GH #3267 unactionable, and an early
    build of this module reintroduced it (a failed logical-address claim
    surfaced as a 0 ms 'no-peer' on the Pi 5)."""
    adapters = [_adapter(physical_address=0x1000)]
    with (
        mock.patch.object(cec.CecAdapter, 'enumerate', return_value=adapters),
        mock.patch.object(cec.CecAdapter, 'power_status', side_effect=error),
    ):
        assert cec.power_status() == cec.PowerStatus.ERROR


def test_one_broken_adapter_does_not_mask_a_real_answer() -> None:
    """Two displays, one wedged: the one that answered still wins."""
    adapters = [
        _adapter(node='/dev/cec0', physical_address=0x1000),
        _adapter(node='/dev/cec1', physical_address=0x2000),
    ]
    with (
        mock.patch.object(cec.CecAdapter, 'enumerate', return_value=adapters),
        mock.patch.object(
            cec.CecAdapter,
            'power_status',
            side_effect=[cec.CecError('boom'), cec.PowerStatus.ON],
        ),
    ):
        assert cec.power_status() == cec.PowerStatus.ON


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


def test_set_power_reaches_every_live_adapter() -> None:
    """The whole reason there is no adapter selection: a device with two
    monitors attached must not leave the second one lit."""
    adapters = [
        _adapter(node='/dev/cec0', physical_address=0x1000),
        _adapter(node='/dev/cec1', physical_address=0x2000),
    ]
    with (
        mock.patch.object(cec.CecAdapter, 'live', return_value=adapters),
        mock.patch.object(
            cec.CecAdapter, 'set_power', return_value=True
        ) as set_power,
    ):
        assert cec.set_power(False) == (2, 2)
    assert set_power.call_count == 2


def test_set_power_counts_only_acknowledged_displays() -> None:
    adapters = [
        _adapter(node='/dev/cec0', physical_address=0x1000),
        _adapter(node='/dev/cec1', physical_address=0x2000),
    ]
    with (
        mock.patch.object(cec.CecAdapter, 'live', return_value=adapters),
        mock.patch.object(
            cec.CecAdapter, 'set_power', side_effect=[True, False]
        ),
    ):
        assert cec.set_power(True) == (1, 2)


def test_set_power_keeps_going_after_one_adapter_fails() -> None:
    """One wedged port must not stop the other display being powered."""
    adapters = [
        _adapter(node='/dev/cec0', physical_address=0x1000),
        _adapter(node='/dev/cec1', physical_address=0x2000),
    ]
    with (
        mock.patch.object(cec.CecAdapter, 'live', return_value=adapters),
        mock.patch.object(
            cec.CecAdapter,
            'set_power',
            side_effect=[cec.CecError('boom'), True],
        ),
    ):
        assert cec.set_power(True) == (1, 2)


def test_set_power_on_a_dead_adapter_short_circuits() -> None:
    """No link means no transmit — and crucially no ioctl at all."""
    adapter = _adapter(physical_address=0xFFFF)
    with mock.patch.object(cec, '_open_node') as open_node:
        assert adapter.set_power(True) is False
    open_node.assert_not_called()


def test_power_status_on_a_dead_adapter_short_circuits() -> None:
    adapter = _adapter(physical_address=0xFFFF)
    with mock.patch.object(cec, '_open_node') as open_node:
        assert adapter.power_status() == cec.PowerStatus.NO_LINK
    open_node.assert_not_called()


def test_available_is_true_when_any_adapter_exists() -> None:
    with mock.patch.object(
        cec.CecAdapter, 'enumerate', return_value=[_adapter()]
    ):
        assert cec.available() is True


def test_available_is_false_on_a_board_with_no_cec_hardware() -> None:
    """The x86 testbed's shape: 3 HDMI + 3 DP connectors, zero
    /dev/cec* — DisplayPort carries no CEC channel and the Intel HDMI
    outputs do not expose one either."""
    with mock.patch.object(cec.CecAdapter, 'enumerate', return_value=[]):
        assert cec.available() is False


# ---------------------------------------------------------------------------
# The wall-clock guard
# ---------------------------------------------------------------------------


def test_claim_clears_the_adapter_before_configuring_it() -> None:
    """The kernel returns EBUSY if you configure an already-configured
    adapter, so the clear is mandatory rather than tidy.

    This is a live-upgrade regression: libcec leaves every adapter
    configured after its process exits (observed on the Pi 5 — LA mask
    0x0002, vendor 0x001582 'Pulse-Eight'), so a device upgrading from
    the libcec implementation meets a dirty adapter on first run. Without
    the clear, every query failed with EBUSY.
    """
    calls: list[int] = []

    def _record(_fd: int, request: int, payload: Any) -> int:
        if request == cec.CEC_ADAP_S_LOG_ADDRS:
            # num_log_addrs is the 4th field of struct cec_log_addrs.
            calls.append(struct.unpack(cec._FMT_LOG_ADDRS, bytes(payload))[3])
        return 0

    with (
        mock.patch('anthias_server.lib.cec.fcntl.ioctl', side_effect=_record),
        mock.patch.object(cec, '_current_logical_address', return_value=4),
    ):
        assert cec._claim_logical_address(fd=99) == 4

    assert calls[0] == 0, 'must clear (num_log_addrs=0) first'
    assert calls[1] == 1, 'then claim exactly one logical address'


def test_claim_raises_rather_than_falling_back_to_unregistered() -> None:
    """An unregistered initiator can transmit but cannot receive a
    directed reply, so degrading to it silently turns every query into a
    false 'nothing answered'."""
    with (
        mock.patch(
            'anthias_server.lib.cec.fcntl.ioctl',
            side_effect=OSError(16, 'Device or resource busy'),
        ),
        pytest.raises(cec.CecError, match='logical address'),
    ):
        cec._claim_logical_address(fd=99)


def test_claim_raises_when_the_address_never_settles() -> None:
    with (
        mock.patch('anthias_server.lib.cec.fcntl.ioctl', return_value=0),
        mock.patch.object(cec, '_current_logical_address', return_value=None),
        mock.patch('anthias_server.lib.cec.time.sleep'),
        pytest.raises(cec.CecError, match='not claimed'),
    ):
        cec._claim_logical_address(fd=99)


def test_run_bounded_returns_the_value() -> None:
    assert cec._run_bounded(lambda: 'answer', 5.0) == 'answer'


def test_run_bounded_reraises_the_callables_error() -> None:
    def _boom() -> None:
        raise cec.CecError('nope')

    with pytest.raises(cec.CecError, match='nope'):
        cec._run_bounded(_boom, 5.0)


def test_run_bounded_gives_up_on_a_wedged_driver() -> None:
    """A blocking ioctl cannot be interrupted from Python, only
    abandoned. Abandoning beats wedging the celery worker, which is the
    Sentry class (ANTHIAS-A/9/B/31) this feature keeps causing."""
    import threading

    release = threading.Event()
    try:
        with pytest.raises(TimeoutError, match='exceeded'):
            cec._run_bounded(lambda: release.wait(30), 0.1)
    finally:
        release.set()


# ---------------------------------------------------------------------------
# Bus lock — CEC access is serialised across processes
# ---------------------------------------------------------------------------


def test_set_power_raises_rather_than_reporting_zero_when_bus_is_busy() -> (
    None
):
    """ "Never transmitted" must not look like "nothing acknowledged".
    The scheduler latches its state on the latter, so conflating them
    would leave a TV powered on for the rest of the off-period."""
    with (
        mock.patch.object(cec.CecAdapter, 'live', return_value=[_adapter()]),
        mock.patch.object(cec, '_bus_lock') as bus_lock,
    ):
        bus_lock.return_value.__enter__.return_value = False
        with pytest.raises(cec.CecBusyError):
            cec.set_power(True)


def test_power_status_reports_error_when_bus_is_busy() -> None:
    """A contended bus is "we could not ask", never a claim about the
    display's state."""
    adapters = [_adapter(physical_address=0x1000)]
    with (
        mock.patch.object(cec.CecAdapter, 'enumerate', return_value=adapters),
        mock.patch.object(cec, '_bus_lock') as bus_lock,
    ):
        bus_lock.return_value.__enter__.return_value = False
        assert cec.power_status() == cec.PowerStatus.ERROR


def test_bus_lock_releases_with_compare_and_delete() -> None:
    """Releasing must not delete a lock someone else acquired after our
    TTL expired mid-operation."""
    client = mock.MagicMock()
    client.set.return_value = True
    with (
        mock.patch(
            'anthias_common.utils.connect_to_redis', return_value=client
        ),
        cec._bus_lock() as locked,
    ):
        assert locked is True
    assert client.set.call_args.kwargs['nx'] is True
    assert client.set.call_args.kwargs['ex'] == cec._LOCK_TTL_S
    # Compare-and-delete, with our token as the comparison value.
    args = client.eval.call_args.args
    assert args[0] == cec._LOCK_RELEASE_LUA
    assert args[2] == cec._LOCK_KEY
    assert args[3] == client.set.call_args.args[1]


def test_bus_lock_proceeds_unlocked_when_redis_is_unreachable() -> None:
    """A single-writer device must keep working without redis rather
    than losing CEC entirely."""
    with (
        mock.patch(
            'anthias_common.utils.connect_to_redis',
            side_effect=OSError('no redis'),
        ),
        cec._bus_lock() as locked,
    ):
        assert locked is True


def test_set_power_skips_the_lock_when_there_is_nothing_to_drive() -> None:
    """Most of the fleet has no CEC link; paying a redis round trip on
    every scheduled transition there is pure waste (0.3-1.4s measured)."""
    with (
        mock.patch.object(cec.CecAdapter, 'live', return_value=[]),
        mock.patch.object(cec, '_bus_lock') as bus_lock,
    ):
        assert cec.set_power(True) == (0, 0)
    bus_lock.assert_not_called()
