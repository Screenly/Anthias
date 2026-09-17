"""Unit tests for the read-only audit phase of balena_unpin_devices.

The script is a stdlib-only CLI under bin/ rather than a package
module, so it is loaded by path. Everything exercised here is pure or
has its single network call stubbed — no balena token required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / 'bin' / 'balena_unpin_devices.py'
)


@pytest.fixture(scope='module')
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location('balena_unpin', SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def device(supervisor: str | None, online: bool = True) -> dict[str, Any]:
    return {
        'id': 1,
        'os_version': 'balenaOS 6.1.24+rev4',
        'supervisor_version': supervisor,
        'is_online': online,
    }


@pytest.mark.parametrize(
    ('supervisor', 'expected'),
    [
        ('19.1.4', True),
        ('19.0.0', True),
        ('v19.0.0', True),
        ('20.0.0', True),
        ('18.9.9', False),
        ('16.5.0', False),
        ('', False),
        (None, False),
    ],
)
def test_self_update_capable_threshold(
    script: ModuleType, supervisor: str | None, expected: bool
) -> None:
    """v19 is the cutoff: it is the first supervisor that can retrieve a
    queued host OS update without the cloud pushing it."""
    assert script.self_update_capable(device(supervisor)) is expected


def test_self_update_capable_missing_field(script: ModuleType) -> None:
    """A device that has never reported a supervisor version counts as
    not ready rather than blowing up."""
    assert script.self_update_capable({'id': 1}) is False


def test_supervisor_major_ignores_rev_suffix(script: ModuleType) -> None:
    assert script.supervisor_major(device('19.1.4+rev2')) == 19


def test_percent_handles_empty_fleet(script: ModuleType) -> None:
    assert script.percent(0, 0) == 'n/a'
    assert script.percent(1, 4) == '25.0%'


def test_report_versions_buckets_missing_as_unknown(
    script: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    script.report_versions('supervisor version', ['19.1.4', '', '19.1.4'])
    out = capsys.readouterr().out
    assert '2 x 19.1.4' in out
    assert '1 x unknown' in out


def test_report_versions_collapses_long_tail(
    script: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fleet spread over many point releases must not bury the
    readiness number under a wall of histogram rows."""
    versions = [f'6.1.{n}' for n in range(script.HISTOGRAM_CAP + 5)]
    script.report_versions('balenaOS version', versions)
    out = capsys.readouterr().out
    assert out.count(' x ') == script.HISTOGRAM_CAP
    assert '... and 5 more balenaOS version(s)' in out


def test_run_audit_phase_accumulates_totals(
    script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    devices = [
        device('19.1.4'),
        device('18.0.0'),
        device('16.5.0', online=False),
        device(None, online=False),
    ]
    monkeypatch.setattr(
        script, 'list_fleet_inventory', lambda _token, _fleet: devices
    )
    totals = {'audit_total': 0, 'audit_online': 0, 'audit_ready': 0}
    script.run_audit_phase('token', 'screenly_ose/anthias-pi4', totals)
    assert totals == {
        'audit_total': 4,
        'audit_online': 2,
        'audit_ready': 1,
    }
    out = capsys.readouterr().out
    assert 'devices=4 online=2 self-update-capable=1 (25.0%)' in out


def test_run_audit_phase_handles_empty_fleet(
    script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        script, 'list_fleet_inventory', lambda _token, _fleet: []
    )
    totals = {'audit_total': 0, 'audit_online': 0, 'audit_ready': 0}
    script.run_audit_phase('token', 'screenly_ose/anthias-pi2', totals)
    assert totals['audit_total'] == 0
    out = capsys.readouterr().out
    assert 'devices=0 online=0 self-update-capable=0 (n/a)' in out
    assert 'by balenaOS version' not in out


def test_audit_never_selects_device_identifiers(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workflow logs are world-readable, so the audit must not even
    fetch uuids — there is no per-device output mode to gate."""
    captured: dict[str, Any] = {}

    def fake_api_request(
        _token: str,
        _method: str,
        _resource: str,
        params: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.update(params or {})
        return {'d': []}

    monkeypatch.setattr(script, 'api_request', fake_api_request)
    script.list_fleet_inventory('token', 'screenly_ose/anthias-pi4')
    assert 'uuid' not in captured['$select']
