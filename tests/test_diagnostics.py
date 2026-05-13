import os
import subprocess
from typing import Any
from unittest import mock

import pytest

from anthias_server.lib import diagnostics


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


def test_get_display_power_true() -> None:
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b'True'
    with mock.patch.object(subprocess, 'run', return_value=completed):
        assert diagnostics.get_display_power() is True


def test_get_display_power_false() -> None:
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b'False'
    with mock.patch.object(subprocess, 'run', return_value=completed):
        assert diagnostics.get_display_power() is False


def test_get_display_power_cec_error() -> None:
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b'CEC error'
    with mock.patch.object(subprocess, 'run', return_value=completed):
        assert diagnostics.get_display_power() == 'CEC error'


def test_get_display_power_unknown() -> None:
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b'Unknown'
    with mock.patch.object(subprocess, 'run', return_value=completed):
        assert diagnostics.get_display_power() == 'Unknown'


def test_get_display_power_empty_output_returns_cec_error() -> None:
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b''
    with mock.patch.object(subprocess, 'run', return_value=completed):
        assert diagnostics.get_display_power() == 'CEC error'


def test_set_display_power_on_success() -> None:
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b'OK'
    with mock.patch.object(subprocess, 'run', return_value=completed):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is True
    assert 'on' in msg


def test_set_display_power_off_success() -> None:
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b'OK'
    with mock.patch.object(subprocess, 'run', return_value=completed):
        ok, msg = diagnostics.set_display_power(on=False)
    assert ok is True
    assert 'off' in msg


def test_set_display_power_cec_error_passes_through_reason() -> None:
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b'ERROR: no adapter'
    with mock.patch.object(subprocess, 'run', return_value=completed):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'no adapter' in msg


def test_set_display_power_timeout_returns_failure_message() -> None:
    with mock.patch.object(
        subprocess,
        'run',
        side_effect=subprocess.TimeoutExpired(cmd='python', timeout=10),
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'timed out' in msg.lower()


def test_set_display_power_unexpected_stdout_falls_through_to_stdout() -> None:
    """No 'OK' / 'ERROR:' sentinel — the helper still has to return
    something actionable. With non-empty stdout and a clean exit, that
    becomes the raw line itself (capped)."""
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b'something weird'
    completed.stderr = b''
    completed.returncode = 0
    with mock.patch.object(subprocess, 'run', return_value=completed):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'something weird' in msg


def test_set_display_power_subprocess_crash_surfaces_stderr() -> None:
    """When stdout is empty and stderr has content (interpreter crash,
    libcec writing to stderr), the last line of stderr is what reaches
    the toast — gives the operator a real reason instead of a generic
    'unexpected response.'"""
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b''
    completed.stderr = (
        b'Traceback (most recent call last):\n'
        b'  File "<string>", line 4, in <module>\n'
        b'RuntimeError: cec init failed: no adapter\n'
    )
    completed.returncode = 1
    with mock.patch.object(subprocess, 'run', return_value=completed):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'RuntimeError: cec init failed: no adapter' in msg


def test_set_display_power_subprocess_crash_with_empty_streams_reports_status() -> (
    None
):
    """Last-resort fallback: subprocess exits non-zero with no stderr
    and no stdout. Still has to report something — surface the returncode."""
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b''
    completed.stderr = b''
    completed.returncode = 137
    with mock.patch.object(subprocess, 'run', return_value=completed):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert '137' in msg


def test_set_display_power_caps_long_error_message() -> None:
    """libcec can spew kilobytes of diagnostic output; the toast / API
    body must not carry an unbounded blob."""
    completed = mock.MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = b''
    completed.stderr = ('X' * 4000).encode()
    completed.returncode = 1
    with mock.patch.object(subprocess, 'run', return_value=completed):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    # Cap is 240; message has prefix "Display turn-on failed: " so total
    # is under ~280 chars and ends with the ellipsis sentinel.
    assert len(msg) < 300
    assert msg.endswith('...')


def test_cec_available_true_when_cec0_present() -> None:
    with mock.patch.object(
        os.path, 'exists', side_effect=lambda p: p == '/dev/cec0'
    ):
        assert diagnostics.cec_available() is True


def test_cec_available_true_when_vchiq_present() -> None:
    with mock.patch.object(
        os.path, 'exists', side_effect=lambda p: p == '/dev/vchiq'
    ):
        assert diagnostics.cec_available() is True


def test_cec_available_false_when_neither_present() -> None:
    with mock.patch.object(os.path, 'exists', return_value=False):
        assert diagnostics.cec_available() is False


def test_get_display_power_subprocess_timeout() -> None:
    with mock.patch.object(
        subprocess,
        'run',
        side_effect=subprocess.TimeoutExpired(cmd='cec', timeout=10),
    ):
        assert diagnostics.get_display_power() == 'CEC error'


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
