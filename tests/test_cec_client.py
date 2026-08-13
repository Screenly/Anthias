"""Tests for the server->viewer CEC client.

The viewer owns ``/dev/cec*`` because it is the only container that can
reach it on every board and every deployment — critically including
balena, where nothing on-device can enumerate the host's adapters and a
statically listed absent node would stop the container from starting.
These cover the request-reply contract between the two.
"""

from typing import Any
from unittest import mock

import pytest

from anthias_common.errors import ReplyTimeoutError
from anthias_server.lib import cec, cec_client


def _bus(reply: Any) -> Any:
    """Patch the publish/collect pair, returning the publisher mock."""
    publisher = mock.MagicMock()
    collector = mock.MagicMock()
    if isinstance(reply, Exception):
        collector.recv_json.side_effect = reply
    else:
        collector.recv_json.return_value = reply
    return publisher, collector


def _patched(publisher: Any, collector: Any) -> Any:
    return (
        mock.patch(
            'anthias_server.lib.cec_client.ViewerPublisher.get_instance',
            return_value=publisher,
        ),
        mock.patch(
            'anthias_server.lib.cec_client.ReplyCollector.get_instance',
            return_value=collector,
        ),
    )


# ---------------------------------------------------------------------------
# Availability — a Redis fact, not a round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('stored', 'expected'),
    [(b'1', True), ('1', True), (b'0', False), ('0', False), (None, False)],
)
def test_available_reads_the_published_fact(
    stored: Any, expected: bool
) -> None:
    """Deliberately not a request-reply: this gates a settings-page
    render, so it must not cost a viewer round trip."""
    client = mock.MagicMock()
    client.get.return_value = stored
    with mock.patch.object(
        cec_client, 'connect_to_redis', return_value=client
    ):
        assert cec_client.available() is expected


def test_available_is_false_when_redis_is_unreachable() -> None:
    """A broken redis must hide the controls, not raise into a page
    render."""
    with mock.patch.object(
        cec_client, 'connect_to_redis', side_effect=OSError('no redis')
    ):
        assert cec_client.available() is False


def test_publish_availability_writes_the_flag() -> None:
    client = mock.MagicMock()
    with mock.patch.object(
        cec_client, 'connect_to_redis', return_value=client
    ):
        cec_client.publish_availability(True)
    client.set.assert_called_once_with(cec_client.CEC_AVAILABLE_KEY, '1')


# ---------------------------------------------------------------------------
# Power status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'status',
    [
        cec.PowerStatus.ON,
        cec.PowerStatus.STANDBY,
        cec.PowerStatus.NO_PEER,
        cec.PowerStatus.NO_LINK,
        cec.PowerStatus.NO_ADAPTER,
    ],
)
def test_power_status_round_trips_every_state(
    status: cec.PowerStatus,
) -> None:
    publisher, collector = _bus({'status': str(status)})
    p1, p2 = _patched(publisher, collector)
    with p1, p2:
        assert cec_client.power_status() == status
    # The command carries a correlation id so the reply can be matched.
    sent = publisher.send_to_viewer.call_args.args[0]
    assert sent.startswith('display_power_status&')


def test_power_status_raises_when_the_viewer_does_not_answer() -> None:
    """A silent viewer means we know nothing — distinct from a display
    that answered "no peer", and the caller must be able to tell."""
    publisher, collector = _bus(ReplyTimeoutError())
    p1, p2 = _patched(publisher, collector)
    with p1, p2, pytest.raises(cec_client.ViewerUnavailableError):
        cec_client.power_status()


def test_power_status_survives_an_unrecognised_state() -> None:
    publisher, collector = _bus({'status': 'something-new'})
    p1, p2 = _patched(publisher, collector)
    with p1, p2:
        assert cec_client.power_status() == cec.PowerStatus.ERROR


def test_malformed_reply_is_an_error_not_a_crash() -> None:
    publisher, collector = _bus(['not', 'a', 'dict'])
    p1, p2 = _patched(publisher, collector)
    with p1, p2, pytest.raises(cec_client.ViewerUnavailableError):
        cec_client.power_status()


# ---------------------------------------------------------------------------
# Power commands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('on', 'command'), [(True, 'display_on'), (False, 'display_off')]
)
def test_set_power_sends_the_right_command(on: bool, command: str) -> None:
    publisher, collector = _bus({'acknowledged': 1, 'attempted': 2})
    p1, p2 = _patched(publisher, collector)
    with p1, p2:
        assert cec_client.set_power(on) == (1, 2)
    assert publisher.send_to_viewer.call_args.args[0].startswith(f'{command}&')


def test_set_power_raises_when_the_viewer_bus_was_busy() -> None:
    """``busy`` means nothing was transmitted. Returning (0, 0) instead
    would let the scheduler latch a command that never reached the TV and
    skip the retry for the rest of the off-period."""
    publisher, collector = _bus({'busy': True})
    p1, p2 = _patched(publisher, collector)
    with p1, p2, pytest.raises(cec_client.ViewerUnavailableError):
        cec_client.set_power(False)


def test_set_power_defaults_missing_counts_to_zero() -> None:
    publisher, collector = _bus({})
    p1, p2 = _patched(publisher, collector)
    with p1, p2:
        assert cec_client.set_power(True) == (0, 0)
