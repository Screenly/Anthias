#!/usr/bin/env python

import os
import subprocess
import sys
from datetime import datetime

from anthias_common import device_helper, utils

# Re-exported from ``anthias_common.version`` (the ``as`` form marks
# this as an explicit re-export so it stays importable from the old
# diagnostics path without a lint suppression). Layer-agnostic code
# imports it from ``anthias_common.version`` directly.
from anthias_common.version import get_anthias_release as get_anthias_release


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
    _done('CEC error')
try:
    _done('True' if tv.is_on() else 'False')
except IOError:
    _done('Unknown')
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


def get_display_power() -> str | bool:
    """
    Queries the TV using CEC.

    The CEC stack can block inside libcec (no HDMI link, TV asleep,
    adapter unresponsive) in a C call that ignores Python signals,
    which would tie up the celery worker until it hits its hard
    time_limit and gets SIGKILL'd. Run the query in a subprocess so
    we can enforce a timeout and recover cleanly.
    """
    try:
        result = subprocess.run(
            [sys.executable, '-c', _CEC_QUERY_SCRIPT],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return 'CEC error'

    output = result.stdout.decode('utf-8', errors='replace').strip()
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
    try:
        result = subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return (
            False,
            f'Display turn-{verb} timed out — CEC adapter unresponsive.',
        )

    output = result.stdout.decode('utf-8', errors='replace').strip()
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
    stderr = result.stderr.decode('utf-8', errors='replace').strip()
    detail = (
        stderr or output
    ) or f'subprocess exited with status {result.returncode}'
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
    return (
        os.path.exists('/dev/cec0')
        or os.path.exists('/dev/cec1')
        or os.path.exists('/dev/vchiq')
    )


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
            result.append('{}: Error'.format(url))
        else:
            result.append('{}: OK'.format(url))
    return result


def get_utc_isodate() -> str:
    return datetime.isoformat(datetime.utcnow())


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
