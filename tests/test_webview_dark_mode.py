"""Integration coverage for the "Prefer dark mode" setting.

The setting is realised entirely inside the C++ webview: when the
operator enables it, the Python viewer exports
``ANTHIAS_PREFER_DARK_MODE=1`` (see ``_build_webview_env`` in
``src/anthias_viewer/__init__.py``) and ``applyDarkModePreference`` in
``src/anthias_webview/src/main.cpp`` translates that into the Chromium
switch ``--blink-settings=forceDarkModeEnabled=true`` before QtWebEngine
boots its Chromium context.

These tests pin the behaviour of that exact switch by driving Chromium —
the same Blink engine QtWebEngine embeds — headless, rendering a plain
white page, screenshotting it, and asserting on the average pixel
luminance: dark with the flag, light without it. Running the full
AnthiasViewer binary end-to-end (offscreen QPA + screenshot) belongs on
a real testbed, not CI; this validates the engine-level mechanism the
device relies on.

The flag literal below is duplicated from main.cpp on purpose — keep the
two in sync; the test fails loudly if Chromium ever stops honouring it.
"""

import io

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

# Must match applyDarkModePreference() in src/anthias_webview/src/main.cpp.
FORCE_DARK_FLAG = '--blink-settings=forceDarkModeEnabled=true'

# A deliberately light page: a pure-white viewport with no
# ``prefers-color-scheme`` hints, so Chromium's force-dark feature has
# something to invert and the no-flag baseline stays bright.
WHITE_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8">'
    '<style>html,body{margin:0;padding:0;height:100%;'
    'background:#ffffff;color:#000;}</style></head>'
    '<body><div style="width:100vw;height:100vh;"></div></body></html>'
)

# Pure white renders at luminance ~255; Chromium force-darkens it to a
# near-black canvas (~#121212). The thresholds leave a wide margin either
# side of the midpoint so anti-aliasing or a future engine tweak to the
# exact dark shade can't flip the result.
LIGHT_THRESHOLD = 200
DARK_THRESHOLD = 120


def _mean_center_luminance(extra_args: list[str]) -> float:
    """Render the white page in headless Chromium and return the mean
    luminance (0-255) of the centre of the screenshot.

    Cropping to the centre quarter avoids any window-chrome or
    scrollbar pixels skewing a full-frame average.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            # --no-sandbox mirrors the suite-wide launch override in
            # conftest.py: the test container runs as root, where
            # Chromium's setuid sandbox refuses to start.
            args=['--no-sandbox', '--hide-scrollbars', *extra_args],
        )
        try:
            page = browser.new_page(viewport={'width': 400, 'height': 300})
            page.set_content(WHITE_PAGE, wait_until='load')
            # Force-dark is applied during paint; give the compositor a
            # beat so the screenshot captures the darkened frame rather
            # than the initial white flash.
            page.wait_for_timeout(300)
            png = page.screenshot()
        finally:
            browser.close()

    image = Image.open(io.BytesIO(png)).convert('L')
    width, height = image.size
    centre = image.crop(
        (width // 4, height // 4, 3 * width // 4, 3 * height // 4)
    )
    pixels: list[int] = list(centre.getdata())
    return sum(pixels) / len(pixels)


@pytest.mark.integration
def test_force_dark_flag_renders_web_page_dark() -> None:
    """ "Prefer dark mode" on: the Chromium switch the webview injects
    must darken an otherwise-white page."""
    luminance = _mean_center_luminance([FORCE_DARK_FLAG])
    assert luminance < DARK_THRESHOLD, (
        f'expected a dark render with {FORCE_DARK_FLAG}, '
        f'got mean luminance {luminance:.1f}'
    )


@pytest.mark.integration
def test_without_flag_web_page_stays_light() -> None:
    """ "Prefer dark mode" off: with no flag the same white page must
    render light, proving the dark result above is caused by the flag
    and not the harness."""
    luminance = _mean_center_luminance([])
    assert luminance > LIGHT_THRESHOLD, (
        'expected a light render without the force-dark flag, '
        f'got mean luminance {luminance:.1f}'
    )
