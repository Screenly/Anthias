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

    The measured problem with ``subprocess.run(..., capture_output=True,
    timeout=N)`` is that a **pipe the child's descendants still hold
    open makes it burn the entire timeout**, even when the direct child
    exited immediately. `run()` drains stdout/stderr via
    ``communicate()``, so a surviving grandchild holding the inherited
    write end keeps the read blocked until the deadline.

    Measured on three testbeds (armhf, arm64 and x86_64), fast-exiting
    child with a grandchild holding stdout: **0.07-0.12s here versus
    8.0s for ``subprocess.run``**. On a task that runs every 5 minutes
    against a 30s soft limit, repeatedly spending the whole budget for
    nothing is the real cost.

    Two honest caveats, because the first version of this comment
    overstated the mechanism and three boards refuted it:

      * The theory that ``SoftTimeLimitExceeded`` re-enters an unbounded
        ``wait()`` and lets the task "sail past the hard limit" does
        **not** reproduce on CPython 3.13, which calls
        ``process.kill()`` before every ``wait()``. Overshoot measured
        0.00s for both implementations.
      * The genuinely unbounded case needs an *uninterruptible*
        (D-state) child. One was produced on the arm64 board with
        O_DIRECT writes to the SD card (98.7% D, wchan
        ``mmc_blk_rw_wait``) and this helper still reaped it in 31.5ms,
        because SIGKILL lands as soon as the task returns from the
        block-layer wait. A reap that actually overruns
        ``_REAP_GRACE_S`` needs a driver that never returns.

    So this is a robustness and latency fix (Sentry ANTHIAS-A / 9 / B /
    31, GH #3264), not a proven cure for the hard-limit SIGKILL — the
    root cause of those events is still open.

    Three deliberate differences from ``subprocess.run``:

      * **``start_new_session=True``** puts the child in its own
        process group, so the kill reaches any grandchild it spawned
        rather than only the direct child.
      * **output goes to a temp file, not a pipe.** With no pipe there
        is nothing to drain, so reaping is a plain ``wait()`` and a
        surviving grandchild holding the write end cannot stall us.
      * **every wait has a timeout.** If the process group is somehow
        still alive after ``_REAP_GRACE_S`` (uninterruptible D-state,
        e.g. a wedged ioctl), we return rather than block. A leaked
        zombie is vastly cheaper than a SIGKILLed worker.

    On zombies, precisely: only the *direct* child is reaped, by the
    ``wait()`` above. A killed **grandchild** is not, and PID 1 in the
    celery container is the celery worker rather than an init that
    reaps — so such a zombie persists for the container's lifetime.
    That is acceptable here because the CEC helper scripts spawn no
    grandchildren (libcec uses threads, not child processes), which was
    confirmed on the testbeds: 25 consecutive real invocations left
    zero zombies and no fd growth. Anything that *does* spawn
    grandchildren should not use this helper without adding a reaper
    (``docker --init`` or an explicit wrapper).

    Two further limits of a process-group kill, measured on the arm64
    testbed and also unreachable from the CEC scripts, but worth knowing
    before reusing this helper:

      * a grandchild that calls ``setpgid(0, 0)`` leaves the group and
        survives the ``killpg``. The reap stays bounded; the escapee
        just outlives it.
      * a child that double-forks and exits *quickly* leaves its orphan
        running, because no timeout fires and so no ``killpg`` runs at
        all.

    Note the Popen is deliberately **not** used as a context manager:
    ``Popen.__exit__``'s unbounded ``wait()`` is one of the very
    hazards this helper exists to avoid.

    stdout and stderr go to *separate* temp files rather than being
    merged: the CEC helper scripts' contract is that stdout carries
    exactly one sentinel token, and libcec is prone to writing chatter
    to stderr, so merging them would corrupt the token.

    Returns ``(stdout, stderr, returncode)`` on a completed run, or
    ``None`` for **either** failure mode — the child had to be killed,
    *or* the fork/exec never happened (out of memory, missing
    interpreter). Callers must not read ``None`` as "timed out"
    specifically; both mean "no answer", which is all the CEC callers
    need to distinguish. (Copilot review of #3264.)
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
        # No answer. Overwhelmingly this means libcec hung and the probe
        # was killed — verified on the vchiq-only Pi 3 A+, where that is
        # the normal outcome on every tick rather than an exceptional
        # one. It also covers the fork/exec never happening at all (out
        # of memory), which is rarer and has larger symptoms of its own;
        # 'unresponsive' is a fair description of both from the
        # operator's side.
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
        # As in get_display_power: either the probe was killed after
        # hanging, or the fork/exec never happened. Both are "no answer"
        # to the operator.
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
