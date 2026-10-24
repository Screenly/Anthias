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
        or limits.get('core') != 0
    }

    assert not offenders, (
        f'{template}: these services would write core dumps into their '
        f'own writable layer: {sorted(offenders)}. Add '
        f'`ulimits: *no-core-dumps` to each.'
    )


@pytest.mark.parametrize('template', TEMPLATES)
def test_core_limit_is_shared_rather_than_retyped(template: str) -> None:
    """One anchor, aliased everywhere else.

    Five copies of `core: 0` would drift the moment someone edits one of
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
