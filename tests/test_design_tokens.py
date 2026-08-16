"""Static guards on the design-token layer.

No database, no browser, no build step: these parse the CSS sources
directly, so they run in milliseconds and fail in CI long before a
Playwright run would notice anything.

The contrast test is the important one. Lighthouse / PageSpeed audits
colour contrast against WCAG AA (4.5:1 for normal text, 3:1 for large
text), and a token layer makes that checkable at the source rather than
per-rendered-page: every foreground role is only ever painted on a known
set of background roles, so the pairs below are the whole surface area.
Checking here also covers the dark theme, which a Lighthouse run against
the default theme would silently skip.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / 'src/anthias_server/app/static/src'
ENTRY = STATIC / 'tailwind.css'
PALETTE = STATIC / 'css/palette.css'
DARK = STATIC / 'css/theme-dark.css'
SASS = REPO / 'src/anthias_server/app/static/sass'

# WCAG AA for normal text. Lighthouse reports a failure below this.
#
# Deliberately applied to EVERY pair, including roles only ever used at
# large sizes today. WCAG would allow 3:1 for large text, but Lighthouse
# judges by rendered font size, so that exemption only holds until
# someone reuses the token on a caption. Holding one bar means the audit
# can never surprise us and nobody has to reason about it at the use
# site.
AA = 4.5

_DECL = re.compile(r'(--[a-z0-9-]+)\s*:\s*([^;]+);')


def _declarations(text: str) -> dict[str, str]:
    return {m[1]: m[2].strip() for m in _DECL.finditer(text)}


def theme_block() -> dict[str, str]:
    """The @theme declarations alone, without the palette primitives.

    Public because test_design_system_page.py checks the demo page
    against exactly this set: the roles, which a component may name,
    and not the primitives, which it may not.
    """
    entry = ENTRY.read_text()
    start = entry.index('@theme static {')
    # The @theme block runs to the closing brace at column 0.
    end = entry.index('\n}', start)
    return _declarations(entry[start:end])


def _light_tokens() -> dict[str, str]:
    """Palette primitives plus the light-theme roles from @theme."""
    tokens = _declarations(PALETTE.read_text())
    tokens.update(theme_block())
    return tokens


def _dark_tokens() -> dict[str, str]:
    """Light tokens with the dark-theme overrides applied on top."""
    tokens = _light_tokens()
    tokens.update(_declarations(DARK.read_text()))
    return tokens


def _resolve(name: str, tokens: dict[str, str], depth: int = 0) -> str:
    """Follow var() indirection down to a literal colour."""
    if depth > 10:
        raise AssertionError(f'var() cycle resolving {name}')
    value = tokens[name]
    match = re.fullmatch(r'var\((--[a-z0-9-]+)\)', value)
    if match:
        return _resolve(match[1], tokens, depth + 1)
    return value


def _parse_colour(value: str) -> tuple[float, float, float, float]:
    """Return (r, g, b, alpha) with channels in 0-255 and alpha 0-1."""
    value = value.strip()
    if value.startswith('#'):
        hex_digits = value[1:]
        if len(hex_digits) == 3:
            hex_digits = ''.join(c * 2 for c in hex_digits)
        r, g, b = (int(hex_digits[i : i + 2], 16) for i in (0, 2, 4))
        return r, g, b, 1.0
    match = re.fullmatch(
        r'rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)'
        r'(?:\s*/\s*([\d.]+))?\s*\)',
        value,
    )
    if not match:
        raise AssertionError(f'cannot parse colour {value!r}')
    alpha = float(match[4]) if match[4] else 1.0
    return float(match[1]), float(match[2]), float(match[3]), alpha


def _composite(
    fg: tuple[float, float, float, float],
    bg: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Flatten a translucent colour onto an opaque backdrop.

    Scrims and washes are declared with alpha, so the effective colour a
    reader sees depends on what is behind it. Contrast has to be measured
    on the composited result, not the declared one.
    """
    alpha = fg[3]
    return (
        fg[0] * alpha + bg[0] * (1 - alpha),
        fg[1] * alpha + bg[1] * (1 - alpha),
        fg[2] * alpha + bg[2] * (1 - alpha),
        1.0,
    )


def _luminance(colour: tuple[float, float, float, float]) -> float:
    def channel(raw: float) -> float:
        c = raw / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b, _ = colour
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(
    fg_name: str,
    bg_name: str,
    tokens: dict[str, str],
    backdrop: str = '--color-surface',
) -> float:
    """WCAG contrast ratio between two token names, compositing alpha.

    ``backdrop`` is what a translucent background resolves against. It
    defaults to the surface plane because every translucent role we have
    (the status washes, the accent wash, the scrims) is painted inside a
    card. Getting this wrong is not a rounding error: composited against
    the plum canvas instead, the light theme's success wash reads as
    dark green and its on-wash text scores 1.28:1.

    Opaque backgrounds ignore it.
    """
    base = _parse_colour(_resolve(backdrop, tokens))
    bg = _composite(_parse_colour(_resolve(bg_name, tokens)), base)
    fg = _composite(_parse_colour(_resolve(fg_name, tokens)), bg)
    light, dark = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


# Every (foreground, background) pair the UI can actually paint. Adding
# a colour role means adding its pairs here, otherwise the role is
# unaudited.
PAIRS: list[tuple[str, str]] = [
    # Text on the surface plane. --color-fg is deliberately NOT paired
    # with the canvas: the canvas is brand plum in both themes, so text
    # sitting directly on it uses --color-on-canvas instead.
    ('--color-fg', '--color-surface'),
    ('--color-fg', '--color-surface-soft'),
    ('--color-fg', '--color-surface-tint'),
    ('--color-fg-muted', '--color-surface'),
    ('--color-fg-muted', '--color-surface-soft'),
    ('--color-fg-muted', '--color-surface-tint'),
    ('--color-fg-faint', '--color-surface'),
    ('--color-fg-faint', '--color-surface-tint'),
    # Text on the canvas plane (page headers, section labels).
    ('--color-on-canvas', '--color-canvas'),
    ('--color-on-canvas-muted', '--color-canvas'),
    ('--color-on-canvas-faint', '--color-canvas'),
    ('--color-on-canvas', '--color-canvas-deep'),
    ('--color-on-canvas-muted', '--color-canvas-deep'),
    # Text on the chrome plane (navbar, footer).
    ('--color-on-chrome', '--color-chrome'),
    ('--color-on-chrome-muted', '--color-chrome'),
    ('--color-on-chrome-faint', '--color-chrome'),
    # Text on the feature plane (active card, splash, login).
    ('--color-on-feature', '--color-feature-to'),
    ('--color-on-feature', '--color-feature-from'),
    ('--color-on-feature-muted', '--color-feature-to'),
    ('--color-on-feature-faint', '--color-feature-to'),
    # Anchors.
    ('--color-link', '--color-surface'),
    ('--color-link-hover', '--color-surface'),
    # Status text on a plain surface.
    ('--color-danger', '--color-surface'),
    # Status text on its own wash (chips, inline alerts).
    ('--color-danger-on-wash', '--color-danger-wash'),
    ('--color-warning-on-wash', '--color-warning-wash'),
    ('--color-success-on-wash', '--color-success-wash'),
    # Filled controls: label against the fill it sits on.
    ('--color-accent-text', '--color-accent'),
    ('--color-accent-text', '--color-accent-strong'),
    ('--color-on-danger', '--color-danger-fill'),
    ('--color-on-danger', '--color-danger-fill-hover'),
    ('--color-on-danger', '--color-danger-fill-active'),
]


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_contrast_ratios_meet_wcag_aa(theme: str) -> None:
    """Every foreground/background role pair passes WCAG AA.

    This is the same threshold Lighthouse / PageSpeed audits against, so
    a failure here is a failure there. Checking tokens rather than
    rendered pages also covers the dark theme, which an audit run against
    the default theme would never visit.
    """
    tokens = _light_tokens() if theme == 'light' else _dark_tokens()
    failures = []
    for fg, bg in PAIRS:
        ratio = contrast(fg, bg, tokens)
        if ratio < AA:
            failures.append(f'  {fg} on {bg}: {ratio:.2f}:1 (needs {AA}:1)')
    assert not failures, (
        f'{theme} theme fails WCAG AA contrast:\n' + '\n'.join(failures)
    )


# Bootstrap's default palette and its grid-tier breakpoints. The class
# names were renamed away long ago (test_template_views.py guards those),
# but the *values* survived in the stylesheets, which nothing covered:
# the danger ramp was still Bootstrap's #dc3545/#c82333/#bd2130 and
# .app-container still stepped through Bootstrap 5's grid tiers.
BOOTSTRAP_HEX = {
    '#dc3545': 'danger',
    '#0d6efd': 'primary',
    '#007bff': 'primary (bs4)',
    '#6c757d': 'secondary',
    '#28a745': 'success (bs4)',
    '#198754': 'success',
    '#ffc107': 'warning',
    '#17a2b8': 'info (bs4)',
    '#0dcaf0': 'info',
    '#f8f9fa': 'light',
    '#212529': 'dark',
    '#343a40': 'dark (bs4)',
}

# Bootstrap 5 grid tiers. 768 and 1024 are deliberately omitted: they are
# ordinary device widths our own scale also lands on.
BOOTSTRAP_BREAKPOINTS = ('576px', '992px', '1200px', '1400px')


def _stylesheets() -> list[Path]:
    sheets = sorted(STATIC.rglob('*.css'))
    if SASS.is_dir():
        sheets += sorted(SASS.rglob('*.scss'))
    return sheets


def test_no_bootstrap_palette_values_in_stylesheets() -> None:
    """No Bootstrap default colour survives as a literal.

    Complements the class-name guard in test_template_views.py. Renaming
    .btn-danger to .app-btn-danger removes the Bootstrap *class* but
    leaves the Bootstrap *colour*, which is how #dc3545 outlived the
    migration. It was a contrast problem too: 4.53:1 as text on white.
    """
    offenders = []
    for sheet in _stylesheets():
        for lineno, line in enumerate(sheet.read_text().splitlines(), 1):
            if line.lstrip().startswith(('//', '*', '/*')):
                continue
            for hex_value, role in BOOTSTRAP_HEX.items():
                if hex_value in line.lower():
                    offenders.append(
                        f'  {sheet.relative_to(REPO)}:{lineno} '
                        f'{hex_value} (Bootstrap {role})'
                    )
    assert not offenders, (
        'Bootstrap palette values reintroduced. Use a role token from '
        'the @theme block in tailwind.css:\n' + '\n'.join(offenders)
    )


def test_no_bootstrap_grid_breakpoints_in_stylesheets() -> None:
    """No Bootstrap grid tier survives as a media-query width.

    Breakpoints are declared once as --breakpoint-* in @theme and reached
    through `lg:` in markup or `@variant lg` in CSS. A raw min-width of
    576/992/1200/1400 means someone reached past the scale for a
    Bootstrap tier.
    """
    offenders = []
    for sheet in _stylesheets():
        for lineno, line in enumerate(sheet.read_text().splitlines(), 1):
            if '@media' not in line:
                continue
            for width in BOOTSTRAP_BREAKPOINTS:
                if width in line:
                    offenders.append(
                        f'  {sheet.relative_to(REPO)}:{lineno} {width}'
                    )
    assert not offenders, (
        'Bootstrap grid-tier breakpoints reintroduced. Breakpoints live '
        'in @theme; use a variant:\n' + '\n'.join(offenders)
    )


# Namespaces that belong to the token layer. A declaration of one of
# these inside the SCSS is a second source of truth for a name @theme
# already owns.
TOKEN_PREFIXES = (
    '--color-',
    '--radius-',
    '--shadow-',
    '--text-',
    '--tracking-',
    '--breakpoint-',
    '--font-',
    '--ease-',
    '--space-',
    '--scrim-',
)

# Component-scoped custom properties. These are the .surface pattern:
# a component declares them on ITS OWN selector and its children read
# them, which is how .surface--active flips every descendant without a
# descendant selector. They are local variables, not tokens.
COMPONENT_LOCAL = (
    '--color-scheme',  # not a token; the CSS property spelled long
)

# Anywhere in the line, not just at its start: `:root { --radius-sm: 4px }`
# on one line is the same reintroduction as a multi-line block. A var()
# READ never matches, because the name is followed by `)` or `,`, never
# by a colon.
_SCSS_DECL = re.compile(r'(--[a-z0-9-]+)\s*:')


def test_scss_declares_no_design_tokens() -> None:
    """The SCSS may READ tokens; it may not DECLARE them.

    This guards the failure that the "single authority" refactor did not
    actually close on its first pass. _styles.scss kept its own :root
    block declaring 39 names that @theme also declared, and because
    anthias.css loads after tailwind.css and both were unlayered, the
    SCSS won all 39 - @theme won none. Every consequence was silent:
    --radius-sm rendered 4px while @theme said 0.25rem, and the contrast
    test above scored @theme values the browser never painted.

    Nothing about that is visible in a diff of either file alone, which
    is why it needs a test rather than a convention.
    """
    offenders = []
    for sheet in _stylesheets():
        if sheet.suffix != '.scss':
            continue
        for lineno, line in enumerate(sheet.read_text().splitlines(), 1):
            if line.lstrip().startswith(('//', '*', '/*')):
                continue
            for match in _SCSS_DECL.finditer(line):
                name = match[1]
                if name in COMPONENT_LOCAL:
                    continue
                if name.startswith(TOKEN_PREFIXES):
                    offenders.append(
                        f'  {sheet.relative_to(REPO)}:{lineno} {name}'
                    )
    assert not offenders, (
        'Design tokens declared in SCSS. anthias.css loads after '
        'tailwind.css, so these silently override @theme and nothing '
        'renders the value the token layer says it does. Declare the '
        'token in tailwind.css (@theme), palette.css (primitive) or '
        'base.css (no @theme namespace), and read it here with var():\n'
        + '\n'.join(offenders)
    )
