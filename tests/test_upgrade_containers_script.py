"""
Ordering guarantees inside bin/upgrade_containers.sh.

The script has no unit harness: it drives `docker compose` against a
real daemon from end to end. What *can* be checked cheaply is the order
of its steps, and one ordering in particular has already broken a
device in the field, so it is worth a guard that fails loudly if anyone
moves it back.
"""

import os
import re

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)
SCRIPT = os.path.join(REPO_ROOT, 'bin', 'upgrade_containers.sh')

# The `up -d` that recreates the stack, and the early exit for a
# pending host reboot.
BRINGS_STACK_UP = re.compile(
    r'^\s*sudo -E docker compose .* up -d --remove-orphans\s*$'
)
REBOOT_GATE = re.compile(r'^\s*if \[\[? -f /var/run/reboot-required')


@pytest.fixture
def lines() -> list[str]:
    with open(SCRIPT) as f:
        return f.read().splitlines()


def _only_index(lines: list[str], pattern: re.Pattern[str]) -> int:
    found = [i for i, line in enumerate(lines) if pattern.match(line)]
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
    up = _only_index(lines, BRINGS_STACK_UP)
    gate = _only_index(lines, REBOOT_GATE)

    assert gate > up, (
        'the /var/run/reboot-required exit sits above the `up -d` '
        f'(line {gate + 1} vs {up + 1}). A device whose dist-upgrade '
        'asked for a reboot would be left with no containers at all.'
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
