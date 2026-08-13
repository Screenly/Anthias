import os
from typing import Any
from unittest import mock

import pytest

from anthias_server.lib import cec, cec_client, diagnostics


@pytest.mark.parametrize(
    'env_value,expected',
    [
        ('master', 'master'),
        ('feature/foo', 'feature/foo'),
        (None, None),
    ],
)
def test_get_git_branch(
    monkeypatch: Any, env_value: str | None, expected: str | None
) -> None:
    if env_value is None:
        monkeypatch.delenv('GIT_BRANCH', raising=False)
    else:
        monkeypatch.setenv('GIT_BRANCH', env_value)
    assert diagnostics.get_git_branch() == expected


def test_get_git_short_hash(monkeypatch: Any) -> None:
    monkeypatch.setenv('GIT_SHORT_HASH', 'abc1234')
    assert diagnostics.get_git_short_hash() == 'abc1234'

    monkeypatch.delenv('GIT_SHORT_HASH', raising=False)
    assert diagnostics.get_git_short_hash() is None


def test_get_git_hash(monkeypatch: Any) -> None:
    monkeypatch.setenv('GIT_HASH', 'abc1234deadbeef')
    assert diagnostics.get_git_hash() == 'abc1234deadbeef'

    monkeypatch.delenv('GIT_HASH', raising=False)
    assert diagnostics.get_git_hash() is None


def test_get_uptime_reads_proc_uptime() -> None:
    fake_uptime = '12345.67 234567.89\n'
    m_open = mock.mock_open(read_data=fake_uptime)
    with mock.patch('builtins.open', m_open):
        assert diagnostics.get_uptime() == pytest.approx(12345.67)
    m_open.assert_called_once_with('/proc/uptime', 'r')


def test_get_load_avg() -> None:
    with mock.patch.object(
        os, 'getloadavg', return_value=(0.123, 0.456, 1.789)
    ):
        result = diagnostics.get_load_avg()
    assert result == {'1 min': 0.12, '5 min': 0.46, '15 min': 1.79}


def test_get_utc_isodate_format() -> None:
    iso = diagnostics.get_utc_isodate()
    # Sanity: looks like an ISO-format timestamp.
    assert 'T' in iso
    assert len(iso) >= len('2025-01-01T00:00:00')


def test_get_debian_version_reads_file(tmp_path: Any) -> None:
    debian_file = tmp_path / 'debian_version'
    debian_file.write_text('13.0\n')
    with mock.patch.object(os.path, 'isfile', return_value=True):
        m_open = mock.mock_open(read_data='13.0\n')
        with mock.patch('builtins.open', m_open):
            assert diagnostics.get_debian_version() == '13.0'


def test_get_debian_version_missing_file() -> None:
    with mock.patch.object(os.path, 'isfile', return_value=False):
        assert (
            diagnostics.get_debian_version() == 'Unable to get Debian version.'
        )


def test_get_raspberry_code_returns_hardware() -> None:
    with mock.patch(
        'anthias_server.lib.diagnostics.device_helper.parse_cpu_info',
        return_value={'hardware': 'BCM2711', 'model': 'Pi 4'},
    ):
        assert diagnostics.get_raspberry_code() == 'BCM2711'


def test_get_raspberry_code_unknown() -> None:
    with mock.patch(
        'anthias_server.lib.diagnostics.device_helper.parse_cpu_info',
        return_value={},
    ):
        assert diagnostics.get_raspberry_code() == 'Unknown'


def test_get_raspberry_model_returns_model() -> None:
    with mock.patch(
        'anthias_server.lib.diagnostics.device_helper.parse_cpu_info',
        return_value={'model': 'Raspberry Pi 4 Model B'},
    ):
        assert diagnostics.get_raspberry_model() == 'Raspberry Pi 4 Model B'


def test_get_raspberry_model_unknown() -> None:
    with mock.patch(
        'anthias_server.lib.diagnostics.device_helper.parse_cpu_info',
        return_value={},
    ):
        assert diagnostics.get_raspberry_model() == 'Unknown'


def test_try_connectivity_all_succeed() -> None:
    with mock.patch(
        'anthias_server.lib.diagnostics.utils.url_fails', return_value=False
    ):
        results = diagnostics.try_connectivity()
    assert len(results) == 4
    for line in results:
        assert line.endswith(': OK')


def test_try_connectivity_all_fail() -> None:
    with mock.patch(
        'anthias_server.lib.diagnostics.utils.url_fails', return_value=True
    ):
        results = diagnostics.try_connectivity()
    assert len(results) == 4
    for line in results:
        assert line.endswith(': Error')


def test_try_connectivity_mixed() -> None:
    # Alternate True/False/True/False across the four URLs.
    side_effect = [True, False, True, False]
    with mock.patch(
        'anthias_server.lib.diagnostics.utils.url_fails',
        side_effect=side_effect,
    ):
        results = diagnostics.try_connectivity()
    assert results[0].endswith(': Error')
    assert results[1].endswith(': OK')
    assert results[2].endswith(': Error')
    assert results[3].endswith(': OK')


# ---------------------------------------------------------------------------
# Display power — the translation layer over lib/cec.py.
#
# These assert the *legacy wire values* the v2 System Info API has always
# exposed (``display_power`` is ``string | null``, with 'True'/'False'
# alongside diagnostic strings). The mechanism underneath changed
# completely when libcec was dropped for the kernel CEC uABI; the values
# deliberately did not, because Anthias never breaks a published API.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('status', 'expected'),
    [
        (cec.PowerStatus.ON, True),
        (cec.PowerStatus.STANDBY, False),
        (cec.PowerStatus.NO_ADAPTER, 'No CEC adapter'),
        (cec.PowerStatus.NO_LINK, 'No CEC display detected'),
        (cec.PowerStatus.NO_PEER, 'No CEC display detected'),
        (cec.PowerStatus.UNKNOWN, 'Mixed'),
        (cec.PowerStatus.ERROR, 'CEC error'),
    ],
)
def test_get_display_power_maps_status_to_legacy_value(
    status: 'cec.PowerStatus', expected: Any
) -> None:
    with mock.patch.object(cec_client, 'power_status', return_value=status):
        assert diagnostics.get_display_power() == expected


def test_get_display_power_no_peer_is_not_reported_as_an_error() -> None:
    """A plain monitor with no CEC support is the common case, not a
    fault. Reporting it as 'CEC error' is what made GH #3267
    unactionable for operators."""
    with mock.patch.object(
        cec_client, 'power_status', return_value=cec.PowerStatus.NO_PEER
    ):
        assert diagnostics.get_display_power() == 'No CEC display detected'


def test_get_display_power_survives_an_unreachable_viewer() -> None:
    """The viewer owns the CEC hardware. If it does not answer we know
    nothing about the display, and that really is a fault — distinct from
    a display that answered "no peer"."""
    with mock.patch.object(
        cec_client,
        'power_status',
        side_effect=cec_client.ViewerUnavailableError('no answer'),
    ):
        assert diagnostics.get_display_power() == 'CEC error'


def test_set_display_power_reports_every_display_it_reached() -> None:
    """Fan-out is the point: a device can have two monitors attached and
    the operator needs to know both got the message."""
    with mock.patch.object(cec_client, 'set_power', return_value=(2, 2)):
        ok, msg = diagnostics.set_display_power(on=False)
    assert ok is True
    assert 'off' in msg
    assert '2 displays' in msg


def test_set_display_power_singular_message_for_one_display() -> None:
    with mock.patch.object(cec_client, 'set_power', return_value=(1, 1)):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is True
    assert '1 display.' in msg


def test_set_display_power_partial_success_is_reported() -> None:
    """Two displays attached, only one answered — that is a success with
    a caveat, not a silent win."""
    with mock.patch.object(cec_client, 'set_power', return_value=(1, 2)):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is True
    assert '1 of 2' in msg


def test_set_display_power_unacknowledged_is_a_failure() -> None:
    with mock.patch.object(cec_client, 'set_power', return_value=(0, 1)):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'not acknowledged' in msg


def test_set_display_power_without_a_live_link_says_so() -> None:
    with mock.patch.object(cec_client, 'set_power', return_value=(0, 0)):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'no CEC link' in msg


def test_set_display_power_surfaces_adapter_errors() -> None:
    with mock.patch.object(
        cec_client,
        'set_power',
        side_effect=cec_client.ViewerUnavailableError('viewer did not answer'),
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'viewer did not answer' in msg


def test_cec_available_reads_the_fact_the_viewer_published() -> None:
    with mock.patch.object(cec_client, 'available', return_value=True):
        assert diagnostics.cec_available() is True
    with mock.patch.object(cec_client, 'available', return_value=False):
        assert diagnostics.cec_available() is False


def test_cec_available_is_false_before_the_viewer_reports() -> None:
    """A missing key means the viewer has not published yet, which must
    read as "no adapter" so the controls stay hidden rather than
    appearing and then failing."""
    client = mock.MagicMock()
    client.get.return_value = None
    with mock.patch(
        'anthias_server.lib.cec_client.connect_to_redis', return_value=client
    ):
        assert diagnostics.cec_available() is False
