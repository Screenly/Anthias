"""The /_design/ page, and the page-against-token-layer drift guard.

Two jobs.

The first is cheap breadth. One request renders every component in every
variant, so a template that stops compiling fails here in milliseconds
instead of surfacing in a Playwright run or on a device.

The second is the one that matters. A design-system page is only worth
having if it is true, and the way it stops being true is silent: someone
adds a role to @theme, does not add it to the catalogue, and the page now
describes a system slightly smaller than the real one. Nothing renders
wrong, nobody notices, and the page's authority decays a token at a time.
So the catalogue is checked against @theme in BOTH directions, and the
documented values are checked against the declared ones.
"""

from __future__ import annotations

import importlib
import re

import pytest
from django.test import Client, override_settings
from django.urls import clear_url_caches, reverse

from anthias_server.app import design_system
from tests.test_design_tokens import SASS, theme_block

URLS_MODULE = 'anthias_server.app.urls'

# Roles that exist for hand-written CSS rather than for a swatch, and
# would be noise on a page whose colour section is read as "the palette
# you may choose from". Each needs a reason, which is the point of
# listing them here rather than filtering by prefix.
NOT_SWATCHED: dict[str, str] = {}


def _colour_roles() -> set[str]:
    """Every --color-* role in @theme, minus the namespace reset."""
    return {
        name.removeprefix('--color-')
        for name in theme_block()
        if name.startswith('--color-') and not name.endswith('*')
    }


def _catalogued() -> set[str]:
    return {
        token
        for _, _, tokens in design_system.COLOUR_GROUPS
        for token in tokens
    }


@pytest.fixture
def client() -> Client:
    return Client()


# ---------------------------------------------------------------------------
# The page renders


@pytest.mark.django_db
def test_page_renders(client: Client) -> None:
    """A single request compiles every component in the library."""
    response = client.get(reverse('anthias_app:design_system'))
    assert response.status_code == 200


@pytest.mark.django_db
def test_every_section_is_present(client: Client) -> None:
    """Each catalogue reaches the page, rather than silently rendering
    an empty section because a context key was renamed."""
    body = client.get(reverse('anthias_app:design_system')).content.decode()
    for anchor in (
        'colour',
        'type',
        'spacing',
        'radii',
        'elevation',
        'layering',
        'breakpoints',
        'buttons',
        'surfaces',
        'forms',
        'status',
        'feedback',
    ):
        assert f'id="{anchor}"' in body, f'section {anchor} missing'


@pytest.mark.django_db
def test_every_colour_role_reaches_the_markup(client: Client) -> None:
    """The swatch loop emits a var() per role.

    Guards the loop itself: a template edit that broke the swatch would
    still leave every section heading in place and still return 200.
    """
    body = client.get(reverse('anthias_app:design_system')).content.decode()
    missing = [
        token
        for token in sorted(_catalogued())
        if f'var(--color-{token})' not in body
    ]
    assert not missing, f'catalogued but never rendered: {missing}'


# ---------------------------------------------------------------------------
# The page does not drift from the token layer


def test_catalogue_covers_every_colour_role() -> None:
    """Every role in @theme appears on the page."""
    missing = sorted(_colour_roles() - _catalogued() - set(NOT_SWATCHED))
    assert not missing, (
        'Colour roles exist in @theme but are absent from the design '
        'page, so the page now describes a smaller system than the one '
        'that ships. Add each to a group in app/design_system.py, or to '
        'NOT_SWATCHED with a reason:\n  ' + '\n  '.join(missing)
    )


def test_catalogue_invents_no_colour_role() -> None:
    """And the page documents nothing that does not exist.

    The failure this catches is a swatch whose token was renamed or
    deleted: it renders as a transparent chip under a confident label,
    which is worse than not documenting the role at all.
    """
    unknown = sorted(_catalogued() - _colour_roles())
    assert not unknown, (
        'The design page documents colour roles that @theme does not '
        'declare. Each renders as an empty swatch:\n  ' + '\n  '.join(unknown)
    )


@pytest.mark.parametrize(
    ('catalogue', 'prefix'),
    [
        (design_system.TYPE_SCALE, '--text-'),
        (design_system.RADII, '--radius-'),
        (design_system.BREAKPOINTS, '--breakpoint-'),
    ],
)
def test_documented_values_match_the_tokens(
    catalogue: list[tuple[str, ...]], prefix: str
) -> None:
    """The page's stated value is the declared value.

    This is the failure the whole branch turned on: the radii section
    said 0.25rem for years' worth of confidence while the browser
    painted 4px, because a second stylesheet was quietly winning.
    """
    tokens = theme_block()
    wrong = []
    for entry in catalogue:
        name, documented = entry[0], entry[1]
        declared = tokens.get(f'{prefix}{name}')
        if declared is None:
            wrong.append(f'{prefix}{name} is not declared in @theme')
        elif declared != documented:
            wrong.append(
                f'{prefix}{name}: page says {documented}, '
                f'@theme says {declared}'
            )
    assert not wrong, '\n  '.join(['Design page is out of date:', *wrong])


def test_documented_spacing_matches_the_scale() -> None:
    """Each spacing step resolves to the rem value the page prints."""
    base = 0.25  # --spacing, Tailwind's default and our base unit.
    wrong = [
        f'step {step}: page says {documented}, '
        f'calc(var(--spacing) * {step}) is {base * int(step)}rem'
        for step, documented in design_system.SPACING
        if float(documented.removesuffix('rem')) != base * int(step)
    ]
    assert not wrong, '\n  '.join(['Spacing scale is out of date:', *wrong])


def test_documented_breakpoint_pixels_match_the_rem_values() -> None:
    """The px column is what the rem column means at a 16px root."""
    wrong = [
        f'{name}: {rem} is {float(rem.removesuffix("rem")) * 16:.0f}px, '
        f'not {px}'
        for name, rem, px in design_system.BREAKPOINTS
        if float(rem.removesuffix('rem')) * 16 != float(px.removesuffix('px'))
    ]
    assert not wrong, '\n  '.join(['Breakpoint table is wrong:', *wrong])


def test_documented_button_variants_exist() -> None:
    """Every documented variant is a class the stylesheet defines."""
    scss = (SASS / '_styles.scss').read_text()
    missing = [
        cls
        for cls, _ in design_system.BUTTON_VARIANTS
        if not re.search(rf'\.{re.escape(cls)}\b', scss)
    ]
    assert not missing, f'Button variants documented but not styled: {missing}'


def test_documented_z_index_matches_base_css() -> None:
    """The stacking scale is declared in base.css, not invented here."""
    from tests.test_design_tokens import STATIC, _declarations

    declared = _declarations((STATIC / 'css/base.css').read_text())
    wrong = [
        f'--z-{name}: page says {value}, base.css says '
        f'{declared.get(f"--z-{name}")}'
        for name, value, _ in design_system.Z_INDEX
        if declared.get(f'--z-{name}') != value
    ]
    assert not wrong, '\n  '.join(['Layering table is out of date:', *wrong])


# ---------------------------------------------------------------------------
# The route is dev-only


def test_route_is_absent_in_production() -> None:
    """A production image must not carry the page at all.

    Both flags have to be false together. DEBUG alone is not a usable
    gate here — pytest-django sets settings.DEBUG = False for the whole
    run, which is why urls.py checks IS_TEST as well, and why a test
    that only asserted the route exists would pass against a gate that
    was wrong in the other direction.
    """
    module = importlib.import_module(URLS_MODULE)
    try:
        with override_settings(DEBUG=False, IS_TEST=False):
            importlib.reload(module)
            names = {pattern.name for pattern in module.urlpatterns}
    finally:
        # Reload under the real settings and drop the resolver cache, so
        # the URLconf the rest of the suite sees is the registered one.
        importlib.reload(module)
        clear_url_caches()

    assert 'design_system' not in names
    # The reload is only meaningful if the module really does register
    # routes at import time; a no-op reload would make this vacuous.
    assert names, 'urls.py registered nothing at all'


@pytest.mark.django_db
def test_route_is_present_under_test(client: Client) -> None:
    """The other direction, so the gate cannot silently close on CI."""
    assert client.get('/_design/').status_code == 200
