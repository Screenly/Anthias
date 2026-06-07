"""
Browser-driven UI integration tests (Playwright).

Every test in this file drives a real Chromium (via Playwright's sync
API) against the uvicorn that ``bin/prepare_test_environment.sh -s``
started inside the same container on localhost:8080. The test process
and uvicorn share one SQLite file (``ENVIRONMENT=test`` +
``ANTHIAS_TEST_DB_PATH=/data/.anthias/test.db`` on both sides), so
``Asset.objects.create(...)`` from a fixture is visible to the page
on the next ``page.goto()``. ``transaction=True`` flushes the assets
table between tests for isolation.

Tests are grouped:

  1. Smoke / regression
  2. Asset table rendering
  3. Add asset (URL + uploads)
  4. Edit / preview / delete modals
  5. Toggle enable/disable
  6. Drag-reorder
  7. Settings + system info pages

Playwright's locator API auto-waits for elements to be visible and
actionable, so the fixed ``sleep()``s + custom retry helpers we needed
under Selenium are gone. ``page.wait_for_function(...)`` covers the
few cases (Alpine state predicates, DB row count) that aren't a
straight DOM query.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from time import monotonic, sleep
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from anthias_server.app.models import Asset
from anthias_server.settings import settings
from tests._seed_data import (
    CHOTCHKIES_FLAIR_POLICY,
    INITECH_ANNOUNCEMENT,
    LUMBERGH_MEMO,
    home_seed_assets,
)
from tests.conftest import MarketingShotFn


BASE_URL = 'http://localhost:8080'
SETTINGS_URL = f'{BASE_URL}/settings/'
SYSTEM_INFO_URL = f'{BASE_URL}/system-info/'

DEFAULT_TIMEOUT_MS = 15_000


# ---------------------------------------------------------------------------
# Asset seed data
# ---------------------------------------------------------------------------
#
# Concrete sample content lives in ``tests/_seed_data.py`` so the
# wizard / smoke / marketing pipelines stay on one set of Office Space
# parody assets. The aliases below preserve the role-based names the
# existing tests reference (``asset_active`` / ``asset_disabled``) —
# only the content the rows render with has changed.

asset_active: dict[str, Any] = INITECH_ANNOUNCEMENT
asset_active_2: dict[str, Any] = LUMBERGH_MEMO
asset_disabled: dict[str, Any] = CHOTCHKIES_FLAIR_POLICY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TemporaryCopy:
    """File-upload helper. Splinter is gone; Playwright's
    ``set_input_files()`` takes a path directly, but we still copy
    repo assets into /tmp because the bind-mount paths inside the test
    container differ between local and CI runs."""

    def __init__(self, original_path: str, base_path: str) -> None:
        self.original_path = original_path
        self.base_path = base_path

    def __enter__(self) -> str:
        self.path = os.path.join(tempfile.gettempdir(), self.base_path)
        shutil.copy2(self.original_path, self.path)
        return self.path

    def __exit__(self, *_: Any) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def _alpine_state(page: Page, expression: str = 'state') -> Any:
    """Read ``window.Alpine.$data(homeRoot)`` and return ``expression``
    as a JSON-decoded Python value. Returns ``None`` when the home-app
    x-data root isn't present on the page (e.g. settings)."""
    raw = page.evaluate(
        """(expression) => {
            const el = document.querySelector('[x-data*="homeApp"]');
            if (!el) return null;
            const state = window.Alpine.$data(el);
            return JSON.stringify(eval(expression));
        }""",
        expression,
    )
    return json.loads(raw) if raw is not None else None


def _wait_alpine(
    page: Page,
    expression: str,
    expected: Any,
    timeout: float = DEFAULT_TIMEOUT_MS,
) -> None:
    """Poll an Alpine-derived expression until it deep-equals
    ``expected``. Used for assertions like "modal opened" — the modal
    DOM is mounted via ``x-show``, but the cleanest signal that
    Alpine processed the click is the underlying state, not a CSS
    visibility check."""
    page.wait_for_function(
        """([expression, expected]) => {
            const el = document.querySelector('[x-data*="homeApp"]');
            if (!el) return false;
            const state = window.Alpine.$data(el);
            const actual = eval(expression);
            return JSON.stringify(actual) === JSON.stringify(expected);
        }""",
        arg=[expression, expected],
        timeout=timeout,
    )


def _disable_asset_poll(page: Page) -> None:
    """Strip the 5 s asset-table htmx poll so a swap doesn't cancel a
    click or drag mid-test. Call AFTER the page has loaded."""
    page.evaluate(
        """() => {
            const el = document.getElementById('asset-table');
            if (el) el.removeAttribute('hx-trigger');
            if (window.htmx) window.htmx.process(document.body);
        }"""
    )


def _wait_db(
    predicate: Callable[[], bool],
    timeout: float = 15.0,
    *,
    description: str,
) -> None:
    """Poll a Django ORM predicate until truthy. Playwright's locator
    auto-waits don't help here — the assertion is on DB state written
    by uvicorn after a form submit. Walks the deadline manually instead
    of relying on pytest-django's transaction tooling."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.2)
    raise AssertionError(f'wait_db {description!r} timed out')


def _drag_handle_to_row(
    page: Page, src_asset_id: str, dst_asset_id: str
) -> None:
    """Pointer-events drag of one row's grip handle onto the lower
    half of another row. The Anthias drag is implemented with raw
    pointerdown/pointermove/pointerup (not HTML5 D&D), so Playwright's
    ``locator.drag_to`` won't trigger it — we drive the mouse manually
    with paced steps so the pointermove handler sees enough movement
    to trigger an insertBefore. Mirrors what a human's drag looks
    like to the listener."""
    src_handle = page.locator(
        f'tr[data-asset-id="{src_asset_id}"] .drag-handle'
    )
    dst_row = page.locator(f'tr[data-asset-id="{dst_asset_id}"]')

    src_box = src_handle.bounding_box()
    dst_box = dst_row.bounding_box()
    assert src_box and dst_box, 'drag source/target not laid out'

    sx = src_box['x'] + src_box['width'] / 2
    sy = src_box['y'] + src_box['height'] / 2
    ex = dst_box['x'] + 200
    ey = dst_box['y'] + dst_box['height'] - 4

    page.mouse.move(sx, sy)
    page.mouse.down()
    page.wait_for_timeout(150)
    for i in range(1, 16):
        page.mouse.move(
            sx + (ex - sx) * i / 15,
            sy + (ey - sy) * i / 15,
        )
        page.wait_for_timeout(40)
    page.wait_for_timeout(200)
    page.mouse.up()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# pytest-playwright supplies the ``page`` fixture; browser viewport,
# launch flags and the optional 3× marketing scale-up live in
# ``tests/conftest.py`` so test_app.py and test_migrate_to_screenly.py
# don't duplicate the same overrides. CLI-level flags (--browser
# chromium, --tracing retain-on-failure, --screenshot only-on-failure,
# --output test-artifacts) are still set in pyproject.toml's addopts.


@pytest.fixture(autouse=True)
def _default_timeout(page: Page) -> None:
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)


@pytest.fixture
def reset_assets() -> None:
    Asset.objects.all().delete()


# ---------------------------------------------------------------------------
# 1. Smoke / regression
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_home_page_renders(reset_assets: None, page: Page) -> None:
    """Sanity: home loads, the homeApp x-data root mounts, no 5xx."""
    page.goto(BASE_URL)
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()
    page.wait_for_function(
        '() => {'
        '  const el = document.querySelector(\'[x-data*="homeApp"]\');'
        '  return el && typeof window.Alpine?.$data(el)?.openAdd === "function";'
        '}'
    )
    assert _alpine_state(page, 'state.mode') is None
    assert 'Internal Server Error' not in page.content()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_no_console_errors_on_load(reset_assets: None, page: Page) -> None:
    """The deferred home.js + vendor.js bundles must execute without
    throwing. A console error on a fresh page load means the minifier
    or an import broke the bundle — same class of regression as the
    Bun --minify-identifiers bug."""
    errors: list[str] = []
    page.on('pageerror', lambda exc: errors.append(f'pageerror: {exc}'))
    page.on(
        'console',
        lambda msg: (
            errors.append(f'console.{msg.type}: {msg.text}')
            if msg.type == 'error'
            and 'Cross-Origin-Opener-Policy' not in msg.text
            else None
        ),
    )
    page.goto(BASE_URL)
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()
    assert errors == [], f'unexpected console errors: {errors!r}'


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_alpine_click_handlers_fire_on_production_bundle(
    reset_assets: None, page: Page
) -> None:
    """Regression for Bun --minify-identifiers renaming Alpine's
    runtime expression evaluator vars. With identifiers minified,
    ``@click="openAdd()"`` silently became a no-op (mode ended up
    holding a Set leaked from another module). With whitespace+syntax
    minification only, this test must pass."""
    page.goto(BASE_URL)
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()
    page.locator('#add-asset-button').click()
    _wait_alpine(page, 'state.mode', 'add')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_home_renders_with_full_schedule(
    reset_assets: None,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """A six-row, mixed-mimetype schedule must render the asset table
    with every row's drag handle and action cluster reachable. The
    per-row tests below verify one row at a time and so miss
    regressions where a layout change pushes later rows past the
    table's right edge or stacks action buttons under a sibling cell.

    Doubles as the source for the ``home`` marketing capture when
    ``MARKETING_SCREENSHOTS=1`` is set."""
    seeds = home_seed_assets()
    for spec in seeds:
        Asset.objects.create(**spec)

    page.goto(BASE_URL)
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()

    rows = page.locator('tr[data-asset-id]')
    expect(rows).to_have_count(len(seeds))

    viewport = page.viewport_size
    assert viewport, 'viewport size missing'

    # For every row, verify that (a) the name cell has a real width
    # (catches the regression where a flex parent collapses a column),
    # (b) the row's right edge stays within the viewport (catches a
    # layout regression that pushes the action cluster off-screen),
    # (c) every enabled row's drag handle stays visible (only
    # is_enabled rows render one — see
    # test_inactive_row_has_no_drag_handle), and (d) the rightmost
    # action button — the delete trash — is visible and clickable.
    # Together these guard the "drag handle and action cluster stay
    # reachable" behaviour the docstring promises, not just that the
    # rows exist.
    for i, spec in enumerate(seeds):
        row = rows.nth(i)
        row_box = row.bounding_box()
        assert row_box, f'row {i} has no bounding box'
        assert row_box['x'] + row_box['width'] <= viewport['width'] + 1, (
            f'row {i} extends past viewport right edge: '
            f'row_right={row_box["x"] + row_box["width"]:.1f}, '
            f'viewport={viewport["width"]}'
        )

        name_cell = row.locator('.asset-cell-name__primary')
        expect(name_cell).to_be_visible()
        name_box = name_cell.bounding_box()
        assert name_box and name_box['width'] > 0, (
            f'row {i} name cell collapsed to zero width: {name_box!r}'
        )

        if spec['is_enabled']:
            drag_handle = row.locator('.drag-handle')
            expect(drag_handle).to_be_visible()
            handle_box = drag_handle.bounding_box()
            assert handle_box and handle_box['width'] > 0, (
                f'row {i} drag handle has no width: {handle_box!r}'
            )

        # The Delete button is the rightmost action cell. Locating
        # by title rather than nth-child means a re-ordering of the
        # action cluster still finds the right element. The +1
        # tolerance mirrors the row-edge check — Playwright reports
        # bounding boxes as floating-point CSS pixels and sub-pixel
        # rounding (especially under the 3× marketing device scale)
        # can produce a right edge like 1400.2 for a button that's
        # visually in-bounds.
        delete_btn = row.locator('button[title="Delete"]')
        expect(delete_btn).to_be_visible()
        del_box = delete_btn.bounding_box()
        assert (
            del_box
            and del_box['x'] + del_box['width'] <= viewport['width'] + 1
        ), f'row {i} Delete button pushed past viewport: {del_box!r}'

    marketing_screenshot('home')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_add_asset_modal_layers_over_full_schedule(
    reset_assets: None,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """Add-asset modal must layer correctly above a populated table.
    Asserts that the modal card has a non-zero bounding box inside
    the visible viewport AND that an asset row directly underneath
    its centre is occluded — catches the two failure modes that the
    docstring of test_add_asset_modal_opens doesn't (modal pushed
    off-screen by a CSS overflow regression, or modal card rendered
    with display: none while the backdrop alone shows).

    Doubles as the source for the ``add-asset`` marketing capture."""
    seeds = home_seed_assets()
    for spec in seeds:
        Asset.objects.create(**spec)

    page.goto(BASE_URL)
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()
    page.locator('#add-asset-button').click()
    _wait_alpine(page, 'state.mode', 'add')

    # Confirm the modal's title rendered before capturing — otherwise
    # the screenshot can land mid-transition with a partially faded
    # backdrop.
    expect(page.get_by_role('heading', name='Add asset')).to_be_visible()

    # The modal card runs a 220ms ``modal-in`` keyframe animation
    # (opacity + translateY). Playwright's ``to_be_visible()`` only
    # checks for a non-empty box; without explicitly waiting for the
    # animation to settle, the screenshot can land mid-fade and the
    # bounding-box assertions below would see the pre-final position.
    # Element.getAnimations({subtree:true}) returns all running or
    # pending animations under the card — wait until every one is in
    # the terminal ``finished`` / ``idle`` state.
    page.wait_for_function(
        """() => {
            const card = document.querySelector('.modal-card');
            if (!card) return false;
            const anims = card.getAnimations({ subtree: true });
            return anims.every(a =>
                a.playState === 'finished' || a.playState === 'idle'
            );
        }"""
    )

    # Modal card has a real footprint inside the viewport. ``.modal-card``
    # is the shared shell used by both the asset modal and the delete
    # confirmation; ``.first`` narrows to the visible add-asset card.
    modal_card = page.locator('.modal-card').first
    card_box = modal_card.bounding_box()
    assert card_box, 'modal card has no bounding box (rendered display:none?)'
    viewport = page.viewport_size
    assert viewport, 'viewport size missing'
    assert card_box['width'] > 200 and card_box['height'] > 200, (
        f'modal card collapsed: {card_box!r}'
    )
    # +1px tolerance on each edge for the same sub-pixel-rounding
    # reason as the home-row check above.
    assert (
        card_box['x'] >= -1
        and card_box['y'] >= -1
        and card_box['x'] + card_box['width'] <= viewport['width'] + 1
        and card_box['y'] + card_box['height'] <= viewport['height'] + 1
    ), f'modal card escaped viewport: card={card_box!r} viewport={viewport!r}'

    # The actual stacking check: the topmost element at the modal's
    # centre must live inside the modal subtree. A z-index regression
    # that leaves an asset row floating above the modal would make
    # ``elementFromPoint`` return that row instead — bounding-box
    # checks alone wouldn't catch that.
    center_x = card_box['x'] + card_box['width'] / 2
    center_y = card_box['y'] + card_box['height'] / 2
    topmost_in_modal = page.evaluate(
        """([x, y]) => {
            const el = document.elementFromPoint(x, y);
            return Boolean(el && el.closest('.modal-card'));
        }""",
        [center_x, center_y],
    )
    assert topmost_in_modal, (
        f'topmost element at modal centre ({center_x}, {center_y}) is not '
        f'inside .modal-card — z-index/stacking regression'
    )

    # full_page=False because the modal is position: fixed; Playwright's
    # full-page mode would push the modal card off-frame and capture
    # only the dimmed backdrop over the underlying page.
    marketing_screenshot('add-asset', full_page=False)


# ---------------------------------------------------------------------------
# 2. Asset-table rendering
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_empty_state_when_no_assets(reset_assets: None, page: Page) -> None:
    page.goto(BASE_URL)
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()
    expect(page.locator('tr[data-asset-id]')).to_have_count(0)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_active_row_renders_with_drag_handle(
    reset_assets: None, page: Page
) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)

    row = page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    expect(row).to_be_visible()
    expect(row.locator('.drag-handle')).to_be_visible()
    expect(row).to_contain_text(asset_active['name'])


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_inactive_row_has_no_drag_handle(
    reset_assets: None, page: Page
) -> None:
    Asset.objects.create(**asset_disabled)
    page.goto(BASE_URL)

    row = page.locator(f'tr[data-asset-id="{asset_disabled["asset_id"]}"]')
    expect(row).to_be_visible()
    expect(row.locator('.drag-handle')).to_have_count(0)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_asset_row_shows_humanised_duration(
    reset_assets: None, page: Page
) -> None:
    Asset.objects.create(**{**asset_active, 'duration': 125})
    page.goto(BASE_URL)
    expect(page.get_by_text('2m 5s')).to_be_visible()


# ---------------------------------------------------------------------------
# 3. Add asset
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_add_asset_modal_opens(reset_assets: None, page: Page) -> None:
    page.goto(BASE_URL)
    page.locator('#add-asset-button').click()
    _wait_alpine(page, 'state.mode', 'add')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_add_asset_via_url(reset_assets: None, page: Page) -> None:
    page.goto(BASE_URL)
    page.locator('#add-asset-button').click()
    _wait_alpine(page, 'state.mode', 'add')

    page.locator('input[name="uri"]').fill('https://example.com')
    page.locator('form[action*="assets/new"] button[type="submit"]').click()

    _wait_db(
        lambda: Asset.objects.filter(uri='https://example.com').exists(),
        description='asset persisted to DB',
    )
    asset = Asset.objects.get(uri='https://example.com')
    assert asset.mimetype == 'webpage'
    assert asset.duration == settings['default_duration']


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_add_asset_via_image_upload(reset_assets: None, page: Page) -> None:
    image_file = '/tmp/image.png'
    page.goto(BASE_URL)
    page.locator('#add-asset-button').click()
    _wait_alpine(page, 'state.mode', 'add')

    # Switch to the Upload File tab inside the add-asset modal.
    page.get_by_role('button', name='Upload file').click()
    page.locator('input[name="file_upload"]').set_input_files(image_file)

    _wait_db(
        lambda: Asset.objects.count() == 1,
        timeout=30.0,
        description='image upload persisted',
    )
    asset = Asset.objects.first()
    assert asset is not None
    # _prettify_upload_name strips extension and title-cases the stem.
    assert asset.name == 'Image'
    assert asset.mimetype == 'image'
    assert asset.duration == settings['default_duration']


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_add_asset_via_video_upload(reset_assets: None, page: Page) -> None:
    with _TemporaryCopy('tests/assets/asset.mov', 'video.mov') as video:
        page.goto(BASE_URL)
        page.locator('#add-asset-button').click()
        _wait_alpine(page, 'state.mode', 'add')
        page.get_by_role('button', name='Upload file').click()
        page.locator('input[name="file_upload"]').set_input_files(video)

        _wait_db(
            lambda: Asset.objects.count() == 1,
            timeout=30.0,
            description='video upload persisted',
        )

    asset = Asset.objects.first()
    assert asset is not None
    assert asset.name == 'Video'
    assert asset.mimetype == 'video'
    # Video uploads land with the placeholder default_duration and
    # is_processing=True, then normalize_video_asset ffprobes the
    # file out-of-band on Celery. Wait for the worker's terminal
    # write instead of asserting the transient placeholder — racing
    # the worker either way is flaky, and the wait doubles as the
    # regression guard for the worker reading the wrong DB (it used
    # to die with "no such table: assets", leaving the row stuck on
    # "Processing" forever).
    _wait_db(
        lambda: Asset.objects.filter(is_processing=False).count() == 1,
        timeout=30.0,
        description='video normalisation reached terminal state',
    )
    asset = Asset.objects.first()
    assert asset is not None
    # ffprobe'd duration of tests/assets/asset.mov (5.57 s, floored),
    # committed by the worker on both the accept and reject paths.
    assert asset.duration == 5
    # The test container sets no DEVICE_TYPE → empty HW-decode set →
    # the mpeg4 source is rejected by the codec gate: on_failure
    # writes the operator-facing error and disables the row.
    assert asset.is_enabled is False
    assert (asset.metadata or {}).get('error_message')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_add_two_uploads_in_one_modal_session(
    reset_assets: None, page: Page
) -> None:
    with (
        _TemporaryCopy('tests/assets/asset.mov', 'video.mov') as video,
        _TemporaryCopy(
            'src/anthias_server/app/static/img/standby.png', 'standby.png'
        ) as image,
    ):
        page.goto(BASE_URL)
        page.locator('#add-asset-button').click()
        _wait_alpine(page, 'state.mode', 'add')
        page.get_by_role('button', name='Upload file').click()
        page.locator('input[name="file_upload"]').set_input_files(image)
        expect(
            page.locator('.asset-cell-name__primary', has_text='Standby')
        ).to_be_visible(timeout=30_000)
        page.locator('input[name="file_upload"]').set_input_files(video)
        expect(
            page.locator('.asset-cell-name__primary', has_text='Video')
        ).to_be_visible(timeout=30_000)

    assets = list(Asset.objects.order_by('name'))
    assert len(assets) == 2
    assert {a.name for a in assets} == {'Standby', 'Video'}


# ---------------------------------------------------------------------------
# 4. Edit / preview / delete modals
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_modal_opens_with_asset(reset_assets: None, page: Page) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_active["asset_id"]}"] button[title="Edit"]'
    ).click()
    _wait_alpine(page, 'state.mode', 'edit')
    assert (
        _alpine_state(page, 'state.editAsset && state.editAsset.asset_id')
        == asset_active['asset_id']
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_changes_duration(reset_assets: None, page: Page) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_active["asset_id"]}"] button[title="Edit"]'
    ).click()
    _wait_alpine(page, 'state.mode', 'edit')

    page.locator('input[name="duration"]').fill('333')
    page.locator('form[action*="/update"] button[type="submit"]').click()

    _wait_db(
        lambda: Asset.objects.get(asset_id=asset_active['asset_id']).duration
        == 333,
        description='duration update persisted',
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_renames_asset(reset_assets: None, page: Page) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])

    page.locator('#edit-name').fill('TPS Reports — Final Notice')
    status = _submit_edit_form(page, asset_active['asset_id'])
    assert status < 500

    _wait_db(
        lambda: Asset.objects.get(asset_id=asset_active['asset_id']).name
        == 'TPS Reports — Final Notice',
        description='rename persisted',
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_save_shows_success_toast(reset_assets: None, page: Page) -> None:
    """Whichever submit path fires (htmx HX-Trigger or full-page
    redirect + Django messages), a success toast must surface."""
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])

    page.locator('#edit-name').fill('Toast check')
    _submit_edit_form(page, asset_active['asset_id'])

    toast = page.locator('.app-toast--success').first
    expect(toast).to_be_visible()
    expect(toast).to_contain_text('Changes saved')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_toggles_nocache_and_skip_asset_check(
    reset_assets: None, page: Page
) -> None:
    """The two Advanced switches post hidden-false/checkbox-true pairs
    that _checkbox() resolves — both directions must persist."""
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])
    _open_advanced_section(page)

    for box_id in ('edit-nocache', 'edit-skip'):
        box = page.locator(f'#{box_id}')
        expect(box).not_to_be_checked()
        page.locator(f'label[for="{box_id}"]').click()
        expect(box).to_be_checked()

    status = _submit_edit_form(page, asset_active['asset_id'])
    assert status < 500

    def _flags_set() -> bool:
        a = Asset.objects.get(asset_id=asset_active['asset_id'])
        return a.nocache is True and a.skip_asset_check is True

    _wait_db(_flags_set, description='advanced switches persisted')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_webpage_refresh_interval_persists(
    reset_assets: None, page: Page
) -> None:
    """The auto-refresh field renders for webpage assets only and
    lands in metadata.refresh_interval_s — feature #2813's UI path."""
    Asset.objects.create(**{**asset_active, 'mimetype': 'webpage'})
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])
    _open_advanced_section(page)

    refresh = page.locator('#edit-refresh-interval')
    expect(refresh).to_be_visible()
    refresh.fill('120')

    status = _submit_edit_form(page, asset_active['asset_id'])
    assert status < 500

    _wait_db(
        lambda: (
            Asset.objects.get(asset_id=asset_active['asset_id']).metadata or {}
        ).get('refresh_interval_s')
        == 120,
        description='refresh interval persisted',
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_refresh_interval_hidden_for_non_webpage(
    reset_assets: None, page: Page
) -> None:
    Asset.objects.create(**asset_active)  # image asset
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])
    _open_advanced_section(page)
    expect(page.locator('#edit-refresh-interval')).to_have_count(0)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_duration_disabled_for_video(
    reset_assets: None, page: Page
) -> None:
    """Video duration is owned by the ffprobe pipeline; the edit form
    must render it disabled so the probed value can't be clobbered."""
    Asset.objects.create(
        **{**asset_active, 'mimetype': 'video', 'duration': 42}
    )
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])
    expect(page.locator('#edit-duration')).to_be_disabled()

    status = _submit_edit_form(page, asset_active['asset_id'])
    assert status < 500

    # The POST is awaited by _submit_edit_form, so the server has
    # already processed the save — the probed duration must survive.
    assert Asset.objects.get(asset_id=asset_active['asset_id']).duration == 42


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_modal_cancel_discards_changes(
    reset_assets: None, page: Page
) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])

    page.locator('#edit-name').fill('Never saved')
    page.locator('form[action*="/update"] button:has-text("Cancel")').click()
    _wait_alpine(page, 'state.mode', None)

    # Cancel fires no POST; once Alpine dropped the modal the DB can
    # be asserted directly.
    assert (
        Asset.objects.get(asset_id=asset_active['asset_id']).name
        == asset_active['name']
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_preview_modal_opens(reset_assets: None, page: Page) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_active["asset_id"]}"] '
        f'button[title="Preview"]'
    ).click()
    _wait_alpine(
        page,
        'state.previewAsset && state.previewAsset.asset_id',
        asset_active['asset_id'],
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_preview_modal_renders_image_and_done_closes(
    reset_assets: None, page: Page
) -> None:
    """An image asset previews via /assets/<id>/preview inside the
    modal; the Done button drops the Alpine state again."""
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_active["asset_id"]}"] '
        f'button[title="Preview"]'
    ).click()
    _wait_alpine(
        page,
        'state.previewAsset && state.previewAsset.asset_id',
        asset_active['asset_id'],
    )
    img = page.locator('img.preview-media')
    expect(img).to_be_visible()
    src = img.get_attribute('src')
    assert src and f'/assets/{asset_active["asset_id"]}/preview' in src

    page.get_by_role('button', name='Done').click()
    _wait_alpine(page, 'state.previewAsset', None)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_delete_confirm_modal_opens_with_pending_id(
    reset_assets: None, page: Page
) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_active["asset_id"]}"] '
        f'button[title="Delete"]'
    ).click()
    _wait_alpine(page, 'state.pendingDeleteId', asset_active['asset_id'])


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_delete_confirm_removes_asset(reset_assets: None, page: Page) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_active["asset_id"]}"] '
        f'button[title="Delete"]'
    ).click()
    _wait_alpine(page, 'state.pendingDeleteId', asset_active['asset_id'])
    page.locator('form[action*="/delete"] button[type="submit"]').click()

    _wait_db(
        lambda: Asset.objects.count() == 0,
        description='asset removed from DB',
    )


# ---------------------------------------------------------------------------
# 4b. Advanced schedule (day-parting) — issue #2988
# ---------------------------------------------------------------------------


def _open_edit_modal(page: Page, asset_id: str) -> None:
    """Click a row's Edit button and wait for the modal's Alpine state."""
    expect(page.locator(f'tr[data-asset-id="{asset_id}"]')).to_be_visible()
    _disable_asset_poll(page)
    page.locator(
        f'tr[data-asset-id="{asset_id}"] button[title="Edit"]'
    ).click()
    _wait_alpine(page, 'state.mode', 'edit')


def _open_advanced_section(page: Page) -> None:
    """Expand the collapsible Advanced disclosure inside the edit
    modal (closed by default when the asset has no advanced values)."""
    toggle = page.locator('.modal-section__toggle')
    if toggle.get_attribute('aria-expanded') != 'true':
        toggle.click()
    expect(page.locator('#edit-play-from')).to_be_visible()


def _set_flatpickr_time(
    page: Page, input_id: str, hour: int, minute: int, meridiem: str | None
) -> None:
    """Drive a Flatpickr time picker the way an operator does: click
    the input so the picker pops open, type into its hour/minute
    spinners, click the AM/PM toggle into the requested state, then
    click a neutral spot to commit. (NOT Escape — the modal overlay
    listens for ``keydown.escape.window`` and would close the whole
    edit modal.) ``meridiem=None`` for 24-hour mode."""
    page.locator(f'#{input_id}').click()
    picker = page.locator('.flatpickr-calendar.open')
    expect(picker).to_have_count(1)
    expect(picker).to_be_visible()
    # Real keystrokes, not fill(): Flatpickr's spinners select-all on
    # focus and reformat on key input, so programmatic value-setting
    # gets clobbered by its own normalisation pass.
    picker.locator('input.flatpickr-hour').click()
    page.keyboard.type(str(hour), delay=50)
    picker.locator('input.flatpickr-minute').click()
    page.keyboard.type(f'{minute:02d}', delay=50)
    # Flatpickr only writes the spinner state back to the bound input
    # on blur of the spinner — Tab commits it.
    page.keyboard.press('Tab')
    if meridiem is not None:
        ampm = picker.locator('.flatpickr-am-pm')
        expect(ampm).to_be_visible()
        # The toggle flips on click; at most one flip needed.
        if ampm.inner_text().strip().upper() != meridiem.upper():
            ampm.click()
        expect(ampm).to_have_text(meridiem.upper())
    page.get_by_role('heading', name='Edit asset').click()
    expect(picker).not_to_be_visible()


def _submit_edit_form(page: Page, asset_id: str) -> int:
    """Submit the edit form and return the HTTP status of the
    assets_update POST it fires. Either submission path is fine —
    an htmx hx-post answers 200 with the table partial, a native
    form submit answers 302 back to home — the regression assertions
    only care that the POST didn't 5xx."""
    with page.expect_response(
        lambda r: f'/assets/{asset_id}/update/' in r.url
        and r.request.method == 'POST'
    ) as resp_info:
        page.locator('form[action*="/update"] button[type="submit"]').click()
    return resp_info.value.status


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_play_window_with_12_hour_time_picker(
    reset_assets: None, page: Page
) -> None:
    """Regression for issue #2988: with the default 12-hour clock,
    the Play from / Play until Flatpickr pickers post "02:30 PM"-shaped
    values, which assets_update used to crash on (int('30 PM') →
    ValueError → HTTP 500). Picking times through the real picker UI
    must save cleanly and persist the 24-hour equivalents."""
    from datetime import time as time_of_day

    assert settings['use_24_hour_clock'] is False, (
        'precondition: this test exercises the 12-hour clock default'
    )
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])
    _open_advanced_section(page)

    _set_flatpickr_time(page, 'edit-play-from', 2, 30, 'PM')
    _set_flatpickr_time(page, 'edit-play-to', 11, 45, 'PM')

    # The pickers must have produced 12-hour strings — that's the
    # exact shape the original bug choked on.
    expect(page.locator('#edit-play-from')).to_have_value('2:30 PM')
    expect(page.locator('#edit-play-to')).to_have_value('11:45 PM')

    status = _submit_edit_form(page, asset_active['asset_id'])
    assert status < 500, (
        f'assets_update returned HTTP {status} for a 12-hour play window'
    )

    def _persisted() -> bool:
        a = Asset.objects.get(asset_id=asset_active['asset_id'])
        return a.play_time_from == time_of_day(14, 30) and (
            a.play_time_to == time_of_day(23, 45)
        )

    _wait_db(_persisted, description='12-hour play window persisted')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_availability_window_with_12_hour_datetime_picker(
    reset_assets: None, page: Page
) -> None:
    """The Start / End availability pickers post the full
    "06/15/2026 02:30 PM" shape under the default 12-hour clock —
    saving must succeed and land the right aware datetimes."""
    assert settings['use_24_hour_clock'] is False, (
        'precondition: this test exercises the 12-hour clock default'
    )
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])

    # Type the full datetime strings (allowInput is on; this is the
    # same wire format the picker itself produces for m/d/Y h:i K).
    page.locator('#edit-start').fill('06/15/2026 09:00 AM')
    page.locator('#edit-end').fill('12/24/2026 11:30 PM')
    # Focusing the inputs pops the calendar panel, which overlays the
    # footer's Save button — click a neutral spot to dismiss it.
    page.get_by_role('heading', name='Edit asset').click()
    expect(page.locator('.flatpickr-calendar.open')).to_have_count(0)
    # Flatpickr re-formats to its h:i K mask on close (leading-zero
    # hour drops: '09:00 AM' → '9:00 AM') but must keep the typed
    # date/time semantics intact — no month/day swap, no reset to
    # the seeded values.
    expect(page.locator('#edit-start')).to_have_value('06/15/2026 9:00 AM')
    expect(page.locator('#edit-end')).to_have_value('12/24/2026 11:30 PM')

    status = _submit_edit_form(page, asset_active['asset_id'])
    assert status < 500, (
        f'assets_update returned HTTP {status} for 12-hour datetimes'
    )

    def _persisted() -> bool:
        a = Asset.objects.get(asset_id=asset_active['asset_id'])
        if a.start_date is None or a.end_date is None:
            return False
        start = a.start_date
        end = a.end_date
        return (start.month, start.day, start.hour, start.minute) == (
            6,
            15,
            9,
            0,
        ) and (end.month, end.day, end.hour, end.minute) == (12, 24, 23, 30)

    _wait_db(_persisted, description='12-hour availability window persisted')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_edit_play_days_and_clear_play_window(
    reset_assets: None, page: Page
) -> None:
    """The weekday picker persists a play_days subset, and clearing
    both time fields resets the day-parting window to all-day."""
    Asset.objects.create(
        **{
            **asset_active,
            'play_time_from': '09:00:00',
            'play_time_to': '17:00:00',
        }
    )
    page.goto(BASE_URL)
    _open_edit_modal(page, asset_active['asset_id'])
    # Asset has a non-default play window, so Advanced auto-expands.
    expect(page.locator('#edit-play-from')).to_be_visible()

    # Uncheck Sat (6) + Sun (7). The checkboxes are visually hidden
    # behind the chip-styled labels, so click the label like a user.
    for day in (6, 7):
        box = page.locator(f'input[name="play_days"][value="{day}"]')
        expect(box).to_be_checked()
        page.locator(
            f'label.weekday:has(input[name="play_days"][value="{day}"])'
        ).click()
        expect(box).not_to_be_checked()

    # Clear both time fields → "play all day".
    page.locator('#edit-play-from').fill('')
    page.locator('#edit-play-to').fill('')
    # Dismiss the time picker the focus popped open so it doesn't
    # overlay the Save button.
    page.get_by_role('heading', name='Edit asset').click()
    expect(page.locator('.flatpickr-calendar.open')).to_have_count(0)

    status = _submit_edit_form(page, asset_active['asset_id'])
    assert status < 500

    def _persisted() -> bool:
        a = Asset.objects.get(asset_id=asset_active['asset_id'])
        return (
            a.get_play_days() == [1, 2, 3, 4, 5]
            and a.play_time_from is None
            and a.play_time_to is None
        )

    _wait_db(_persisted, description='weekday subset + cleared window')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_delete_cancel_keeps_asset(reset_assets: None, page: Page) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_active["asset_id"]}"] '
        f'button[title="Delete"]'
    ).click()
    _wait_alpine(page, 'state.pendingDeleteId', asset_active['asset_id'])
    page.get_by_role('button', name='Cancel').click()
    _wait_alpine(page, 'state.pendingDeleteId', None)

    # Cancel fires no POST — assert the row survived directly.
    assert Asset.objects.filter(asset_id=asset_active['asset_id']).exists()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_day_parted_asset_renders_schedule_chips(
    reset_assets: None, page: Page
) -> None:
    """A weekday-filtered, day-parted asset must render its weekday
    and time-window chips on the table row (12-hour clock default)."""
    Asset.objects.create(
        **{
            **asset_active,
            'play_days': '[1, 3, 5]',
            'play_time_from': '09:00:00',
            'play_time_to': '17:30:00',
        }
    )
    page.goto(BASE_URL)
    row = page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    expect(row).to_be_visible()
    for label in ('Mon', 'Wed', 'Fri'):
        expect(row.locator('.schedule-chip', has_text=label)).to_be_visible()
    expect(
        row.locator('.schedule-chip', has_text='9:00 AM – 5:30 PM')
    ).to_be_visible()
    # No 'Everyday' chip when a weekday subset is active.
    expect(row.locator('.schedule-chip', has_text='Everyday')).to_have_count(0)


# ---------------------------------------------------------------------------
# 5. Toggle enable/disable
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_toggle_enables_asset(reset_assets: None, page: Page) -> None:
    Asset.objects.create(**asset_disabled)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_disabled["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_disabled["asset_id"]}"] '
        f'input[type="checkbox"]'
    ).click()

    _wait_db(
        lambda: Asset.objects.get(
            asset_id=asset_disabled['asset_id']
        ).is_enabled
        is True,
        description='asset is_enabled flipped to True',
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_toggle_disables_asset(reset_assets: None, page: Page) -> None:
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.locator(f'tr[data-asset-id="{asset_active["asset_id"]}"]')
    ).to_be_visible()
    _disable_asset_poll(page)

    page.locator(
        f'tr[data-asset-id="{asset_active["asset_id"]}"] '
        f'input[type="checkbox"]'
    ).click()

    _wait_db(
        lambda: Asset.objects.get(asset_id=asset_active['asset_id']).is_enabled
        is False,
        description='asset is_enabled flipped to False',
    )


# ---------------------------------------------------------------------------
# 6. Drag-reorder
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_drag_reorders_play_order(reset_assets: None, page: Page) -> None:
    """The vanilla pointer-events drag we replaced SortableJS with
    must move both the visible row and the persisted play_order."""
    Asset.objects.create(**asset_active)
    Asset.objects.create(**asset_active_2)
    page.goto(BASE_URL)
    expect(page.locator('#active-rows tr')).to_have_count(2)
    _disable_asset_poll(page)

    initial = page.locator('#active-rows tr').evaluate_all(
        'rows => rows.map(r => r.dataset.assetId)'
    )
    assert initial == [
        asset_active['asset_id'],
        asset_active_2['asset_id'],
    ]

    _drag_handle_to_row(
        page, asset_active['asset_id'], asset_active_2['asset_id']
    )
    page.wait_for_timeout(1500)  # POST + htmx refresh-assets refetch

    updated = page.locator('#active-rows tr').evaluate_all(
        'rows => rows.map(r => r.dataset.assetId)'
    )
    assert updated == [
        asset_active_2['asset_id'],
        asset_active['asset_id'],
    ]
    a = Asset.objects.get(asset_id=asset_active['asset_id'])
    b = Asset.objects.get(asset_id=asset_active_2['asset_id'])
    assert b.play_order < a.play_order


# ---------------------------------------------------------------------------
# 7. Other pages
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_settings_page_renders(
    reset_assets: None,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """Settings must render top-to-bottom on the marketing viewport
    without any 5xx body — also the source of the ``settings@Nx.png``
    capture."""
    page.goto(SETTINGS_URL)
    expect(
        page.get_by_role('heading', name='Settings', exact=True)
    ).to_be_visible()
    body = page.content()
    assert 'Internal Server Error' not in body
    assert 'Gateway Time-out' not in body

    marketing_screenshot('settings')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_settings_form_persists_default_duration(
    reset_assets: None, page: Page
) -> None:
    """End-to-end: change a setting, save, reload, see the new value
    in the input. Catches form-handler regressions where save returns
    200 but the value never lands in anthias.conf.

    The reset goes through the BROWSER (not via ``settings.save()`` in
    this process) because the running uvicorn keeps its own copy of
    the AnthiasSettings singleton — a host-side write touches only the
    conf file, not the in-memory cache the request handlers read."""
    original = settings['default_duration']
    new_value = original + 7

    def _post_default_duration(value: int) -> None:
        page.goto(SETTINGS_URL)
        expect(
            page.get_by_role('heading', name='Settings', exact=True)
        ).to_be_visible()
        page.locator('#default_duration').fill(str(value))
        page.locator(
            'form[action*="settings/save"] button[type="submit"]'
        ).click()
        # The save handler redirects back to /settings; the next render
        # has the new value in the input.
        expect(page.locator('#default_duration')).to_have_value(str(value))

    try:
        _post_default_duration(new_value)
    finally:
        _post_default_duration(original)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_navbar_links_navigate(reset_assets: None, page: Page) -> None:
    """The top-nav tabs must reach their pages (Integrations is
    balena-gated and absent in the test container)."""
    page.goto(BASE_URL)
    page.get_by_role('link', name='Settings').click()
    expect(
        page.get_by_role('heading', name='Settings', exact=True)
    ).to_be_visible()
    page.get_by_role('link', name='System info').click()
    expect(page.get_by_role('heading', name='System Info')).to_be_visible()
    # Not exact=True: the tabler icon's ::before glyph joins the
    # link's accessible name.
    page.get_by_role('link', name='Schedule').click()
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()


def _save_settings_form(page: Page) -> None:
    page.locator('form[action*="settings/save"] button[type="submit"]').click()
    # The save handler redirects back to /settings.
    expect(
        page.get_by_role('heading', name='Settings', exact=True)
    ).to_be_visible()


def _set_clock_toggle(page: Page, use_24h: bool) -> None:
    """Flip the 'Use 24-hour clock' switch through the browser and
    save. Goes through uvicorn (not settings.save() in this process)
    because the server keeps its own AnthiasSettings singleton."""
    page.goto(SETTINGS_URL)
    box = page.locator('#use_24_hour_clock')
    if box.is_checked() != use_24h:
        page.locator('label[for="use_24_hour_clock"]').click()
    if use_24h:
        expect(box).to_be_checked()
    else:
        expect(box).not_to_be_checked()
    _save_settings_form(page)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_24_hour_clock_play_window_end_to_end(
    reset_assets: None, page: Page
) -> None:
    """Counterpart to the 12-hour regression: with the 24-hour clock
    enabled in Settings, the play-window pickers run without an AM/PM
    toggle, post H:i values, and persist. Restores the 12-hour
    default afterwards so sibling tests keep their precondition."""
    from datetime import time as time_of_day

    Asset.objects.create(**asset_active)
    try:
        _set_clock_toggle(page, use_24h=True)

        page.goto(BASE_URL)
        _open_edit_modal(page, asset_active['asset_id'])
        _open_advanced_section(page)

        page.locator('#edit-play-from').click()
        picker = page.locator('.flatpickr-calendar.open')
        expect(picker).to_be_visible()
        # 24-hour mode renders no AM/PM toggle.
        expect(picker.locator('.flatpickr-am-pm')).to_have_count(0)
        picker.locator('input.flatpickr-hour').click()
        page.keyboard.type('14', delay=50)
        picker.locator('input.flatpickr-minute').click()
        page.keyboard.type('30', delay=50)
        page.keyboard.press('Tab')
        page.get_by_role('heading', name='Edit asset').click()
        expect(page.locator('#edit-play-from')).to_have_value('14:30')

        _set_flatpickr_time(page, 'edit-play-to', 23, 45, None)
        expect(page.locator('#edit-play-to')).to_have_value('23:45')

        status = _submit_edit_form(page, asset_active['asset_id'])
        assert status < 500

        def _persisted() -> bool:
            a = Asset.objects.get(asset_id=asset_active['asset_id'])
            return a.play_time_from == time_of_day(14, 30) and (
                a.play_time_to == time_of_day(23, 45)
            )

        _wait_db(_persisted, description='24-hour play window persisted')
    finally:
        _set_clock_toggle(page, use_24h=False)


def _set_date_format(page: Page, value: str) -> None:
    page.goto(SETTINGS_URL)
    page.locator('#date_format').select_option(value)
    _save_settings_form(page)
    expect(page.locator('#date_format')).to_have_value(value)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_date_format_round_trip_in_edit_modal(
    reset_assets: None, page: Page
) -> None:
    """Switch the device to dd/mm/yyyy: the availability pickers must
    format AND parse in that shape, so saving a typed d/m/Y datetime
    lands the right calendar day (not a swapped month/day)."""
    Asset.objects.create(**asset_active)
    try:
        _set_date_format(page, 'dd/mm/yyyy')

        page.goto(BASE_URL)
        _open_edit_modal(page, asset_active['asset_id'])
        page.locator('#edit-end').fill('24/12/2026 11:30 PM')
        page.get_by_role('heading', name='Edit asset').click()
        expect(page.locator('.flatpickr-calendar.open')).to_have_count(0)
        expect(page.locator('#edit-end')).to_have_value('24/12/2026 11:30 PM')

        status = _submit_edit_form(page, asset_active['asset_id'])
        assert status < 500

        def _persisted() -> bool:
            a = Asset.objects.get(asset_id=asset_active['asset_id'])
            if a.end_date is None:
                return False
            return (
                a.end_date.day,
                a.end_date.month,
                a.end_date.year,
                a.end_date.hour,
                a.end_date.minute,
            ) == (24, 12, 2026, 23, 30)

        _wait_db(_persisted, description='d/m/Y end date persisted')
    finally:
        _set_date_format(page, 'mm/dd/yyyy')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_settings_player_name_round_trip(
    reset_assets: None, page: Page
) -> None:
    """Player name is the simplest free-text setting — save, reload,
    value sticks, then restore."""
    page.goto(SETTINGS_URL)
    original = page.locator('#player_name').input_value()

    def _set_name(value: str) -> None:
        page.goto(SETTINGS_URL)
        page.locator('#player_name').fill(value)
        _save_settings_form(page)
        expect(page.locator('#player_name')).to_have_value(value)

    try:
        _set_name('Initech Lobby Screen')
    finally:
        _set_name(original)


# Inlined into the system-info marketing capture below. Lives at
# module scope so the script stays diffable and the indentation
# doesn't drift with the test body. Mutating the DOM (vs. patching
# Django context) is the only practical mock here — uvicorn runs in a
# separate process from pytest, so monkeypatching ``page_context``
# from a fixture wouldn't reach the rendering process.
_SYSTEM_INFO_MARKETING_MOCK_JS = """
() => {
  const labelToValue = (label) => {
    for (const card of document.querySelectorAll('.stat-card')) {
      const l = card.querySelector('.stat-card__label');
      if (l && l.textContent.trim() === label) {
        return card.querySelector('.stat-card__value');
      }
    }
    return null;
  };

  // Load average — three rows + trend card.
  const loads = [
    { value: '0.42', pct: 14 },
    { value: '0.51', pct: 17 },
    { value: '0.48', pct: 16 },
  ];
  document.querySelectorAll('.loadavg-row').forEach((row, i) => {
    if (!loads[i]) return;
    row.classList.remove('loadavg-row--warn', 'loadavg-row--high');
    const fill = row.querySelector('.loadavg-row__fill');
    if (fill) fill.style.width = loads[i].pct + '%';
    const v = row.querySelector('.loadavg-row__value');
    if (v) v.textContent = loads[i].value;
  });
  const trend = document.querySelector('.loadavg-trend');
  if (trend) {
    trend.classList.remove('loadavg-trend--up', 'loadavg-trend--down');
    trend.classList.add('loadavg-trend--stable');
    const primary = trend.querySelector('.loadavg-trend__primary');
    if (primary) primary.textContent = 'Steady';
    const secondary = trend.querySelector('.loadavg-trend__secondary');
    if (secondary) secondary.textContent = '4 CPUs · saturation at 4.0';
    const icon = trend.querySelector('i');
    if (icon) icon.className = 'ti ti-minus-circle';
  }

  const uptime = labelToValue('Uptime');
  if (uptime) uptime.textContent = '14 days, 6 hours';

  // Memory — Pi 5 8GB realistic numbers.
  const memPie = document.querySelector('.resource-pie--memory');
  if (memPie) {
    memPie.style.setProperty('--slice-1', '40');
    memPie.style.setProperty('--slice-2', '22');
    const tot = memPie.querySelector('.resource-pie__total');
    if (tot) tot.textContent = '8,192';
    const unit = memPie.querySelector('.resource-pie__unit');
    if (unit) unit.textContent = 'MiB total';
    const card = memPie.closest('.stat-card');
    const rows = card ? card.querySelectorAll('.resource-legend__value') : [];
    const memLegend = [
      '3,277 MiB · 40%',
      '1,802 MiB · 22%',
      '3,113 MiB · 38%',
      '4,915 MiB',
    ];
    rows.forEach((row, i) => {
      if (memLegend[i] !== undefined) row.textContent = memLegend[i];
    });
  }

  // Disk — 64 GB card, ~28% used.
  const diskPie = document.querySelector('.resource-pie--disk');
  if (diskPie) {
    diskPie.style.setProperty('--slice-1', '28');
    diskPie.style.setProperty('--slice-2', '0');
    const tot = diskPie.querySelector('.resource-pie__total');
    if (tot) tot.textContent = '57.3 GB';
    const unit = diskPie.querySelector('.resource-pie__unit');
    if (unit) unit.textContent = 'total';
    const card = diskPie.closest('.stat-card');
    const rows = card ? card.querySelectorAll('.resource-legend__value') : [];
    const diskLegend = ['16.0 GB · 28%', '41.3 GB · 72%'];
    rows.forEach((row, i) => {
      if (diskLegend[i] !== undefined) row.textContent = diskLegend[i];
    });
  }

  const device = labelToValue('Device model');
  if (device) device.textContent = 'Raspberry Pi 5 Model B (8 GB RAM)';

  // Resolution + its source meta line.
  const reso = labelToValue('Resolution');
  if (reso) {
    reso.textContent = '1920×1080';
    const meta = reso.parentElement
      ? reso.parentElement.querySelector('.stat-card__meta')
      : null;
    if (meta) meta.innerHTML = '<i class=\"ti ti-broadcast mr-1\"></i>Reported by viewer';
  }

  const cec = labelToValue('Display Power (CEC)');
  if (cec) cec.textContent = 'On';

  const versionHead = document.querySelector('.version-line__head');
  if (versionHead) {
    while (versionHead.firstChild) versionHead.removeChild(versionHead.firstChild);
    versionHead.textContent = 'v0.20.1';
  }
  const versionMeta = document.querySelector('.version-line__meta');
  if (versionMeta) versionMeta.textContent = 'commit a1b2c3d · 2 weeks ago';
  // Don't show the "Update available" pill in marketing — implies stale.
  const updatePill = document.querySelector('.update-pill');
  if (updatePill) updatePill.remove();

  const mac = labelToValue('MAC address');
  if (mac) mac.textContent = 'DC:A6:32:9F:42:1B';
}
"""


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_system_info_page_renders(
    reset_assets: None,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """Smoke-test that System Info renders (heading visible, no 5xx)
    against the values the page actually produced — also the source of
    the ``system-info@Nx.png`` marketing capture. Under
    ``MARKETING_SCREENSHOTS=1`` the rendered DOM is overwritten in
    place after those assertions with curated Pi-5-shaped values so
    the slider tile is presentable; the mock never runs before the
    assertions, so this stays a real smoke test in CI."""
    page.goto(SYSTEM_INFO_URL)
    expect(page.get_by_role('heading', name='System Info')).to_be_visible()
    assert 'Internal Server Error' not in page.content()

    if os.environ.get('MARKETING_SCREENSHOTS') == '1':
        page.evaluate(_SYSTEM_INFO_MARKETING_MOCK_JS)
    marketing_screenshot('system-info')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ('selector', 'expected_command'),
    [
        ('form[action*="control/next"] button[type="submit"]', 'next'),
        ('form[action*="control/previous"] button[type="submit"]', 'previous'),
    ],
)
def test_skip_buttons_publish_correct_command(
    reset_assets: None,
    page: Page,
    selector: str,
    expected_command: str,
) -> None:
    """Regression for #2821: clicking Next / Previous on the home page
    must publish the bare ``next`` / ``previous`` token on the
    ``anthias.viewer`` Redis channel — that's what the viewer's
    command dispatch table (src/anthias_viewer/__init__.py — ``commands``)
    keys on. A previous revision sent ``asset_<command>`` instead,
    which fell through to the ``unknown`` handler so the buttons
    silently no-op'd in production despite returning a clean 302."""
    import redis as _redis

    Asset.objects.create(**asset_active)

    # Subscribe to the viewer channel BEFORE clicking. Using a
    # directly-constructed client (not connect_to_redis) bypasses the
    # autouse fake in conftest.py — uvicorn is a separate process and
    # publishes against the real broker either way.
    # ``pubsub`` typed as Any because redis-py's stubs don't expose
    # get_message / unsubscribe on the PubSub class (matches the
    # workaround in src/anthias_viewer/messaging.py).
    client: Any = _redis.Redis(
        host='redis', port=6379, db=0, decode_responses=True
    )
    sub: Any = client.pubsub()
    try:
        sub.subscribe('anthias.viewer')
        # Wait for the SUBSCRIBE ack frame before clicking — otherwise
        # uvicorn can publish faster than the broker registers the
        # subscription, and the test races. ``get_message`` returning
        # ``None`` just means no frame arrived in this poll window;
        # keep polling until the deadline rather than breaking out
        # early.
        subscribed = False
        deadline = monotonic() + 5.0
        while monotonic() < deadline:
            msg = sub.get_message(timeout=0.2)
            if msg is None:
                continue
            if msg.get('type') == 'subscribe':
                subscribed = True
                break
        assert subscribed, 'redis SUBSCRIBE ack never arrived'

        page.goto(BASE_URL)
        expect(
            page.get_by_role('heading', name='Schedule Overview')
        ).to_be_visible()
        _disable_asset_poll(page)

        btn = page.locator(selector)
        expect(btn).to_be_visible()
        btn.click()
        expect(
            page.get_by_role('heading', name='Schedule Overview')
        ).to_be_visible()

        published: str | None = None
        deadline = monotonic() + 5.0
        while monotonic() < deadline:
            msg = sub.get_message(timeout=0.5)
            if msg is None:
                continue
            if msg.get('type') != 'message':
                continue
            data = msg.get('data')
            if isinstance(data, str) and data.startswith('viewer '):
                published = data[len('viewer ') :]
                break
        assert published == expected_command, (
            f'expected viewer publish {expected_command!r}, got {published!r}'
        )
    finally:
        try:
            sub.unsubscribe()
            sub.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 8. Display power (experimental, HDMI-CEC) — issue #2575
# ---------------------------------------------------------------------------
#
# The section is gated on cec_available(), which stats /dev/cec0 and
# /dev/vchiq. Neither exists in the test container by default, so the
# section is hidden on every other settings test. To exercise the
# visible state we stub /dev/vchiq with a plain file before navigating
# and remove it on teardown.

# Screenshot capture is OFF by default. The original PR (#2886) used
# screenshots for a one-time UX review; running them on every CI cycle
# is pure overhead because the `Upload integration test artifacts` step
# in .github/workflows/test-runner.yml is gated on `if: failure()` —
# the PNGs on a green run get written and immediately discarded. Set
# `PYTEST_CAPTURE_SCREENSHOTS=1` when you want them locally (UX work,
# design tweaks). Relative path mirrors the `--output test-artifacts`
# convention pytest-playwright already uses in pyproject.toml.
_SCREENSHOT_DIR = 'test-artifacts/cec'
# Explicit truthy parse so `PYTEST_CAPTURE_SCREENSHOTS=0` keeps the
# gate OFF — bool(os.environ.get(...)) would flip on for any non-empty
# string, including '0'/'false'.
_CAPTURE_SCREENSHOTS = os.environ.get(
    'PYTEST_CAPTURE_SCREENSHOTS', ''
).lower() in {'1', 'true', 'yes', 'on'}


def _maybe_screenshot(page: Page, filename: str, **kwargs: Any) -> None:
    """No-op unless PYTEST_CAPTURE_SCREENSHOTS is set in the env."""
    if not _CAPTURE_SCREENSHOTS:
        return
    os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
    page.screenshot(path=f'{_SCREENSHOT_DIR}/{filename}', **kwargs)


@pytest.fixture
def cec_stub_device() -> Any:
    """Create a stub `/dev/vchiq` so `diagnostics.cec_available()`
    returns True. /dev is tmpfs+writable in the test container; we
    create a plain file (not a real device node) — the gate only
    `os.path.exists`s the path.
    """
    path = '/dev/vchiq'
    created = False
    if not os.path.exists(path):
        try:
            open(path, 'w').close()
            created = True
        except OSError:
            pytest.skip('cannot stub /dev/vchiq in this environment')
    try:
        yield path
    finally:
        if created:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_display_power_section_hidden_without_cec_adapter(
    reset_assets: None, page: Page
) -> None:
    """No /dev/cec0 or /dev/vchiq in the container by default — the
    experimental section must NOT render. Guards against accidentally
    surfacing CEC controls on x86 / non-CEC hardware."""
    if os.path.exists('/dev/vchiq') or os.path.exists('/dev/cec0'):
        pytest.skip('CEC device present; cannot test the hidden case')
    page.goto(SETTINGS_URL)
    expect(
        page.get_by_role('heading', name='Settings', exact=True)
    ).to_be_visible()
    expect(page.get_by_role('heading', name='Display power')).to_have_count(0)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_display_power_section_visible_with_cec_adapter(
    reset_assets: None, page: Page, cec_stub_device: str
) -> None:
    """With a CEC device node present, both buttons render under an
    Experimental badge inside the System controls neighbourhood."""
    page.goto(SETTINGS_URL)
    expect(page.get_by_role('heading', name='Display power')).to_be_visible()
    expect(page.get_by_role('button', name='Turn display on')).to_be_visible()
    expect(page.get_by_role('button', name='Turn display off')).to_be_visible()
    # Experimental badge sits next to the heading.
    expect(page.locator('.settings-section__badge')).to_have_text(
        'Experimental'
    )

    # Screenshot 1: full settings page with the new section
    _maybe_screenshot(page, '01-settings-page-with-cec.png', full_page=True)

    # Screenshot 2: just the Display power card (tight crop)
    if _CAPTURE_SCREENSHOTS:
        section = page.locator('section', has_text='Display power').last
        section.scroll_into_view_if_needed()
        box = section.bounding_box()
        assert box, 'display-power section has no bounding box'
        _maybe_screenshot(
            page,
            '02-display-power-card.png',
            clip={
                'x': max(box['x'] - 8, 0),
                'y': max(box['y'] - 8, 0),
                'width': box['width'] + 16,
                'height': box['height'] + 16,
            },
        )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_display_power_button_click_surfaces_error_toast(
    reset_assets: None, page: Page, cec_stub_device: str
) -> None:
    """Issue #2575's feedback-loop requirement: a failing CEC command
    must surface to the operator as a visible toast, not silently
    succeed or no-op. The container has no real CEC adapter, so the
    inner subprocess fails — exactly the path we want to exercise."""
    page.goto(SETTINGS_URL)
    expect(page.get_by_role('heading', name='Display power')).to_be_visible()

    page.get_by_role('button', name='Turn display on').click()

    # After the form post + redirect, the toast pipeline reads
    # django-messages and pushes an app-toast--error item. Match by
    # the CSS class the toast template sets per-kind.
    error_toast = page.locator('.app-toast--error').first
    expect(error_toast).to_be_visible()
    # The message should namespace the failure as a display action.
    expect(error_toast).to_contain_text('Display turn-on')

    # Screenshot 3: error toast in context
    _maybe_screenshot(page, '03-error-toast.png', full_page=False)
