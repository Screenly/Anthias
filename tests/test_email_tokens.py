"""Static guards on the newsletter's copy of the design tokens.

emails/newsletter.mjml cannot use the token layer the way a template
does. Email clients strip CSS custom properties and Outlook drops
rgba(), so every colour, size and radius has to be written into that
file as a literal. That makes it a second copy of the token layer, and
a second copy goes stale without anything looking wrong: the file's
first version was derived from sass/_variables.scss, and when the
authority moved into the @theme block it kept rendering the colours of
a file that no longer existed.

So the template carries one table of resolved literals and nothing else
may write one. These tests re-derive every row from the real tokens,
and fail on any literal further down that the table does not account
for, which is what keeps the table from being routed around.

Same shape as tests/test_design_tokens.py, and it borrows that module's
resolver: no database, no browser, no build step, just the CSS and the
.mjml parsed directly.
"""

from __future__ import annotations

import re

from PIL import Image

from tests.test_design_tokens import (
    AA,
    REPO,
    _composite,
    _light_tokens,
    _parse_colour,
    _resolve,
    contrast,
)

TEMPLATE = REPO / 'emails/newsletter.mjml'
LOGO = REPO / 'website/static/img/anthias-logo-email.png'

# Email is px, the token layer is rem. One root font size, stated once.
ROOT_PX = 16

# Rows of the RESOLVED TOKENS table, which are the only lines in the
# file indented this far. Reading `--token [on --backdrop] = literal`.
_ROW = re.compile(
    r'^ {8}(--[a-z0-9-]+)(?: +on +(--[a-z0-9-]+))? *= *(\S+) *$',
    re.MULTILINE,
)

# Literals as they appear at a use site. Deliberately matches both the
# MJML attribute form (font-size="14px") and the CSS form inside
# mj-style (font-size: 14px), because a value smuggled in through the
# stylesheet is exactly as stale as one in an attribute.
#
# Padding and width are NOT checked. They are layout, not tokens: there
# is no spacing scale in @theme for them to drift from.
_HEX = re.compile(r'#[0-9a-fA-F]{3,8}')
_FONT_SIZE = re.compile(r'font-size\s*[:=]\s*"?\s*([\d.]+)px')
_RADIUS = re.compile(r'border-radius\s*[:=]\s*"?\s*([\d.]+)px')
_TRACKING = re.compile(r'letter-spacing\s*[:=]\s*"?\s*([\d.]+em)')

# Every tag that sets both, so the pair can be checked against the ramp.
_TAG = re.compile(r'<[a-z-]+\s[^>]*>', re.DOTALL)
_ATTR_SIZE = re.compile(r'font-size="([\d.]+)px"')
_ATTR_LEADING = re.compile(r'line-height="([\d.]+)"')


def _template() -> str:
    return TEMPLATE.read_text()


def _rows() -> list[tuple[str, str | None, str]]:
    rows = _ROW.findall(_template())
    assert rows, 'No token table found in the template.'
    return [(name, backdrop or None, value) for name, backdrop, value in rows]


def _literals(prefix: str) -> set[str]:
    """The literal values of every table row under one token prefix."""
    return {value for name, _, value in _rows() if name.startswith(prefix)}


def _flatten(name: str, backdrop: str | None, tokens: dict[str, str]) -> str:
    """The literal an email has to write for a colour token."""
    colour = _parse_colour(_resolve(name, tokens))
    if colour[3] < 1.0:
        assert backdrop, (
            f'{name} is declared with alpha, so it has no literal until '
            'the table names what it is composited over.'
        )
        base = _parse_colour(_resolve(backdrop, tokens))
        assert base[3] == 1.0, f'{backdrop} is itself translucent'
        colour = _composite(colour, base)
    r, g, b = (round(c) for c in colour[:3])
    return f'#{r:02x}{g:02x}{b:02x}'


def test_token_table_matches_the_design_tokens() -> None:
    """Every row is what the token layer resolves to today.

    The one test that would have caught the drift this file was written
    after: the template's palette survived a refactor that moved the
    authority out from under it, and nothing rendered differently in
    the app, because the app had already moved on.
    """
    tokens = _light_tokens()
    failures = []
    for name, backdrop, literal in _rows():
        assert name in tokens, f'{name} is not a token any more'
        if literal.startswith('#'):
            expected = _flatten(name, backdrop, tokens)
        elif literal.endswith('px'):
            rem = _resolve(name, tokens)
            assert rem.endswith('rem'), f'{name} is not a rem length: {rem}'
            expected = f'{float(rem[:-3]) * ROOT_PX:g}px'
        else:
            expected = _resolve(name, tokens)
        if literal != expected:
            over = f' on {backdrop}' if backdrop else ''
            failures.append(
                f'  {name}{over}: table says {literal}, tokens say {expected}'
            )
    assert not failures, (
        'The newsletter template is painting colours the design system '
        'no longer declares:\n' + '\n'.join(failures)
    )


def test_no_literal_reaches_past_the_token_table() -> None:
    """A value at a use site must be one the table accounts for.

    Without this the table is documentation rather than an authority:
    the next colour someone needs gets typed straight into an
    attribute, and the test above keeps passing because it only ever
    looks at the rows.
    """
    body = _ROW.sub('', _template())
    offenders = []
    checks = (
        ('colour', _HEX.findall(body), _literals('--color-')),
        (
            'font-size',
            [f'{v}px' for v in _FONT_SIZE.findall(body)],
            _literals('--text-'),
        ),
        (
            'border-radius',
            [f'{v}px' for v in _RADIUS.findall(body)],
            _literals('--radius-'),
        ),
        ('letter-spacing', _TRACKING.findall(body), _literals('--tracking-')),
    )
    for kind, used, allowed in checks:
        for value in used:
            if value not in allowed:
                offenders.append(f'  {kind} {value}')
    assert not offenders, (
        'Literals used in emails/newsletter.mjml that no row of its '
        'token table resolves to. Add the token to the table and use '
        'the value the table gives, or the newsletter has a colour the '
        'product does not:\n' + '\n'.join(sorted(set(offenders)))
    )


def test_every_table_row_is_used() -> None:
    """A row nothing paints with is a value nobody is maintaining.

    The table is checked against the token layer on every run, so a row
    left behind by an edit keeps being verified, keeps looking
    deliberate, and is the first thing someone copies when they need a
    colour.
    """
    body = _ROW.sub('', _template())
    unused = [
        f'  {name} = {literal}'
        for name, _, literal in _rows()
        if literal not in body
    ]
    assert not unused, (
        'Token table rows that nothing in the template uses:\n'
        + '\n'.join(unused)
    )


def test_font_sizes_and_line_heights_are_the_same_ramp_step() -> None:
    """A size and its leading come from one step of the type ramp.

    The ramp declares --text-lg beside --text-lg--line-height for a
    reason: the leading is chosen for that size. Email has no
    equivalent of the `text-lg` utility that binds them, so each tag
    restates both numbers and is free to mix steps. Doing that is not
    visibly wrong in the .mjml and shows up only as 14px copy set at a
    16px rhythm in the sent mail.
    """
    tokens = _light_tokens()
    ramp = {
        f'{float(_resolve(name, tokens)[:-3]) * ROOT_PX:g}px': tokens.get(
            f'{name}--line-height'
        )
        for name in tokens
        if re.fullmatch(r'--text-[a-z0-9]+', name)
    }
    offenders = []
    for tag in _TAG.findall(_template()):
        size = _ATTR_SIZE.search(tag)
        leading = _ATTR_LEADING.search(tag)
        if not size or not leading:
            continue
        expected = ramp.get(f'{size[1]}px')
        if leading[1] != expected:
            offenders.append(
                f'  font-size {size[1]}px with line-height {leading[1]}, '
                f'the ramp pairs it with {expected}'
            )
    assert not offenders, (
        'Type sizes set at a leading from a different step of the '
        'ramp:\n' + '\n'.join(sorted(set(offenders)))
    )


def test_masthead_logo_is_flattened_onto_the_canvas_token() -> None:
    """The logo PNG bakes --color-canvas into an image file.

    SVG does not render in Gmail, Outlook or Yahoo, and a transparent
    PNG picks up a black matte in older Outlook builds, so the masthead
    ships pre-composited onto the canvas colour. That puts a token
    inside a binary, where no other test can see it and no diff will
    ever show it moving.
    """
    canvas = _parse_colour(_resolve('--color-canvas', _light_tokens()))
    expected = tuple(round(c) for c in canvas[:3])

    assert LOGO.name in _template(), (
        f'{LOGO.name} is not the masthead src any more; this test is '
        'checking a file the template does not use.'
    )
    with Image.open(LOGO) as image:
        assert image.mode == 'RGB', (
            f'{LOGO.name} is {image.mode}: it must carry no alpha, or '
            'Outlook mattes it against black.'
        )
        corners = tuple(
            image.getpixel(xy)
            for xy in (
                (0, 0),
                (image.width - 1, 0),
                (0, image.height - 1),
                (image.width - 1, image.height - 1),
            )
        )
    assert set(corners) == {expected}, (
        f'{LOGO.name} sits on {corners}, but --color-canvas is '
        f'{expected}. Re-render it onto the current canvas colour, or '
        "the masthead is a rectangle of last season's plum on this "
        "season's background."
    )


# Pairs the newsletter paints that the app does not, so
# tests/test_design_tokens.py has never measured them. Both are the
# same substitution: an anchor on a dark plane, where the app would use
# --color-link and email cannot, because --color-link is chosen to
# carry on a light surface.
#
# Everything else the newsletter paints is already a row of that
# module's PAIRS, and is deliberately not repeated here.
EMAIL_PAIRS: list[tuple[str, str]] = [
    ('--color-accent', '--color-canvas'),
    ('--color-accent', '--color-feature-to'),
]


def test_email_only_colour_pairs_meet_wcag_aa() -> None:
    """Contrast holds for the pairs only the newsletter uses."""
    tokens = _light_tokens()
    failures = []
    for fg, bg in EMAIL_PAIRS:
        ratio = contrast(fg, bg, tokens, backdrop=bg)
        if ratio < AA:
            failures.append(f'  {fg} on {bg}: {ratio:.2f}:1 (needs {AA}:1)')
    assert not failures, (
        'The newsletter fails WCAG AA contrast:\n' + '\n'.join(failures)
    )
