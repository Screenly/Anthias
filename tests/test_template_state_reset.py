"""Static guard against stale state in reopenable UI surfaces.

No database, no browser, no build step: this parses the Django templates
directly, in the spirit of test_design_tokens.py.

The rule it enforces comes out of a real bug. The Add-asset modal is
hidden with ``x-show``, never unmounted, so its DOM outlives a close. Its
Asset URL box was a plain input bound to nothing, so the URL from one
asset was still sitting there when the operator went to add the next one.
The install form's Name box had the same defect in a subtler shape: bound
one-way with ``:value``, it drifted from component state the moment the
operator typed, and re-selecting the same app re-seeded the *identical*
string -- no reactive change, so no re-render, so the previous install's
name stayed on screen.

Both are the same category: an editable control whose DOM value can
diverge from the state that is supposed to seed it, living in a region
that hides rather than unmounts.

Two shapes are safe, and the rule accepts either:

* ``x-model`` -- two-way, so DOM and state cannot drift apart.
* anywhere inside ``<template x-if>`` -- Alpine tears the subtree down
  when the condition goes false and rebuilds it from current state on
  the way back in, so a one-way ``:value`` seeds a fresh element every
  time. This is why the edit and bulk-edit modals never had the bug.

Deliberately NOT a rule about ``x-show`` subtrees in general. The other
shape of the original bug -- a plain unbound input that nothing ever
resets -- can only be judged against what the surface means: the Add
modal is a fresh action every time it opens, so last time's URL is
wrong, while /settings is one form submitted once, where holding what
the operator typed as they toggle a disclosure open and shut is the
right behaviour. Broadening this to every ``x-show`` subtree flags those
settings fields, and an allowlist of judgment calls would blunt a rule
that currently has no exceptions. That shape is pinned behaviourally
instead, in add-modal-reset.test.ts.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / 'src/anthias_server/app/templates'

EDITABLE_TAGS = {'input', 'textarea', 'select'}

# Controls that carry no free-form text the operator can diverge, or
# that cannot be edited at all.
NON_EDITABLE_TYPES = {
    'hidden',
    'checkbox',
    'radio',
    'file',
    'submit',
    'button',
    'reset',
    'image',
}

ONE_WAY_VALUE = re.compile(r'(?:^|\s)(?::value|x-bind:value)\s*=')

# Django comments may wrap arbitrary markup (including example tags),
# so they come out before anything is scanned.
DJANGO_COMMENT = re.compile(
    r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}',
    re.DOTALL,
)


def _strip_comments(src: str) -> str:
    """Blank out Django comments, preserving offsets so reported line
    numbers still point at the real source line."""

    def blank(match: re.Match[str]) -> str:
        return re.sub(r'[^\n]', ' ', match.group(0))

    return DJANGO_COMMENT.sub(blank, src)


def _iter_tags(src: str) -> Iterator[tuple[int, str, str, bool]]:
    """Yield ``(offset, tag_text, name, is_closing)`` for every HTML tag.

    Quote-aware on purpose: Alpine expressions routinely contain ``>``
    (``x-show="uploadTotal > 1"``), which a ``<[^>]*>`` scan would cut
    the tag short on.
    """
    i, n = 0, len(src)
    while True:
        i = src.find('<', i)
        if i < 0:
            return
        if src.startswith('<!--', i):
            end = src.find('-->', i)
            i = n if end < 0 else end + 3
            continue
        match = re.match(r'</?([a-zA-Z][\w-]*)', src[i:])
        if not match:
            i += 1
            continue
        j = i + match.end()
        quote = None
        while j < n:
            char = src[j]
            if quote:
                if char == quote:
                    quote = None
            elif char in '"\'':
                quote = char
            elif char == '>':
                break
            j += 1
        yield i, src[i : j + 1], match.group(1).lower(), src[i : i + 2] == '</'
        i = j + 1


def _input_type(tag: str) -> str:
    match = re.search(r'\stype\s*=\s*"([^"]*)"', tag)
    return match.group(1).strip().lower() if match else 'text'


def _offenders(path: Path) -> list[tuple[int, str]]:
    src = _strip_comments(path.read_text(encoding='utf-8'))
    found: list[tuple[int, str]] = []
    # Depth of <template> nesting, and how many of those carry x-if.
    stack: list[bool] = []

    for offset, tag, name, closing in _iter_tags(src):
        if name == 'template':
            if closing:
                if stack:
                    stack.pop()
            else:
                stack.append(bool(re.search(r'\sx-if\s*=', tag)))
            continue

        if closing or name not in EDITABLE_TAGS:
            continue
        if any(stack):
            continue  # inside an x-if: remounts, so a one-way seed is fine
        if name == 'input' and _input_type(tag) in NON_EDITABLE_TYPES:
            continue
        if re.search(r'\s(readonly|disabled)(\s|=|>)', tag):
            continue
        if not ONE_WAY_VALUE.search(tag):
            continue
        if re.search(r'\sx-model(\.\w+)*\s*=', tag):
            continue

        line = src[:offset].count('\n') + 1
        found.append((line, ' '.join(tag.split())[:120]))

    return found


# rglob, matching test_design_tokens.py: a template moved into a
# subdirectory must not drop out of a repo-wide guard silently.
TEMPLATE_FILES = sorted(TEMPLATES.rglob('*.html'))


def test_templates_were_found() -> None:
    """A glob that silently matches nothing would make every case below
    pass without checking anything."""
    assert len(TEMPLATE_FILES) > 10


@pytest.mark.parametrize('template', TEMPLATE_FILES, ids=lambda p: p.name)
def test_editable_inputs_cannot_hold_stale_state(template: Path) -> None:
    offenders = _offenders(template)
    assert not offenders, (
        f'{template.name}: editable control(s) bound one-way with '
        f':value outside a <template x-if>. The DOM value drifts from '
        f'state as soon as the operator types, and re-seeding an '
        f"unchanged value is a no-op, so the previous visit's text "
        f'survives into the next one. Use x-model, or move the control '
        f'inside a <template x-if> so it remounts.\n'
        + '\n'.join(f'  line {line}: {tag}' for line, tag in offenders)
    )


def test_guard_catches_the_original_defect() -> None:
    """The rule is only worth having if it fires on the shape that
    caused the bug -- pin that, so a loosened matcher is visible."""
    bad = (
        '<div x-show="tab === \'apps\'" x-data="appsTab()">'
        '<input type="text" name="name" :value="assetName" required>'
        '</div>'
    )
    good_two_way = bad.replace(':value=', 'x-model=')
    good_unmounted = (
        '<template x-if="mode === \'edit\'">' + bad + '</template>'
    )

    def check(markup: str, tmp: Path) -> list[tuple[int, str]]:
        tmp.write_text(markup, encoding='utf-8')
        return _offenders(tmp)

    with tempfile.TemporaryDirectory() as tmpdir:
        probe = Path(tmpdir) / 'probe.html'
        assert check(bad, probe), 'guard missed the one-way bind'
        assert not check(good_two_way, probe)
        assert not check(good_unmounted, probe)
