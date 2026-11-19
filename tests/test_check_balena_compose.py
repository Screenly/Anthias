"""
bin/check_balena_compose.py gates `balena deploy` on what balena's own
compose parser makes of our file.

The inputs below are the parser's actual outputs, captured from
balena-compose-parser 0.2.1 (the version balena-cli 25.1.3 ships)
against the 2026.11.0 template and the fixed one.
"""

import importlib.util
import os
from types import ModuleType

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)


def _load() -> ModuleType:
    path = os.path.join(REPO_ROOT, 'bin', 'check_balena_compose.py')
    spec = importlib.util.spec_from_file_location('check_balena_compose', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


def _composition(**ulimits: object) -> dict[str, object]:
    return {'services': {'redis': {'image': 'x', 'ulimits': ulimits}}}


def test_the_2026_11_0_composition_is_rejected() -> None:
    # `core: 0` and `core: {soft: 0, hard: 0}` both parse to this.
    problems = check.malformed_ulimits(_composition(core={}))

    assert problems == ['redis: ulimits.core parsed as {}']


@pytest.mark.parametrize(
    'limit',
    [
        1,
        0,
        {'soft': 1024, 'hard': 2048},
    ],
)
def test_shapes_the_supervisor_can_compare_pass(limit: object) -> None:
    assert check.malformed_ulimits(_composition(core=limit)) == []


@pytest.mark.parametrize(
    'limit',
    [
        {'hard': 1},  # `{soft: 0, hard: 1}` after the parser
        {'soft': 1},
        {'soft': '1', 'hard': 1},
        True,
        '1',
    ],
)
def test_partial_or_mistyped_limits_are_rejected(limit: object) -> None:
    assert check.malformed_ulimits(_composition(nofile=limit))


def test_services_without_ulimits_are_fine() -> None:
    composition = {'services': {'redis': {'image': 'x'}}}

    assert check.malformed_ulimits(composition) == []


def test_missing_services_mapping_is_reported() -> None:
    assert check.malformed_ulimits({}) == [
        'the parsed composition has no services mapping'
    ]
