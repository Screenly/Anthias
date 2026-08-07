#!/usr/bin/env python

import os
import signal
import subprocess
import sys
import tempfile
from datetime import UTC, datetime

from anthias_common import device_helper, utils

# Re-exported from ``anthias_common.version`` (the ``as`` form marks
# this as an explicit re-export so it stays importable from the old
# diagnostics path without a lint suppression). Layer-agnostic code
# imports it from ``anthias_common.version`` directly.
from anthias_common.version import (
    get_anthias_release as get_anthias_release,  # noqa: PLC0414
)

# Never let this probe reach normal interpreter teardown. On hardware
# without a usable CEC adapter (e.g. Raspberry Pi 5) libcec's adapter
# thread aborts as it is torn down ("FATAL: exception not rethrown",
# SIGABRT), which dumps a multi-MB core every run and eventually fills
# the disk. The answer is already on stdout by then, so the helper
# flushes and os._exit(0)s to skip Python/libcec teardown entirely.
_CEC_QUERY_SCRIPT = """
import os
import sys


def _done(text):
    sys.stdout.write(text)
    sys.stdout.flush()
    os._exit(0)


try:
    import cec
    cec.init()
    tv = cec.Device(cec.CECDEVICE_TV)
except Exception:
    # libcec could not open an adapter at all. On a mainline-KMS Pi the
    # container only gets /dev/vchiq, which libcec cannot use, so this
    # is the normal answer there rather than a fault (GH #3267).
    _done('No CEC adapter')
try:
    _done('True' if tv.is_on() else 'False')
except (IOError, OSError):
    # The adapter works but no peer answered — the expected state for a
    # plain monitor with no CEC support. Reported distinctly so the
    # operator is not told their hardware is broken.
    _done('No CEC display detected')
"""

# Issued from the settings page / REST endpoint, *not* from a celery
# worker, so a hung libcec call would block the request thread until
# the subprocess timeout fires. Same subprocess+timeout shape as
# `_CEC_QUERY_SCRIPT` for the same reason: libcec C calls don't
# honour Python signals. Same os._exit(0) on the way out, too, to
# avoid the teardown abort + core dump described above.
_CEC_SET_SCRIPT = """
import os
import sys


def _done(text):
    sys.stdout.write(text)
    sys.stdout.flush()
    os._exit(0)


try:
    import cec
    cec.init()
    tv = cec.Device(cec.CECDEVICE_TV)
except Exception as exc:
    _done('ERROR: ' + (str(exc) or 'CEC stack unavailable'))
try:
    if {on}:
        tv.power_on()
    else:
        tv.standby()
    _done('OK')
except Exception as exc:
    _done('ERROR: ' + (str(exc) or 'CEC command failed'))
"""


# Wall-clock budget for one CEC subprocess, and the extra grace we
# allow for the kill to land before giving up on reaping at all.
_CEC_TIMEOUT_S = 10
_REAP_GRACE_S = 2


def _run_bounded(argv: list[str], timeout: int) -> tuple[str, str, int] | None:
    """Run ``argv`` without ever blocking much past ``timeout``.

    ``subprocess.run(..., capture_output=True, timeout=N)`` looks like
    it does this and does not. Two of its wait paths are unbounded:

      * ``except TimeoutExpired:`` does ``process.kill()`` then a bare
        ``process.wait()`` with no timeout;
      * its bare ``except:`` defers to ``Popen.__exit__``, which also
        ends in an unbounded ``self.wait()``.

    Celery raises its soft time limit **once**. That signal interrupts
    the first ``waitpid``, but unwinding then re-enters an unbounded
    wait with no second signal coming, so the task sails past the hard
    limit and the worker is SIGKILLed — taking asset normalisation,
    downloads and the cleanup sweep with it. That is the mechanism
    behind Sentry ANTHIAS-A / 9 / B / 31 (GH #3264), which kept firing
    on builds that already carried the ``SoftTimeLimitExceeded`` guard
    from #3063 precisely because the guard cannot help if the
    interpreter never regains control.

    Three deliberate differences from ``subprocess.run``:

      * **``start_new_session=True``** puts the child in its own
        process group, so the kill reaches any grandchild it spawned
        rather than only the direct child.
      * **output goes to a temp file, not a pipe.** With no pipe there
        is nothing to drain, so reaping is a plain ``wait()`` and a
        surviving grandchild holding the write end cannot stall us.
      * **every wait has a timeout.** If the process group is somehow
        still alive after ``_REAP_GRACE_S`` (uninterruptible D-state,
        e.g. a wedged ioctl), we return and let init reap the orphan.
        A leaked zombie is vastly cheaper than a SIGKILLed worker.

    Note the Popen is deliberately **not** used as a context manager:
    ``Popen.__exit__``'s unbounded ``wait()`` is one of the very
    hazards this helper exists to avoid.

    stdout and stderr go to *separate* temp files rather than being
    merged: the CEC helper scripts' contract is that stdout carries
    exactly one sentinel token, and libcec is prone to writing chatter
    to stderr, so merging them would corrupt the token.

    Returns ``(stdout, stderr, returncode)``, or ``None`` if the child
    had to be killed.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(
                argv,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
        except OSError:
            # Fork/exec itself failed (out of memory, no interpreter).
            return None
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            try:
                process.wait(timeout=_REAP_GRACE_S)
            except subprocess.TimeoutExpired:
                # Unkillable for now. Walk away rather than block.
                pass
            return None
        out.seek(0)
        err.seek(0)
        return (
            out.read().decode('utf-8', errors='replace').strip(),
            err.read().decode('utf-8', errors='replace').strip(),
            process.returncode,
        )


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """SIGKILL the child's whole process group, falling back to the child.

    ``start_new_session=True`` made the child a group leader, so its pid
    is also its process-group id and one ``killpg`` reaches everything it
    spawned. The fallbacks cover the child already having exited
    (``ProcessLookupError``) and the group being unsignalable
    (``PermissionError``).
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass


def get_display_power() -> str | bool:
    """Query the attached display's power state over CEC.

    The CEC stack can block inside libcec (no HDMI link, TV asleep,
    adapter unresponsive) in a C call that ignores Python signals, so
    the query runs in a subprocess we can bound — see
    ``_run_bounded`` for why ``subprocess.run`` is not sufficient.

    Return values are deliberately distinct, because "CEC error" used
    to cover three very different situations and reported the most
    common one to the operator as a fault (GH #3267). A probe cannot
    predict CEC usability — that depends on the *peer* display, which
    is not observable from ``/dev`` — so the states are only
    distinguishable by actually asking:

      * ``'No CEC adapter'`` — libcec *raised* because it found no
        usable adapter.
      * ``'No CEC display detected'`` — the adapter works but nothing
        answered. This is the expected state for a plain monitor
        without CEC support, which is a large share of signage
        installs. Not an error.
      * ``'CEC adapter unresponsive'`` — libcec neither answered nor
        raised within the timeout, so we killed it. Measured on the
        vchiq-only Pi 3 A+: ``cec.init()`` simply **hangs** there
        rather than raising, so the subprocess always hits the bound.
        This is a distinct state from a raise, and reporting it as a
        generic error is what made #3267 unactionable — an operator
        cannot tell "no hardware" from "hardware wedged".
      * ``'CEC error'`` — genuinely unexpected.
      * ``True`` / ``False`` — a real answer from a real peer.

    Note the hang case is the *common* one on Pi 1-4, where the
    container is handed ``/dev/vchiq`` and never ``/dev/cec0``. Those
    boards therefore burn the full ``_CEC_TIMEOUT_S`` on every beat
    tick. Skipping the doomed probe entirely needs a reliable way to
    tell "vchiq is usable here" from "vchiq is vestigial", which is not
    yet established across the fleet — see #3267.

    The ``str | bool`` return and the ``True``/``False`` values are
    deliberately left as they were. They are the *data* the v2 System
    Info API surfaces for ``display_power`` (its caller in
    ``celery_tasks`` coerces with ``str()`` because redis-py rejects a
    bool — Sentry ANTHIAS-2C), so changing them would be a
    field-semantics change for external clients. #3267 is about the
    error strings misreporting a working setup as a fault, not about
    renaming the values.
    """
    completed = _run_bounded(
        [sys.executable, '-c', _CEC_QUERY_SCRIPT], _CEC_TIMEOUT_S
    )
    if completed is None:
        # Timed out and was killed — libcec hung rather than raising.
        # Verified on the vchiq-only Pi 3 A+, where this is the normal
        # outcome on every tick, not an exceptional one.
        return 'CEC adapter unresponsive'
    output = completed[0]
    if output == 'True':
        return True
    if output == 'False':
        return False
    return output or 'CEC error'


def set_display_power(on: bool) -> tuple[bool, str]:
    """Send a CEC power_on / standby to the connected TV.

    Returns ``(ok, message)`` for direct surfacing to the operator as
    a toast. Stays synchronous on purpose — the issue brief asks for
    an immediate feedback loop so failed CEC commands aren't silent.
    """
    script = _CEC_SET_SCRIPT.format(on='True' if on else 'False')
    verb = 'on' if on else 'off'
    # Same bounded-reap treatment as the query path: this one runs on the
    # request thread, so an unbounded wait would hang the operator's HTTP
    # request rather than a celery worker (GH #3264).
    completed = _run_bounded([sys.executable, '-c', script], _CEC_TIMEOUT_S)
    if completed is None:
        return (
            False,
            f'Display turn-{verb} timed out — CEC adapter unresponsive.',
        )

    output, stderr, returncode = completed
    if output == 'OK':
        return True, f'Display turn-{verb} command sent.'
    if output.startswith('ERROR: '):
        return False, (
            f'Display turn-{verb} failed: '
            f'{_trim_cec_detail(output[len("ERROR: ") :])}'
        )

    # Subprocess didn't emit one of the two contract sentinels. The
    # likely causes are an interpreter crash (returncode != 0) or
    # libcec writing its diagnostic to stderr instead of stdout — both
    # would surface as "unexpected CEC response." without further
    # detail, which is useless to an operator. Fall back to stderr (or
    # the raw stdout if non-empty) so the toast / API response carries
    # something actionable.
    detail = (
        stderr or output
    ) or f'subprocess exited with status {returncode}'
    return False, f'Display turn-{verb} failed: {_trim_cec_detail(detail)}'


def _trim_cec_detail(detail: str) -> str:
    """Sanitize an arbitrarily-sized libcec / Python error blob into a
    one-line, length-capped toast / JSON message.

    libcec (and the in-subprocess Python) can emit multi-line tracebacks
    or kilobyte-scale diagnostics on either stdout or stderr. The last
    non-empty line is almost always the actual exception/error message,
    so we keep that and drop the rest, then cap to 240 chars so the toast
    stack doesn't overflow and JSON responses stay small.
    """
    lines = [line for line in detail.splitlines() if line.strip()]
    one_line = lines[-1].strip() if lines else detail.strip()
    if len(one_line) > 240:
        one_line = one_line[:237] + '...'
    return one_line


def cec_available() -> bool:
    """Cheap render-time gate for whether to show CEC controls.

    Probes only for the device nodes libcec consumes — `/dev/cec0`
    on mainline kernels (Pi 5, x86 USB adapters when exposed) and
    `/dev/vchiq` on Pi 1-4 (currently the only one passed into the
    server container by `docker-compose.yml.tmpl`). A positive result
    means the adapter *could* work, not that it will: the actual
    success/failure is surfaced by ``set_display_power``'s toast.
    """
    return os.path.exists('/dev/cec0') or os.path.exists('/dev/vchiq')


def get_uptime() -> float:
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])

    return uptime_seconds


def get_load_avg() -> dict[str, float]:
    """
    Returns load average rounded to two digits.
    """

    load_avg: dict[str, float] = {}
    get_load_avg = os.getloadavg()

    load_avg['1 min'] = round(get_load_avg[0], 2)
    load_avg['5 min'] = round(get_load_avg[1], 2)
    load_avg['15 min'] = round(get_load_avg[2], 2)

    return load_avg


def get_git_branch() -> str | None:
    return os.getenv('GIT_BRANCH')


def get_git_short_hash() -> str | None:
    return os.getenv('GIT_SHORT_HASH')


def get_git_hash() -> str | None:
    return os.getenv('GIT_HASH')


# Treat both as the project's release line — `master` is upstream's
# convention; `main` is the GitHub default for forks. Either resolves
# to "no branch suffix on the version label".
_RELEASE_BRANCHES = frozenset({'master', 'main'})


def get_anthias_version_head() -> str:
    """The primary version line — ``v{calver}``. Returns ``''`` only
    when ``get_anthias_release()`` finds neither the installed package
    metadata nor the repo-root pyproject.toml (i.e. the running code
    is detached from both its install record and its source tree —
    in practice, never on a real device or CI runner)."""
    release = get_anthias_release()
    return f'v{release}' if release else ''


def get_anthias_version_meta() -> str:
    """The de-emphasised git-meta line — ``(short_hash[, branch])``
    when the env vars are present, empty otherwise. Branch is
    suppressed on master/main since operators don't need to be told
    they're on the release line.

    Rendered on its own row under the version head in the System Info
    template, in a smaller, muted font.
    """
    short_hash = get_git_short_hash()
    branch = get_git_branch()
    parts: list[str] = []
    if short_hash:
        parts.append(short_hash)
    if branch and branch not in _RELEASE_BRANCHES:
        parts.append(branch)
    return f'({", ".join(parts)})' if parts else ''


def get_anthias_version() -> str:
    """The combined label, used by the v2 info API so external clients
    get a single human-readable string.

    Format:
      - on master/main:   ``v2026.5.0 (08c26f3)``
      - on a feature/PR branch: ``v2026.5.0 (08c26f3, vanilla-django)``
      - if either piece is missing (e.g. host run with no GIT_BRANCH
        env var):
            * just release:       ``v2026.5.0``
            * just git, no release: ``(08c26f3)`` / ``(08c26f3, branch)``

    Replaces the old ``{branch}@{hash}`` shape so the operator sees a
    real release number first instead of "vanilla-django@08c26f3".
    """
    head = get_anthias_version_head()
    meta = get_anthias_version_meta()
    return f'{head} {meta}'.strip() if head and meta else (head or meta)


def try_connectivity() -> list[str]:
    urls = [
        'http://www.google.com',
        'http://www.bbc.co.uk',
        'https://www.google.com',
        'https://www.bbc.co.uk',
    ]
    result = []
    for url in urls:
        if utils.url_fails(url):
            result.append(f'{url}: Error')
        else:
            result.append(f'{url}: OK')
    return result


def get_utc_isodate() -> str:
    return datetime.now(UTC).isoformat()


def get_debian_version() -> str:
    debian_version = '/etc/debian_version'
    if os.path.isfile(debian_version):
        with open(debian_version, 'r') as f:
            for line in f:
                return str(line).strip()
        return 'Unable to get Debian version.'
    else:
        return 'Unable to get Debian version.'


def get_raspberry_code() -> int | str:
    return device_helper.parse_cpu_info().get('hardware', 'Unknown')


def get_raspberry_model() -> int | str:
    return device_helper.parse_cpu_info().get('model', 'Unknown')
