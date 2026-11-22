"""
Ordering and recovery guarantees inside bin/upgrade_containers.sh.

The script has no unit harness: it drives `docker compose` against a
real daemon from end to end. What *can* be checked cheaply is the shape
of its steps, and two of them have already broken a device in the
field, so each is worth a guard that fails loudly if anyone takes it
back out.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)
SCRIPT = os.path.join(REPO_ROOT, 'bin', 'upgrade_containers.sh')

# The `up -d` calls that recreate the stack, and the early exit for a
# pending host reboot. There are two bring-ups: the normal one, and the
# retry behind the stale-port-reservation recovery. The first is a line
# continuation feeding `tee`, so a trailing backslash is allowed.
BRINGS_STACK_UP = re.compile(
    r'^\s*sudo -E docker compose .* up -d --remove-orphans\s*\\?\s*$'
)
REBOOT_GATE = re.compile(r'^\s*if \[\[? -f /var/run/reboot-required')


@pytest.fixture
def lines() -> list[str]:
    with open(SCRIPT) as f:
        return f.read().splitlines()


def _indices(lines: list[str], pattern: re.Pattern[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if pattern.match(line)]


def _only_index(lines: list[str], pattern: re.Pattern[str]) -> int:
    found = _indices(lines, pattern)
    assert len(found) == 1, f'expected exactly one match, got {found}'
    return found[0]


def test_stack_is_up_before_the_script_defers_to_a_reboot(
    lines: list[str],
) -> None:
    """A pending reboot must not leave the device with no containers.

    install.sh takes the stack down with `down --remove-orphans` before
    the host work, so by the time this script runs the containers do not
    exist. `restart: always` cannot bring back a container that was
    removed, so exiting above the `up -d` leaves nothing running, this
    script still exits 0, and the reboot does not repair it. That is a
    dark screen after a routine upgrade, which is how it was found:
    https://forums.screenly.io/t/sd-card-completely-full/6778
    """
    ups = _indices(lines, BRINGS_STACK_UP)
    gate = _only_index(lines, REBOOT_GATE)

    assert ups, 'no `up -d --remove-orphans` found'
    assert gate > max(ups), (
        'the /var/run/reboot-required exit sits above a `up -d` '
        f'(line {gate + 1} vs {max(ups) + 1}). A device whose '
        'dist-upgrade asked for a reboot would be left with no '
        'containers at all.'
    )


def test_reboot_gate_still_skips_the_image_purge(lines: list[str]) -> None:
    """Deferring the purge is the part worth keeping.

    Reclaiming superseded images is the one step there is a reason to
    skip when the host is half-upgraded and about to go down, so the
    gate belongs between the bring-up and the purge rather than at the
    very end.

    Only the full sweep counts here. The pull's failure path also calls
    the purge script, with `--untagged-only`, and exits well above the
    bring-up; that narrower reclaim is meant to run early, so it is not
    something the gate has to sit above.
    """
    gate = _only_index(lines, REBOOT_GATE)
    purge = [
        i
        for i, line in enumerate(lines)
        if 'PURGE_SCRIPT' in line
        and 'bash' in line
        and '--untagged-only' not in line
    ]

    assert purge, 'no call to the full stale-image purge found'
    assert gate < min(purge), (
        'the reboot gate should still sit above the stale-image purge'
    )


def test_stale_port_reservation_is_retried(lines: list[str]) -> None:
    """A stale host-port reservation must not end the upgrade.

    libnetwork keeps host-port reservations in its own store, and an
    endpoint that was never torn down cleanly (daemon killed
    mid-upgrade, board browned out while the stack was running) leaves
    its reservation behind. The next bring-up then collides with its own
    ghost and `up -d` fails with "port is already allocated" while
    nothing is listening on that port. Taking this project down releases
    the endpoints and drops the reservation, so one retry recovers it:
    https://forums.screenly.io/t/6688
    """
    source = '\n'.join(lines)

    assert 'port is already allocated' in source, (
        'nothing detects the stale-reservation failure, so an upgrade '
        'that hits it aborts with a bare docker error'
    )

    ups = _indices(lines, BRINGS_STACK_UP)
    assert len(ups) == 2, (
        f'expected a bring-up and one retry, found {len(ups)} at {ups}'
    )

    recovery_down = [
        i for i, line in enumerate(lines) if 'down --remove-orphans' in line
    ]
    assert recovery_down, 'the recovery never takes the project down'
    assert ups[0] < min(recovery_down) < ups[1], (
        'the recovery `down` must sit between the failed bring-up and '
        f'the retry (got {ups[0] + 1}, {min(recovery_down) + 1}, '
        f'{ups[1] + 1})'
    )


def test_recovery_does_not_delete_the_libnetwork_store(
    lines: list[str],
) -> None:
    """Never "fix" the stale reservation by removing local-kv.db.

    That is the workaround circulating in the forum thread. The file is
    the daemon's only record of every user-defined network on the host,
    so deleting it strands the running containers and leaves the web UI
    unreachable until the whole stack is rebuilt. The scoped `down`
    above clears the same reservation without touching anything else on
    the device.
    """
    offending = [
        line
        for line in lines
        if 'local-kv.db' in line and not line.lstrip().startswith('#')
    ]

    assert not offending, (
        "the script touches libnetwork's store outside a comment, "
        'which risks dropping every user-defined network on the host: '
        f'{offending}'
    )


def _port_check_script(lines: list[str]) -> str:
    """The port-check block, from WEB_PORTS to its call, as a script."""
    start = next(
        i for i, line in enumerate(lines) if line == 'WEB_PORTS=(443 8443)'
    )
    end = next(i for i, line in enumerate(lines) if line == 'check_web_ports')
    return '\n'.join(lines[start : end + 1])


def _run_port_check(
    lines: list[str],
    tmp_path: Path,
    *,
    listening: dict[int, str],
    ours: set[int],
) -> subprocess.CompletedProcess[str]:
    stubs = tmp_path
    # `sudo CMD...` just runs CMD (dropping -E).
    (stubs / 'sudo').write_text(
        '#!/bin/bash\n[ "$1" = -E ] && shift\nexec "$@"\n'
    )
    cases = '\n'.join(
        f'  *":{port}"*) echo {owner!r} ;;'
        for port, owner in listening.items()
    )
    (stubs / 'ss').write_text(f'#!/bin/bash\ncase "$*" in\n{cases}\nesac\n')
    held = ' '.join(str(p) for p in ours)
    (stubs / 'docker').write_text(
        '#!/bin/bash\n'
        f'for p in {held}; do\n'
        '  [ "${@: -1}" = "$p" ] && [ "${@: -3:1}" = port ] && exit 0\n'
        'done\nexit 1\n'
    )
    for stub in ('sudo', 'ss', 'docker'):
        (stubs / stub).chmod(0o755)
    script = 'COMPOSE_FILES=(-f x.yml)\n' + _port_check_script(lines)
    return subprocess.run(
        ['bash', '-c', script],
        env={'PATH': f'{stubs}:/usr/bin:/bin'},
        capture_output=True,
        text=True,
        check=False,
    )


def test_upgrade_warns_when_another_service_holds_a_web_port(
    lines: list[str], tmp_path: Path
) -> None:
    result = _run_port_check(
        lines,
        tmp_path,
        listening={443: 'LISTEN 0 511 *:443 users:(("nginx",pid=1))'},
        ours=set(),
    )

    # A warning, never a failure: the bring-up reports the real error.
    assert result.returncode == 0
    assert 'host port 443 is already in use' in result.stderr
    assert 'nginx' in result.stderr
    assert '8443' not in result.stderr.split('already in use')[0]


def test_upgrade_ignores_ports_held_by_its_own_caddy(
    lines: list[str], tmp_path: Path
) -> None:
    result = _run_port_check(
        lines,
        tmp_path,
        listening={
            443: 'LISTEN 0 4096 *:443 users:(("docker-proxy",pid=2))',
            8443: 'LISTEN 0 4096 *:8443 users:(("docker-proxy",pid=3))',
        },
        ours={443, 8443},
    )

    assert result.returncode == 0
    assert result.stderr == ''


def test_upgrade_checks_the_web_ports_before_the_bring_up(
    lines: list[str],
) -> None:
    check = _only_index(lines, re.compile(r'^check_web_ports$'))
    assert check < min(_indices(lines, BRINGS_STACK_UP))
