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
from collections.abc import Callable, Iterator
from datetime import timedelta
from time import monotonic, sleep
from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Browser, Page, expect, sync_playwright

from anthias_server.app.models import Asset
from anthias_server.settings import settings


BASE_URL = 'http://localhost:8080'
SETTINGS_URL = f'{BASE_URL}/settings/'
SYSTEM_INFO_URL = f'{BASE_URL}/system-info/'

DEFAULT_TIMEOUT_MS = 15_000


# ---------------------------------------------------------------------------
# Asset seed data
# ---------------------------------------------------------------------------

asset_active: dict[str, Any] = {
    'mimetype': 'image',
    'asset_id': '7e978f8c1204a6f70770a1eb54a76e9b',
    'name': 'Sample Image',
    'uri': 'https://example.com/sample.png',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 6,
    'is_enabled': 1,
    'nocache': 0,
    'play_order': 0,
    'skip_asset_check': 0,
}

asset_active_2: dict[str, Any] = {
    'mimetype': 'web',
    'asset_id': '4c8dbce552edb5812d3a866cfe5f159d',
    'name': 'Wireload',
    'uri': 'http://www.wireload.net',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 5,
    'is_enabled': 1,
    'nocache': 0,
    'play_order': 1,
    'skip_asset_check': 0,
}

asset_disabled: dict[str, Any] = {
    'mimetype': 'web',
    'asset_id': 'aa11bb22cc33dd44ee55ff6677889900',
    'name': 'Disabled Page',
    'uri': 'https://example.com/disabled',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 5,
    'is_enabled': 0,
    'nocache': 0,
    'play_order': 99,
    'skip_asset_check': 0,
}


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


@pytest.fixture(scope='session')
def _playwright() -> Iterator[Any]:
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope='session')
def _browser(_playwright: Any) -> Iterator[Browser]:
    browser = _playwright.chromium.launch(headless=True, args=['--no-sandbox'])
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture
def page(_browser: Browser) -> Iterator[Page]:
    context = _browser.new_context(viewport={'width': 1400, 'height': 900})
    context.set_default_timeout(DEFAULT_TIMEOUT_MS)
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


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
    # Video uploads land with the placeholder default_duration while
    # probe_video_duration runs ffprobe out-of-band on Celery.
    assert asset.duration == settings['default_duration']
    assert asset.is_processing is True


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
def test_settings_page_renders(reset_assets: None, page: Page) -> None:
    page.goto(SETTINGS_URL)
    expect(
        page.get_by_role('heading', name='Settings', exact=True)
    ).to_be_visible()
    body = page.content()
    assert 'Internal Server Error' not in body
    assert 'Gateway Time-out' not in body


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
def test_system_info_page_renders(reset_assets: None, page: Page) -> None:
    page.goto(SYSTEM_INFO_URL)
    expect(page.get_by_role('heading', name='System Info')).to_be_visible()
    assert 'Internal Server Error' not in page.content()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_skip_next_button_present(reset_assets: None, page: Page) -> None:
    """The Next/Previous controls fire a viewer Redis publish — we
    can't observe the side effect without a viewer process, so the
    test just confirms the controls exist and submit cleanly (no 5xx
    on the action endpoint)."""
    Asset.objects.create(**asset_active)
    page.goto(BASE_URL)
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()
    _disable_asset_poll(page)

    next_btn = page.locator(
        'form[action*="control/next"] button[type="submit"]'
    )
    prev_btn = page.locator(
        'form[action*="control/previous"] button[type="submit"]'
    )
    expect(next_btn).to_be_visible()
    expect(prev_btn).to_be_visible()
    next_btn.click()
    expect(
        page.get_by_role('heading', name='Schedule Overview')
    ).to_be_visible()
