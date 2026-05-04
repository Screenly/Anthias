#!/usr/bin/env python

import os
import subprocess
import sys
from datetime import datetime

from anthias_common import device_helper, utils


_CEC_QUERY_SCRIPT = """
import sys
try:
    import cec
    cec.init()
    tv = cec.Device(cec.CECDEVICE_TV)
except Exception:
    sys.stdout.write('CEC error')
    sys.exit(0)
try:
    sys.stdout.write('True' if tv.is_on() else 'False')
except IOError:
    sys.stdout.write('Unknown')
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


def get_anthias_release() -> str:
    """Read the project version, sourced from pyproject.toml's
    [project].version (currently CalVer ``YYYY.M.MICRO``).

    Resolution order:
      1. ``importlib.metadata.version('anthias')`` — works for
         editable installs (``pip install -e .``) and any path where
         the project ships as a wheel.
      2. Fallback: parse ``pyproject.toml`` directly with ``tomllib``.
         Required because every production / test / host environment
         runs ``uv sync --no-install-project`` (see
         docker/uv-builder.j2, docker/Dockerfile.{server,test,viewer},
         bin/install.sh) — that flag installs the project's deps but
         NOT the project itself, so importlib.metadata has no record
         of an ``anthias`` distribution to read.

    Cached after first successful read so the System Info HTML render
    (called per request) and the v2 info API don't re-open the file.
    Returns the empty string only when both sources fail.
    """
    cached: str | None = getattr(get_anthias_release, '_cached', None)
    if cached is not None:
        return cached
    value: str
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            value = version('anthias')
        except PackageNotFoundError:
            value = _read_version_from_pyproject()
    except Exception:
        value = ''
    setattr(get_anthias_release, '_cached', value)
    return value


def _read_version_from_pyproject() -> str:
    """Last-ditch source for the project version when the package
    isn't installed in the active venv. Walks up from this file to
    find the repo-root pyproject.toml — `__file__` lives at
    ``src/anthias_server/lib/diagnostics.py``, so parents[3] is the
    checkout root in both editable installs and the
    ``uv sync --no-install-project`` Docker layout."""
    try:
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[3] / 'pyproject.toml'
        with pyproject.open('rb') as f:
            data = tomllib.load(f)
        return str(data.get('project', {}).get('version', ''))
    except Exception:
        return ''


def get_anthias_version_head() -> str:
    """The primary version line — ``v{calver}``. Empty when the
    package isn't installed (host scripts without `uv sync`)."""
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
