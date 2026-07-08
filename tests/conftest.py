"""
Playwright browser overrides + opt-in marketing capture for the
integration suite under ``tests/``.

The shared unit-test isolation (ENVIRONMENT=test, gi/pydbus stubs, the
fake-Redis wiring) lives in the **repo-root** ``conftest.py`` so it
applies to every test tree — including ``src/anthias_server/api/tests/``,
which has no ancestor conftest under ``tests/``. Only the Playwright
fixtures, which just the ``tests/`` integration suite consumes, remain
here.

Browser-test failure artifacts are owned by pytest-playwright. The
``--tracing retain-on-failure --screenshot only-on-failure --output
test-artifacts`` flags in pyproject.toml's addopts make it write
``<output>/<test-id>/{trace.zip,test-failed-1.png}`` for failed tests and
nothing for passing ones. The GH Actions upload-artifact@v7 step in
test-runner.yml uploads the directory on job failure, where the trace
replays via ``playwright show-trace``.

The ``browser_context_args`` viewport (1400×900) matches the existing
``website/assets/images/overview*.png`` convention exactly, so captures
slot into the Hugo image-set without rescaling. The
``marketing_screenshot`` fixture below is opt-in via
MARKETING_SCREENSHOTS=1: when enabled it bumps the context to 3× device
scale and saves a ``<name>.png`` plus retina ``<name>@2x.png`` and
``<name>@3x.png`` siblings under test-artifacts/marketing/ — matching the
website's existing overview*.png naming. Default integration runs stay at
1× and the fixture is a no-op so call sites in test bodies don't need to
branch.
"""

import os
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

_MARKETING_ENABLED = os.environ.get('MARKETING_SCREENSHOTS') == '1'
_MARKETING_SCALE = 3
_MARKETING_OUT_DIR = Path('test-artifacts/marketing')


@pytest.fixture(scope='session', autouse=True)
def _reset_marketing_dir() -> None:
    """Clear ``test-artifacts/marketing/`` once per session when
    marketing capture is enabled. Runs inside the test container
    (where pytest executes), so the cleanup has the same root
    permissions as the container that originally wrote the files —
    a host-side ``rm -rf`` from a CI runner step would hit
    permission-denied on a retry attempt because the bind-mounted
    artefacts are owned by root.

    No-op when MARKETING_SCREENSHOTS is unset; ordinary integration
    runs leave any existing marketing/ tree alone."""
    if _MARKETING_ENABLED and _MARKETING_OUT_DIR.exists():
        import shutil

        shutil.rmtree(_MARKETING_OUT_DIR)


@pytest.fixture(scope='session')
def browser_context_args(
    browser_context_args: dict[str, Any],
) -> dict[str, Any]:
    args = {
        **browser_context_args,
        'viewport': {'width': 1400, 'height': 900},
    }
    if _MARKETING_ENABLED:
        # Driving the whole session at 3× costs ~25% more CPU per
        # test (framebuffer scales with DPR²) — the price of being
        # able to drop call sites into existing tests rather than
        # fork a parallel marketing suite.
        args['device_scale_factor'] = _MARKETING_SCALE
    return args


@pytest.fixture(scope='session')
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    # ``--no-sandbox`` because the test container runs as root;
    # Chromium's setuid sandbox refuses to come up in that
    # configuration and the user-namespace sandbox would need extra
    # capabilities at compose-up time.
    return {
        **browser_type_launch_args,
        'args': [*browser_type_launch_args.get('args', []), '--no-sandbox'],
    }


MarketingShotFn = Callable[..., None]


@pytest.fixture
def marketing_screenshot(request: pytest.FixtureRequest) -> MarketingShotFn:
    """Capture ``page`` at the context's scale factor and emit a
    ``<name>.png`` plus retina ``<name>@2x.png`` and ``<name>@3x.png``
    siblings under ``test-artifacts/marketing/``. Filename convention
    matches the existing website ``overview.png`` / ``overview@2x.png``
    / ``overview@3x.png`` set so Hugo's image-set picker resolves the
    new files without additional config — the base 1× is the canonical
    URL and the ``@Nx`` siblings are retina sources.

    Call as ``marketing_screenshot('home')`` for a viewport-only
    capture (default) at 1400×900 — the size the website's home-page
    slider expects every slide to be. Pass ``full_page=True`` to
    capture the entire scrolled document instead; useful for asset
    pages that aren't sliced into the slider.

    Viewport-only is the default for two reasons. First, the home-
    page slider in ``website/`` lays every slide into a uniform
    1400×900 frame, and full-page captures (variable height, growing
    with document content) crop unpredictably in that frame. Second,
    any capture that includes a ``position: fixed`` overlay needs
    viewport-only: Playwright's full-page mode artificially extends
    the viewport height to fit the document, and fixed-position
    elements anchored to the (now much taller) viewport drift out of
    frame or get hidden under the dimming backdrop.

    No-op (still callable) when ``MARKETING_SCREENSHOTS`` is unset, so
    test bodies can sprinkle calls unconditionally — non-marketing
    integration runs pay zero capture cost beyond the function call.

    The 3× capture is the source of truth; 2× and 1× variants come
    from a LANCZOS downscale via Pillow — same algorithm professional
    design tooling uses for retina export. Re-rendering at lower DPRs
    produces visible moiré on the asset-table grid lines, so we don't
    do that.
    """
    if not _MARKETING_ENABLED:

        def _noop(name: str, *, full_page: bool = False) -> None:
            pass

        return _noop

    # Only resolve ``page`` when capture is actually active — this
    # fixture lives in the root conftest and the ``page`` fixture only
    # exists when pytest-playwright is collected, which is only the
    # case for integration tests. Lazy fixture lookup keeps unit-test
    # collection from importing playwright when MARKETING_SCREENSHOTS
    # is unset (the common case).
    page = request.getfixturevalue('page')

    # Pillow ships with the server image (HEIC normalisation pipeline),
    # so the test container has it. Import is local so the non-marketing
    # path stays clean if Pillow ever moves out of the server group.
    from PIL import Image as _PILImage

    _MARKETING_OUT_DIR.mkdir(parents=True, exist_ok=True)

    def _capture(name: str, *, full_page: bool = False) -> None:
        png_bytes = page.screenshot(full_page=full_page)

        # Write the 3× original verbatim — re-encoding through PIL
        # would needlessly re-compress.
        (_MARKETING_OUT_DIR / f'{name}@3x.png').write_bytes(png_bytes)

        src = _PILImage.open(BytesIO(png_bytes))
        w, h = src.size
        for scale, suffix in ((2, '@2x'), (1, '')):
            target = (
                int(w * scale / _MARKETING_SCALE),
                int(h * scale / _MARKETING_SCALE),
            )
            resized = src.resize(target, _PILImage.Resampling.LANCZOS)
            resized.save(_MARKETING_OUT_DIR / f'{name}{suffix}.png')

    return _capture
