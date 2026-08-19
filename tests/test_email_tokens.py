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
    r'^ {8}(--[a-z0-9-]+)(?: +on +(--[a-z0-9-]+))? *= *(.+?) *$',
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
# Attribute form only. The one CSS-form font-family in the template is
# the monospace stack on the code chip, and @theme declares no
# --font-mono to check it against: --font-sans is the only family the
# design system has.
_FONT_FAMILY = re.compile(r'font-family="([^"]+)"')
_FONT_SIZE = re.compile(r'font-size\s*[:=]\s*"?\s*([\d.]+)px')
_RADIUS = re.compile(r'border-radius\s*[:=]\s*"?\s*([\d.]+)px')
_TRACKING = re.compile(r'letter-spacing\s*[:=]\s*"?\s*([\d.]+em)')

# What each prefix of the table is scanned as, for the failure message.
SLOTS = {
    '--color-': 'colour',
    '--text-': 'font-size',
    '--radius-': 'border-radius',
    '--tracking-': 'letter-spacing',
    '--font-': 'font-family',
}

# Tags that set paragraph type, and so owe a leading as well as a size.
#
# mj-button is deliberately not one: its label is a single line in a box
# whose height comes from inner-padding, so a paragraph leading would
# only change the box. Neither is the code chip, which is inline and has
# to keep the leading of the paragraph around it.
# (?=[\s>]) rather than \s: a bare <mj-text> has no attributes to
# separate, and that is precisely the tag this needs to see, since
# one with nothing on it is one inheriting all of MJML's defaults.
_TYPE_TAG = re.compile(r'<(mj-text)(?=[\s>])[^>]*>', re.DOTALL)
_ATTR_SIZE = re.compile(r'font-size="([\d.]+)px"')
_ATTR_LEADING = re.compile(r'line-height="([\d.]+)"')


def _norm(value: str) -> str:
    """Compare ignoring quote style and whitespace runs.

    The font stack is the only row where this matters. @theme writes it
    with double quotes, and an MJML attribute is itself double-quoted,
    so the family name has to be single-quoted at every use site here.
    That is a syntax difference, not a different font. Hex and px values
    pass through untouched.
    """
    return re.sub(r'\s+', ' ', value.replace("'", '"')).strip()


def _template() -> str:
    return TEMPLATE.read_text()


def _rows() -> list[tuple[str, str | None, str]]:
    rows = _ROW.findall(_template())
    assert rows, 'No token table found in the template.'
    return [(name, backdrop or None, value) for name, backdrop, value in rows]


def _literals(prefix: str) -> set[str]:
    """The literal values of every table row under one token prefix."""
    return {
        _norm(value) for name, _, value in _rows() if name.startswith(prefix)
    }


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
        if _norm(literal) != _norm(expected):
            over = f' on {backdrop}' if backdrop else ''
            failures.append(
                f'  {name}{over}: table says {literal}, tokens say {expected}'
            )
    assert not failures, (
        'The newsletter template is painting colours the design system '
        'no longer declares:\n' + '\n'.join(failures)
    )


def _painted() -> dict[str, set[str]]:
    """Every literal the template paints with, keyed by token prefix.

    Read by both directions of the table check, which have to agree on
    what counts as a use or a row is dead to one and alive to the
    other. Scanning per slot rather than searching the file for the
    string is what makes them agree: `4px` occurs inside `14px` and
    `24px`, so a substring search would report the small radius as used
    by any font size that happens to end in it.
    """
    body = _ROW.sub('', _template())
    slots = {
        '--color-': set(_HEX.findall(body)),
        '--text-': {f'{value}px' for value in _FONT_SIZE.findall(body)},
        '--radius-': {f'{value}px' for value in _RADIUS.findall(body)},
        '--tracking-': set(_TRACKING.findall(body)),
        '--font-': set(_FONT_FAMILY.findall(body)),
    }
    return {k: {_norm(v) for v in vs} for k, vs in slots.items()}


def test_every_table_row_belongs_to_a_scanned_slot() -> None:
    """Both directions of the check cover every row.

    The two tests below only see the slots _painted() scans. A row
    under some other prefix would sit in the table looking as guarded
    as its neighbours while being checked in neither direction, so a
    new kind of token has to arrive with the scan that reads it.
    """
    prefixes = tuple(_painted())
    unscanned = [
        f'  {name}' for name, _, _ in _rows() if not name.startswith(prefixes)
    ]
    assert not unscanned, (
        'Token table rows in no slot _painted() scans, so nothing '
        'checks whether the template uses them or reaches past them:\n'
        + '\n'.join(unscanned)
    )


# Elements that paint text, and the attributes each must set on
# itself for the design to survive without the head.
# XML tolerates whitespace around the '=', so an exact substring
# would let `mj-class = "x"` through the one guard meant to stop it.
_MJ_CLASS = re.compile(r'\bmj-class\s*=')


def _declares(attribute: str, tag: str) -> bool:
    """Whether a tag sets this attribute itself.

    A substring test is wrong in both directions here. `color="` is
    contained in `background-color="`, so an mj-button that lost its own
    colour and kept its fill would satisfy the check built to catch
    exactly that; and XML allows whitespace around the '=' and either
    quote, so a reformat with no change of meaning would report a tag as
    missing something it sets.
    """
    return bool(
        re.search(rf'(?<![-\w]){re.escape(attribute)}\s*=\s*["\']', tag)
    )


_STYLED_TAG = re.compile(r'<(mj-text|mj-button)(?=[\s>])[^>]*>', re.DOTALL)
SELF_CONTAINED = ('color', 'font-family', 'font-size', 'line-height')


def test_nothing_visual_depends_on_the_head() -> None:
    """No mj-attributes block, and no mj-class on any element.

    This is the test for the defect that produced it. The whole design
    used to live in one mj-attributes block: the text colours, the
    section backgrounds, and every padding. Anything that takes the
    body without the head drops it, and MJML falls back to its own
    defaults, which is black text on the plum canvas at 1.6:1, no
    cards, and 20px of padding everywhere.

    The reason it needs a test rather than a convention is that it does
    not fail loudly. The output is still a laid-out email, still sends,
    and still looks deliberate. It was only visibly wrong once someone
    opened it.
    """
    text = _template()
    offenders = []
    if '<mj-attributes' in text:
        offenders.append(
            '  <mj-attributes>: put the values on the elements instead'
        )
    for lineno, line in enumerate(text.splitlines(), 1):
        if _MJ_CLASS.search(line):
            offenders.append(f'  line {lineno}: {line.strip()[:60]}')
    assert not offenders, (
        'emails/newsletter.mjml is leaning on its own mj-head again. '
        'Every colour, size and space has to be written on the element '
        'that uses it, or the design disappears the moment the body is '
        'taken without the head:\n' + '\n'.join(offenders)
    )


def test_every_styled_element_carries_its_own_styling() -> None:
    """Each mj-text and mj-button names its own colour and type.

    The complement to the guard above: removing mj-attributes is only
    half of it, because an element that sets no colour still inherits
    MJML's black. Nothing here may rely on a default, ours or MJML's.
    """
    offenders = []
    for tag in _STYLED_TAG.finditer(_template()):
        missing = [a for a in SELF_CONTAINED if not _declares(a, tag[0])]
        if missing:
            head = ' '.join(tag[0].split())[:58]
            offenders.append(f'  <{tag[1]}> missing {missing}: {head}')
    assert not offenders, (
        'Elements relying on an inherited value for something the head '
        'may not be around to supply:\n' + '\n'.join(offenders)
    )


def test_no_literal_reaches_past_the_token_table() -> None:
    """A value at a use site must be one the table accounts for.

    Without this the table is documentation rather than an authority:
    the next colour someone needs gets typed straight into an
    attribute, and the test above keeps passing because it only ever
    looks at the rows.
    """
    offenders = [
        f'  {SLOTS[prefix]} {value}'
        for prefix, used in _painted().items()
        for value in used
        if value not in _literals(prefix)
    ]
    assert not offenders, (
        'Literals used in emails/newsletter.mjml that no row of its '
        'token table resolves to. Add the token to the table and use '
        'the value the table gives, or the newsletter has a colour the '
        'product does not:\n' + '\n'.join(sorted(offenders))
    )


def test_every_table_row_is_used() -> None:
    """A row nothing paints with is a value nobody is maintaining.

    The table is checked against the token layer on every run, so a row
    left behind by an edit keeps being verified, keeps looking
    deliberate, and is the first thing someone copies when they need a
    colour.

    Rows are matched by literal, not by role, so rows that resolve to
    the same value cover for each other: --color-on-canvas,
    --color-surface and --color-on-feature are all #ffffff today, and
    one of the three going unused would not be caught. Telling them
    apart would mean every use site naming the role it paints, which is
    a lot of annotation to buy the detection of a stale row for a white
    that is still white. The colours that actually move are the ones
    with a value of their own, and those are matched exactly.
    """
    painted = _painted()
    unused = [
        f'  {name} = {literal}'
        for name, _, literal in _rows()
        for prefix, used in painted.items()
        if name.startswith(prefix) and _norm(literal) not in used
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
    for tag in _TYPE_TAG.finditer(_template()):
        size = _ATTR_SIZE.search(tag[0])
        if not size:
            continue
        expected = ramp.get(f'{size[1]}px')
        leading = _ATTR_LEADING.search(tag[0])
        # Silence is not agreement. A size with no leading beside it
        # inherits mj-text's default, which is one step's leading
        # applied to every other step.
        if leading is None:
            offenders.append(
                f'  <{tag[1]}> sets font-size {size[1]}px and no '
                f'line-height, so it inherits one; the ramp pairs that '
                f'size with {expected}'
            )
        elif leading[1] != expected:
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
