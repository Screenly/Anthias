"""
Every container Anthias ships must refuse to write a core dump.

The device's card is the thing at stake, not the dump. A crashing
process writes `core.<pid>` into its working directory, which for our
images is `WORKDIR /usr/src/app`: inside the container, on no mount, and
reachable by no cleanup we have. `docker images` does not list it and
`docker image prune` cannot touch a live container's writable layer, so
the space goes missing with nothing to show for it. A device in the
field accumulated 24.2 GB that way (issue 3417).

These assertions are over the compose templates rather than a running
stack because that is where the limit is declared, and because the
failure they guard against is someone adding a sixth service and not
knowing this rule exists.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)

TEMPLATES = (
    'docker-compose.yml.tmpl',
    'docker-compose.balena.yml.tmpl',
    'docker-compose.balena.dev.yml.tmpl',
)

BALENA_TEMPLATES = tuple(t for t in TEMPLATES if '.balena.' in t)

# The value that disables dumps differs by deployment. Docker Compose
# takes `core: 0` as written. `balena deploy` does not: its compose
# parser drops every zero-valued ulimit field, so both `core: 0` and
# `{soft: 0, hard: 0}` reach the release as `core: {}`, which the
# supervisor never sees as matching the running container and
# recreates forever (2026.11.0, issues 3475 and 3477). A limit of 1
# byte survives the parser and writes no core either: it is below the
# size of any ELF core, and the kernel treats exactly 1 as "skip" for
# a piped core_pattern too.
EXPECTED_CORE_LIMIT = {t: 1 if t in BALENA_TEMPLATES else 0 for t in TEMPLATES}


Service = dict[str, object]


def _services(template: str) -> dict[str, Service]:
    with open(os.path.join(REPO_ROOT, template)) as f:
        document = yaml.safe_load(f)
    services: dict[str, Service] = document['services']
    return services


@pytest.mark.parametrize('template', TEMPLATES)
def test_every_service_disables_core_dumps(template: str) -> None:
    offenders = {
        name: service.get('ulimits')
        for name, service in _services(template).items()
        if not isinstance(limits := service.get('ulimits'), dict)
        or limits.get('core') != EXPECTED_CORE_LIMIT[template]
    }

    assert not offenders, (
        f'{template}: these services would write core dumps into their '
        f'own writable layer: {sorted(offenders)}. Add '
        f'`ulimits: *no-core-dumps` to each.'
    )


@pytest.mark.parametrize('template', TEMPLATES)
def test_core_limit_is_shared_rather_than_retyped(template: str) -> None:
    """One anchor, aliased everywhere else.

    Five copies of the core limit would drift the moment someone edits one of
    them, and the YAML alias is what makes "every service" checkable by
    reading a single line.
    """
    with open(os.path.join(REPO_ROOT, template)) as f:
        body = f.read()

    assert body.count('ulimits: &no-core-dumps') == 1, (
        f'{template}: expected exactly one anchor definition'
    )
    assert body.count('ulimits: *no-core-dumps') == (
        len(_services(template)) - 1
    ), f'{template}: every service but the anchor should alias it'


def test_templates_agree_on_which_services_exist() -> None:
    """The balena templates are easy to update in isolation and forget.

    If they ever diverge, the parametrised checks above still pass on
    each file separately while one deployment quietly loses a service's
    protection, so compare them directly.
    """
    by_template = {t: sorted(_services(t)) for t in TEMPLATES}
    distinct = {tuple(v) for v in by_template.values()}

    assert len(distinct) == 1, (
        f'compose templates list different services: {by_template}'
    )


def _zero_fields(name: str, limit: object) -> list[str]:
    if isinstance(limit, dict):
        return [
            f'{name}.{field}' for field, value in limit.items() if value == 0
        ]
    return [name] if limit == 0 else []


@pytest.mark.parametrize('template', BALENA_TEMPLATES)
def test_balena_templates_have_no_zero_valued_ulimit(template: str) -> None:
    """balena's compose parser deletes zeros, whatever the limit.

    This is wider than the core limit on purpose: a future
    `nofile: {soft: 0, ...}` would be mangled the same way, and the
    symptom is a device that never starts its services rather than a
    failed deploy, so it has to be caught here.
    """
    offenders = {
        service_name: zeros
        for service_name, service in _services(template).items()
        if isinstance(limits := service.get('ulimits'), dict)
        and (
            zeros := [
                z
                for name, limit in limits.items()
                for z in _zero_fields(name, limit)
            ]
        )
    }

    assert not offenders, (
        f'{template}: balena-compose-parser drops zero-valued ulimit '
        f'fields, which leaves the supervisor recreating these services '
        f'forever: {offenders}'
    )
