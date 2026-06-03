"""Unit tests for the balenaOS track logic in bin/balena_fleet_maintenance.py.

The roller targets the ESR track by default; these cover the CalVer-vs-semver
discrimination and the HUP-reachability pre-filter that decides which devices
are even candidates for an ESR target.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / 'bin'
    / 'balena_fleet_maintenance.py'
)
_spec = importlib.util.spec_from_file_location(
    'balena_fleet_maintenance', _SCRIPT
)
fm = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve the module by name.
sys.modules[_spec.name] = fm
_spec.loader.exec_module(fm)


@pytest.mark.parametrize(
    'version,expected',
    [
        ('2024.1.0', True),
        ('2025.7.0', True),
        ('2026.1.0', True),
        ('balenaOS 2026.1.0', True),
        ('7.2.0', False),
        ('6.12.3+rev4', False),
        ('5.1.54', False),
        ('balenaOS 2.113.4', False),
    ],
)
def test_is_calver(version, expected):
    assert fm.is_calver(version) is expected


@pytest.mark.parametrize(
    'current',
    ['balenaOS 2.113.4', '5.1.54', '5.3.22', '6.0.36', '6.1.24+rev2'],
)
def test_old_classic_lines_can_reach_esr(current):
    # The genuinely old (2.x .. 6.1) lines are valid HUP sources for ESR.
    assert fm.unreachable_for_target(current, '2025.7.0') is False


@pytest.mark.parametrize(
    'current',
    ['2026.1.0', 'balenaOS 2026.1.0', '2025.7.0'],
)
def test_calver_devices_are_at_or_ahead_of_esr_target(current):
    # Already on the ESR/CalVer track -> no downward/sideways HUP path.
    assert fm.unreachable_for_target(current, '2025.7.0') is True


def test_regular_seven_cannot_reach_esr():
    assert fm.unreachable_for_target('7.2.0', '2025.7.0') is True


def test_no_filter_for_regular_target():
    # raspberrypi2 falls back to a regular target; the ESR pre-filter must
    # not suppress an otherwise-eligible device there.
    assert fm.unreachable_for_target('5.1.54', '5.1.20') is False
    assert fm.unreachable_for_target('2.113.4', '5.1.20') is False
