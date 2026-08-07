import os
import sys
import tempfile
import time
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
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=('True', '', 0)
    ):
        assert diagnostics.get_display_power() is True


def test_get_display_power_false() -> None:
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=('False', '', 0)
    ):
        assert diagnostics.get_display_power() is False


def test_get_display_power_cec_error() -> None:
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=('CEC error', '', 0)
    ):
        assert diagnostics.get_display_power() == 'CEC error'


def test_get_display_power_unknown() -> None:
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=('Unknown', '', 0)
    ):
        assert diagnostics.get_display_power() == 'Unknown'


def test_get_display_power_empty_output_returns_cec_error() -> None:
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=('', '', 0)
    ):
        assert diagnostics.get_display_power() == 'CEC error'


def test_set_display_power_on_success() -> None:
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=('OK', '', 0)
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is True
    assert 'on' in msg


def test_set_display_power_off_success() -> None:
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=('OK', '', 0)
    ):
        ok, msg = diagnostics.set_display_power(on=False)
    assert ok is True
    assert 'off' in msg


def test_set_display_power_cec_error_passes_through_reason() -> None:
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=('ERROR: no adapter', '', 0)
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'no adapter' in msg


def test_set_display_power_timeout_returns_failure_message() -> None:
    with mock.patch.object(diagnostics, '_run_bounded', return_value=None):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'timed out' in msg.lower()


def test_set_display_power_unexpected_stdout_falls_through_to_stdout() -> None:
    """No 'OK' / 'ERROR:' sentinel — the helper still has to return
    something actionable. With non-empty stdout and a clean exit, that
    becomes the raw line itself (capped)."""
    stderr_text = (b'').decode()
    with mock.patch.object(
        diagnostics,
        '_run_bounded',
        return_value=('something weird', stderr_text, 0),
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'something weird' in msg


def test_set_display_power_subprocess_crash_surfaces_stderr() -> None:
    """When stdout is empty and stderr has content (interpreter crash,
    libcec writing to stderr), the last line of stderr is what reaches
    the toast — gives the operator a real reason instead of a generic
    'unexpected response.'"""
    stderr_text = (
        'Traceback (most recent call last):\n'
        '  File "<string>", line 4, in <module>\n'
        'RuntimeError: cec init failed: no adapter\n'
    )
    with mock.patch.object(
        diagnostics,
        '_run_bounded',
        return_value=('', stderr_text, 1),
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'RuntimeError: cec init failed: no adapter' in msg


def test_set_display_power_subprocess_crash_with_empty_streams_reports_status() -> (
    None
):
    """Last-resort fallback: subprocess exits non-zero with no stderr
    and no stdout. Still has to report something — surface the returncode."""
    stderr_text = (b'').decode()
    with mock.patch.object(
        diagnostics,
        '_run_bounded',
        return_value=('', stderr_text, 137),
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert '137' in msg


def test_set_display_power_caps_long_error_message() -> None:
    """libcec can spew kilobytes of diagnostic output; the toast / API
    body must not carry an unbounded blob."""
    stderr_text = (('X' * 4000).encode()).decode()
    with mock.patch.object(
        diagnostics,
        '_run_bounded',
        return_value=('', stderr_text, 1),
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    # Cap is 240; message has prefix "Display turn-on failed: " so total
    # is under ~280 chars and ends with the ellipsis sentinel.
    assert len(msg) < 300
    assert msg.endswith('...')


def test_set_display_power_caps_long_error_sentinel_reason() -> None:
    """The ERROR: sentinel branch must apply the same length cap +
    last-line trim as the unexpected-stdout fallback; a hostile or
    chatty libcec build could otherwise smuggle a multi-line / huge
    string into the toast via the contract path."""
    long_reason = 'X' * 4000
    with mock.patch.object(
        diagnostics,
        '_run_bounded',
        return_value=(f'ERROR: {long_reason}', '', 0),
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert len(msg) < 300
    assert msg.endswith('...')


def test_set_display_power_error_sentinel_strips_multiline() -> None:
    """Multi-line reason on the ERROR: branch — we keep only the last
    non-empty line so the toast stays one row tall."""
    stderr_text = (b'').decode()
    with mock.patch.object(
        diagnostics,
        '_run_bounded',
        return_value=(
            'ERROR: first line\nmiddle line\nactual failure reason',
            stderr_text,
            0,
        ),
    ):
        ok, msg = diagnostics.set_display_power(on=True)
    assert ok is False
    assert 'actual failure reason' in msg
    assert 'first line' not in msg
    assert 'middle line' not in msg


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
    """A timeout is 'the adapter hung', which is a different state from
    'libcec raised'. Verified on the vchiq-only Pi 3 A+, where cec.init()
    hangs on every tick rather than raising — so reporting this as a
    generic 'CEC error' left the operator unable to tell 'no hardware'
    from 'hardware wedged' (GH #3267)."""
    with mock.patch.object(diagnostics, '_run_bounded', return_value=None):
        assert diagnostics.get_display_power() == 'CEC adapter unresponsive'


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
# _run_bounded — the bounded-reap fix for GH #3264
# ---------------------------------------------------------------------------


def test_run_bounded_returns_stdout_stderr_and_status() -> None:
    """Happy path: separate streams, both captured, real returncode."""
    argv = [
        sys.executable,
        '-c',
        "import sys; sys.stdout.write('out'); sys.stderr.write('err')",
    ]
    result = diagnostics._run_bounded(argv, timeout=30)
    assert result is not None
    stdout, stderr, returncode = result
    assert stdout == 'out'
    assert stderr == 'err'
    assert returncode == 0


def test_run_bounded_keeps_streams_separate() -> None:
    """The CEC scripts' contract is that stdout carries exactly one
    sentinel token, and libcec is prone to chattering on stderr —
    merging the two would corrupt the token."""
    argv = [
        sys.executable,
        '-c',
        "import sys; sys.stdout.write('True'); sys.stderr.write('libcec noise')",
    ]
    result = diagnostics._run_bounded(argv, timeout=30)
    assert result is not None
    assert result[0] == 'True', 'stderr must not leak into the token'


def test_run_bounded_kills_a_hanging_child_within_its_budget() -> None:
    """A child that ignores everything must not outlast the budget.

    This is the regression that matters: the old ``subprocess.run``
    reaped with an *unbounded* ``wait()`` on the timeout path, so celery's
    single soft-limit signal could not save the worker and the 60s hard
    limit SIGKILLed it (Sentry ANTHIAS-A/9/B/31)."""
    argv = [sys.executable, '-c', 'import time; time.sleep(60)']
    start = time.monotonic()
    result = diagnostics._run_bounded(argv, timeout=1)
    elapsed = time.monotonic() - start
    assert result is None, 'a killed child must report as None'
    assert elapsed < 1 + diagnostics._REAP_GRACE_S + 3, (
        f'took {elapsed:.1f}s — the reap is not bounded'
    )


def test_run_bounded_kills_the_whole_process_group() -> None:
    """``start_new_session=True`` + killpg means a grandchild cannot
    survive the kill, nor stall the reap by holding a handle.

    The child spawns a grandchild that would outlive it, writes the
    grandchild's pid, then hangs. After the timeout, neither should be
    alive."""
    with tempfile.NamedTemporaryFile('w+') as pidfile:
        # The grandchild's pid goes to a file rather than stdout, because
        # _run_bounded deliberately discards output when it has to kill.
        child_script = (
            'import subprocess, sys, time\n'
            "grandchild = subprocess.Popen([sys.executable, '-c',"
            " 'import time; time.sleep(60)'])\n"
            f'open({pidfile.name!r}, "w").write(str(grandchild.pid))\n'
            'time.sleep(60)\n'
        )
        argv = [sys.executable, '-c', child_script]
        assert diagnostics._run_bounded(argv, timeout=2) is None
        pidfile.seek(0)
        raw = pidfile.read().strip()
    assert raw, 'child never reported its grandchild pid'
    grandchild = int(raw)
    # Give the group kill a moment to be reaped by init.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f'grandchild {grandchild} survived the process-group kill')


def test_run_bounded_handles_unspawnable_argv() -> None:
    """Fork/exec failing (no such binary) must return None, not raise —
    this runs inside an upload request and a celery beat."""
    assert diagnostics._run_bounded(['/nonexistent/binary'], timeout=5) is None


# ---------------------------------------------------------------------------
# CEC status strings — GH #3267
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'sentinel',
    ['No CEC adapter', 'No CEC display detected'],
)
def test_get_display_power_passes_through_distinct_cec_states(
    sentinel: str,
) -> None:
    """'CEC error' used to cover three different situations, and reported
    the most common one — a plain monitor with no CEC support — to the
    operator as a fault. The distinct states must survive to the caller."""
    with mock.patch.object(
        diagnostics, '_run_bounded', return_value=(sentinel, '', 0)
    ):
        assert diagnostics.get_display_power() == sentinel
