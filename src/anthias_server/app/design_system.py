"""The design-system demo page.

One page rendering every design token and every UI component in every
variant. It is the reference for what the system contains, the place to
eyeball both themes side by side, and the cheapest test surface we have:
a single request exercises the whole component library, so a template
that fails to compile fails a fast unit test instead of surfacing in a
Playwright run or, worse, on a device.

Dev-only. The route is registered in urls.py behind ``settings.DEBUG``,
so production images (ENVIRONMENT=production, hence DEBUG=False) never
expose it. The guard lives at route registration rather than inside the
view so there is no way to reach it at all.

The catalogues below are data, not markup, so the template stays a
handful of loops and adding a token means adding one tuple.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

# Colour roles, grouped by the plane they belong to. Every entry is a
# token name; the template renders each as a swatch labelled with the
# name you would type to use it.
#
# Palette primitives (--color-plum-*, --color-ink-*) are deliberately
# absent: they are not reachable from markup and a component naming one
# has hardcoded a colour. See css/palette.css.
COLOUR_GROUPS: list[tuple[str, str, list[str]]] = [
    (
        'Canvas',
        (
            'The page behind everything. Brand plum in both themes, so '
            'text on it is always light.'
        ),
        [
            'canvas',
            'canvas-deep',
            'on-canvas',
            'on-canvas-muted',
            'on-canvas-faint',
        ],
    ),
    (
        'Chrome',
        (
            'Navbar and footer. Brand furniture; deepens in dark mode but '
            'never inverts. The scrim is the mobile nav drawer, which has '
            'to be near-opaque so page content cannot read through it.'
        ),
        [
            'chrome',
            'chrome-scrim',
            'on-chrome',
            'on-chrome-muted',
            'on-chrome-faint',
        ],
    ),
    (
        'Surface',
        (
            'Cards, modals, popovers. This is the plane that actually '
            'flips between themes.'
        ),
        [
            'surface',
            'surface-soft',
            'surface-tint',
            'surface-sunken',
            'fg',
            'fg-muted',
            'fg-faint',
            'border',
            'divider',
        ],
    ),
    (
        'Feature',
        (
            'The emphasised card: .surface--active, splash and login. '
            'Lighter than the canvas in both themes.'
        ),
        [
            'feature-from',
            'feature-to',
            'on-feature',
            'on-feature-muted',
            'on-feature-faint',
            'feature-divider',
        ],
    ),
    (
        'Accent',
        'Brand yellow. Primary actions and highlights.',
        [
            'accent',
            'accent-soft',
            'accent-strong',
            'accent-text',
            'accent-wash',
            'accent-edge',
            'accent-hover',
        ],
    ),
    (
        'Link',
        (
            'Anchors on a light surface. The brand yellow has far too '
            'little contrast against white to be a link colour.'
        ),
        ['link', 'link-hover', 'link-wash', 'link-edge', 'link-ring'],
    ),
    (
        'Danger',
        (
            'Split roles: --color-danger is ink, --color-danger-fill is a '
            'button background. One token cannot be both, because dark '
            'mode lightens the ink and that would strand white label text '
            'at 2.75:1.'
        ),
        [
            'danger',
            'danger-fill',
            'danger-fill-hover',
            'danger-fill-active',
            'on-danger',
            'danger-wash',
            'danger-edge',
            'danger-on-wash',
        ],
    ),
    (
        'Warning',
        'Under-voltage and storage banners.',
        [
            'warning',
            'warning-wash',
            'warning-edge',
            'warning-on-wash',
            'warning-ring',
        ],
    ),
    (
        'Success',
        'Live schedule windows, healthy states, success toasts.',
        [
            'success',
            'success-bright',
            'success-wash',
            'success-wash-strong',
            'success-edge',
            'success-edge-strong',
            'success-ring',
            'success-ring-pulse',
            'success-on-wash',
            'success-on-wash-strong',
        ],
    ),
    (
        'Focus',
        (
            'One ring colour for every focusable control, paired with '
            '--ring-width. Keyboard focus is the one state that must never '
            'be styled per component.'
        ),
        ['focus-ring'],
    ),
    (
        'Scrims',
        (
            'Theme-relative overlay ladder. Aliases onto ink in light and '
            'paper in dark, which is why no component needs a [data-theme] '
            'override of its own.'
        ),
        [
            'scrim-2',
            'scrim-4',
            'scrim-5',
            'scrim-6',
            'scrim-8',
            'scrim-10',
            'scrim-14',
            'scrim-18',
            'scrim-25',
            'scrim-40',
        ],
    ),
]

# (token suffix, the size it resolves to, what it is for)
#
# The three scales below are independent, and a few steps coincide:
# 0.75rem is a type step, a spacing step and a radius. Sonar reads that
# as a literal worth extracting (python:S1192), hence the NOSONAR. Do
# not extract it — hoisting the value into one constant would couple
# three scales that must be free to move separately, which is the whole
# reason they are separate scales.
TYPE_SCALE: list[tuple[str, str, str]] = [
    ('2xs', '0.6875rem', 'Eyebrows, uppercase micro-labels'),
    ('xs', '0.75rem', 'Hints, chips, table meta'),  # NOSONAR
    ('sm', '0.875rem', 'Secondary body, buttons, inputs, most labels'),
    ('base', '1rem', 'Body copy'),
    ('lg', '1.125rem', 'Card headings'),
    ('xl', '1.25rem', 'Stat figures, modal titles'),
    ('2xl', '1.5rem', 'Section titles, mobile page header'),
    ('3xl', '1.875rem', 'Page header'),
    ('4xl', '2.25rem', 'Error codes, splash headline'),
]

SPACING: list[tuple[str, str]] = [
    ('1', '0.25rem'),
    ('2', '0.5rem'),
    ('3', '0.75rem'),
    ('4', '1rem'),
    ('5', '1.25rem'),
    ('6', '1.5rem'),
    ('8', '2rem'),
    ('12', '3rem'),
    ('16', '4rem'),
]

RADII: list[tuple[str, str]] = [
    ('sm', '0.25rem'),
    ('md', '0.5rem'),
    ('lg', '0.75rem'),
    ('xl', '1rem'),
    ('pill', '62.5rem'),
]

ELEVATION: list[tuple[str, str]] = [
    ('sm', 'Resting card'),
    ('md', 'Raised card, dropdown'),
    ('lg', 'Modal'),
]

# One ordered stacking scale, replacing the 1040/1050/1060/1080/1090/1100
# that used to be scattered across the SCSS and three inline style
# attributes.
Z_INDEX: list[tuple[str, str, str]] = [
    ('below', '-1', 'Behind the flow'),
    ('base', '0', 'Default'),
    ('raised', '2', 'Sticky modal header and footer'),
    ('sticky', '100', 'Sticky page furniture'),
    ('nav', '200', 'Navbar'),
    ('bulk-bar', '300', 'Floating selection bar'),
    ('modal', '400', 'Modal overlay'),
    ('modal-nested', '410', 'Preview / bulk-edit over the asset modal'),
    ('datepicker', '500', 'Flatpickr, must clear nested modals'),
    ('toast', '600', 'Toast stack'),
    ('nudge', '610', 'Review nudge, above the toasts'),
]

BREAKPOINTS: list[tuple[str, str, str]] = [
    ('xs', '30rem', '480px'),
    ('sm', '40rem', '640px'),
    ('md', '48rem', '768px'),
    ('lg', '64rem', '1024px'),
    ('xl', '80rem', '1280px'),
    ('2xl', '96rem', '1536px'),
]

BUTTON_VARIANTS: list[tuple[str, str]] = [
    ('app-btn-primary', 'Primary action, one per view'),
    ('app-btn-light', 'Neutral action on a dark surface'),
    ('app-btn-outline-dark', 'Neutral action on a light surface'),
    ('app-btn-danger', 'Destructive action'),
    ('app-btn-link', 'Tertiary, reads as text'),
]


@require_safe
def design_system(request: HttpRequest) -> HttpResponse:
    """Render the whole design system on one page.

    Read-only, so GET and HEAD and nothing else.
    """
    context: dict[str, Any] = {
        'colour_groups': COLOUR_GROUPS,
        'type_scale': TYPE_SCALE,
        'spacing': SPACING,
        'radii': RADII,
        'elevation': ELEVATION,
        'z_index': Z_INDEX,
        'breakpoints': BREAKPOINTS,
        'button_variants': BUTTON_VARIANTS,
    }
    return render(request, 'design_system.html', context)
