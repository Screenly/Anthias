"""
The balena compose templates may only use what balena supports.

balena is not Docker Compose. Its compose support "is currently based on
version 2.4", and "any fields that were introduced in version 3 are not
supported" (balena docs, "Docker Compose fields" reference). Several v2
fields are unsupported or restricted as well. What balena does with
something it does not support is the dangerous part: it does not always
refuse the deploy. 2026.11.0 shipped `ulimits: core: 0`, a field balena
lists as supported, and balena's parser silently stored it as
`core: {}`. Every device then sat in a container-recreate loop with no
web UI (issues 3475 and 3477).

So the balena templates are held to an allowlist rather than a denylist.
Adding a key to one of them means checking it against balena's field
reference AND running the rendered file through the parser
(bin/check_balena_compose.py does that before every deploy) before
adding it here. A Compose v3 / Compose Specification habit picked up
from docker-compose.yml.tmpl (`deploy:`, long-form `depends_on`,
long-syntax volumes, `configs`, `secrets`, ...) fails here instead of on
a device.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)

BALENA_TEMPLATES = (
    'docker-compose.balena.yml.tmpl',
    'docker-compose.balena.dev.yml.tmpl',
)

# Every key here is listed as supported in balena's compose field
# reference, and is in use in a balena template today.
ALLOWED_SERVICE_KEYS = frozenset(
    {
        'build',
        'command',
        'depends_on',
        'environment',
        'image',
        'labels',
        'network_mode',
        'ports',
        'privileged',
        'restart',
        'shm_size',
        'ulimits',
        'volumes',
    }
)

ALLOWED_TOP_LEVEL_KEYS = frozenset({'version', 'services', 'volumes'})

# balena: "Only support `bridge`, `host`, `service:[service name]`, or
# none".
ALLOWED_NETWORK_MODES = ('bridge', 'host')

Document = dict[str, object]


def _load(template: str) -> Document:
    with open(os.path.join(REPO_ROOT, template)) as f:
        document: Document = yaml.safe_load(f)
    return document


def _services(template: str) -> dict[str, dict[str, object]]:
    services = _load(template)['services']
    assert isinstance(services, dict)
    return services


@pytest.mark.parametrize('template', BALENA_TEMPLATES)
def test_declares_a_v2_file_format(template: str) -> None:
    version = str(_load(template).get('version', ''))

    assert version.startswith('2'), (
        f'{template}: balena supports compose file format 2.x only '
        f'(based on 2.4); found version {version!r}'
    )


@pytest.mark.parametrize('template', BALENA_TEMPLATES)
def test_uses_only_allowed_top_level_keys(template: str) -> None:
    unexpected = sorted(set(_load(template)) - ALLOWED_TOP_LEVEL_KEYS)

    assert not unexpected, (
        f'{template}: top-level keys {unexpected} are not on the balena '
        f'allowlist; see this module docstring before adding them'
    )


@pytest.mark.parametrize('template', BALENA_TEMPLATES)
def test_services_use_only_allowed_keys(template: str) -> None:
    unexpected = {
        name: sorted(set(service) - ALLOWED_SERVICE_KEYS)
        for name, service in _services(template).items()
        if set(service) - ALLOWED_SERVICE_KEYS
    }

    assert not unexpected, (
        f'{template}: service keys not on the balena allowlist: '
        f'{unexpected}. balena supports compose 2.4 fields only, and '
        f'not all of them; see this module docstring.'
    )


@pytest.mark.parametrize('template', BALENA_TEMPLATES)
def test_depends_on_is_the_array_form(template: str) -> None:
    # balena: "Only array form and `service_started` condition".
    offenders = sorted(
        name
        for name, service in _services(template).items()
        if 'depends_on' in service
        and not isinstance(service['depends_on'], list)
    )

    assert not offenders, (
        f'{template}: depends_on must be a list of service names on '
        f'balena: {offenders}'
    )


@pytest.mark.parametrize('template', BALENA_TEMPLATES)
def test_volumes_use_the_short_syntax(template: str) -> None:
    # balena: "Only support short syntax and named volumes".
    offenders = {
        name: volume
        for name, service in _services(template).items()
        if isinstance(volumes := service.get('volumes'), list)
        for volume in volumes
        if not isinstance(volume, str)
    }

    assert not offenders, (
        f'{template}: long-syntax volumes are not supported by balena: '
        f'{offenders}'
    )


@pytest.mark.parametrize('template', BALENA_TEMPLATES)
def test_network_mode_is_one_balena_accepts(template: str) -> None:
    offenders = {
        name: mode
        for name, service in _services(template).items()
        if (mode := service.get('network_mode')) is not None
        and mode not in ALLOWED_NETWORK_MODES
        and not str(mode).startswith('service:')
    }

    assert not offenders, (
        f'{template}: network_mode values balena does not support: {offenders}'
    )
