#!/usr/bin/env python
"""Render the newsletter masthead from the website's SVG logo.

Gmail, Outlook and Yahoo all refuse to render SVG, so the newsletter
cannot use website/assets/images/logo-full.svg the way every other
surface does, and a transparent PNG picks up a black matte in older
Outlook builds. The masthead is therefore a PNG flattened onto the
canvas colour, which puts a design token inside a binary file.

This script is how that file is regenerated, so the colour is read from
the newsletter's own token table rather than typed in again.
tests/test_email_tokens.py checks the table against the design tokens
and the rendered PNG against the table, which closes the loop: change
--color-canvas, run this, and the test goes green again.

Needs cairosvg, which is not a project dependency: it pulls the native
Cairo stack, and nothing else in the repo rasterises anything.

    uv run --no-project --with cairosvg python emails/render_logo.py
"""

from __future__ import annotations

import re
from pathlib import Path

import cairosvg

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / 'emails/newsletter.mjml'
SOURCE = REPO / 'website/assets/images/logo-full.svg'
TARGET = REPO / 'website/static/img/anthias-logo-email.png'

# Rendered at twice the width it is displayed at, so it stays sharp on
# a retina screen. The .mjml sets the display width to the SVG's own.
SCALE = 2

_CANVAS = re.compile(r'^ {8}--color-canvas +\= +(#[0-9a-f]{6})$', re.MULTILINE)
_SVG_SIZE = re.compile(r'<svg[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"')


def main() -> None:
    canvas = _CANVAS.search(TEMPLATE.read_text())
    if not canvas:
        raise SystemExit(
            f'No --color-canvas row in {TEMPLATE.name}: nothing says what '
            'the masthead should be flattened onto.'
        )

    size = _SVG_SIZE.search(SOURCE.read_text())
    if not size:
        raise SystemExit(f'{SOURCE.name} declares no width and height.')
    width, height = (int(value) * SCALE for value in size.groups())

    cairosvg.svg2png(
        url=str(SOURCE),
        write_to=str(TARGET),
        output_width=width,
        output_height=height,
        background_color=canvas[1],
    )
    print(f'{TARGET.relative_to(REPO)}: {width}x{height} on {canvas[1]}')


if __name__ == '__main__':
    main()
