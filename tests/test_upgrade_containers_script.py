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
    """
    gate = _only_index(lines, REBOOT_GATE)
    purge = [
        i
        for i, line in enumerate(lines)
        if 'PURGE_SCRIPT' in line and 'bash' in line
    ]

    assert purge, 'no call to the purge script found'
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
