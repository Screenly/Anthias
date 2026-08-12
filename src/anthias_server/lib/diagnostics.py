#!/usr/bin/env python

import os
from datetime import UTC, datetime

from anthias_common import device_helper, utils

# Re-exported from ``anthias_common.version`` (the ``as`` form marks
# this as an explicit re-export so it stays importable from the old
# diagnostics path without a lint suppression). Layer-agnostic code
# imports it from ``anthias_common.version`` directly.
from anthias_common.version import (
    get_anthias_release as get_anthias_release,  # noqa: PLC0414
)
from anthias_server.lib import cec

# Display power runs on the kernel CEC uABI (see ``lib/cec.py``), not
# libcec. The functions below are a thin translation layer: they exist
# only to keep the *wire* shape the v2 System Info API has always had.
#
# ``get_display_power`` returns ``str | bool`` and the v2 ``/info``
# endpoint exposes ``display_power`` as ``string | null``, so external
# clients already parse ``'True'``/``'False'`` alongside diagnostic
# strings. Anthias never breaks a published API version, so the values
# below are deliberately unchanged from the libcec era even though the
# mechanism underneath is completely different.

#: Adapter present but nothing reachable through it — either no CEC
#: link at all (nothing plugged in, or the sink's EDID carries no CEC
#: block) or a link with nothing that answers. Both are the normal
#: state for a plain desktop monitor, which is a large share of
#: signage installs, so neither is reported as a fault (GH #3267).
_NO_DISPLAY = 'No CEC display detected'

#: More than one display is attached and they disagree about power
#: state. Only reachable now that operations fan out across every
#: adapter instead of picking one.
_MIXED = 'Mixed'

_STATUS_TO_LEGACY: dict[cec.PowerStatus, str | bool] = {
    cec.PowerStatus.ON: True,
    cec.PowerStatus.STANDBY: False,
    cec.PowerStatus.NO_ADAPTER: 'No CEC adapter',
    cec.PowerStatus.NO_LINK: _NO_DISPLAY,
    cec.PowerStatus.NO_PEER: _NO_DISPLAY,
    cec.PowerStatus.UNKNOWN: _MIXED,
    # "We could not ask" — the node would not open, an ioctl failed, or
    # we could not claim a place on the bus. Kept distinct from
    # _NO_DISPLAY on purpose: only this one is a fault.
    cec.PowerStatus.ERROR: 'CEC error',
}


def get_display_power() -> str | bool:
    """Power state of the attached display(s), aggregated over every port.

    ``True``/``False`` mean every display that answered is on / in
    standby. Anything else is a diagnostic string — see
    ``_STATUS_TO_LEGACY``.

    Unlike the libcec implementation this replaced, there is no
    subprocess, no 10s timeout and no core-dump workaround: the kernel
    ioctls that back this answered in 0.0-0.1ms on every board in the
    testbed fleet, and a transmit to an unresponsive monitor NACKs in
    well under a second.
    """
    try:
        status = cec.power_status()
    except (OSError, cec.CecError, TimeoutError):
        return 'CEC error'
    return _STATUS_TO_LEGACY.get(status, 'CEC error')


def set_display_power(on: bool) -> tuple[bool, str]:
    """Turn the attached display(s) on or off over CEC.

    Fans out to **every** adapter with a live link rather than picking
    one. A device can have more than one display attached (two HDMI
    ports on a Pi 4/5, more on x86), and powering down only the first
    would leave the other lit.

    Returns ``(ok, message)`` for direct surfacing to the operator as a
    toast, and stays synchronous on purpose so a failed command is not
    silent.
    """
    verb = 'on' if on else 'off'
    try:
        acknowledged, attempted = cec.set_power(on)
    except (OSError, cec.CecError, TimeoutError) as exc:
        return False, f'Display turn-{verb} failed: {exc}'

    if not attempted:
        return False, (
            f'Display turn-{verb} failed: no CEC link on any HDMI port.'
        )
    if not acknowledged:
        # Transmitted fine, nothing acknowledged. Overwhelmingly this
        # means the attached display simply has no CEC support.
        return False, (
            f'Display turn-{verb} was not acknowledged — no CEC-capable '
            f'display responded.'
        )
    if acknowledged < attempted:
        return True, (
            f'Display turn-{verb} sent to {acknowledged} of {attempted} '
            f'displays; the rest did not respond.'
        )
    plural = 'display' if attempted == 1 else 'displays'
    return True, f'Display turn-{verb} command sent to {attempted} {plural}.'


def cec_available() -> bool:
    """Whether this device has any CEC adapter at all.

    Render-time gate for the settings controls and a fast-fail for the
    REST endpoint. It answers "is there hardware worth showing controls
    for", **not** "will CEC work here" — no device-node probe can answer
    the latter, because it depends on the peer display (GH #3267).

    Note this no longer counts ``/dev/vchiq``. That node was only ever
    meaningful to libcec, which is gone; on a mainline-KMS kernel libcec
    could not use it anyway (``No default adapter found``, measured at
    0.07s). Boards that are currently handed vchiq and nothing else
    therefore report "not available" and skip the doomed probe entirely,
    instead of burning a timeout on every beat tick.
    """
    return cec.available()


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
