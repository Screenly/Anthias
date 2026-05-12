"""Playwright UI integration tests for the Screenly migration wizard.

The wizard talks to two private endpoints — ``/api/v2/integrations/
screenly/validate`` and ``/migrate`` — both of which forward to the
real Screenly API. These tests mock those endpoints via Playwright's
``page.route`` so the browser exercise drives the full Alpine state
machine (intro → token → select → running → done → retry) without
ever reaching api.screenlyapp.com.

The asset rows the picker displays come from Anthias' own
``/api/v2/assets``, which we let through to the running uvicorn (and
seed via the ORM the same way ``test_app.py`` does).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Page, Route, expect

from anthias_server.app.models import Asset


BASE_URL = 'http://localhost:8080'
MIGRATE_URL = f'{BASE_URL}/settings/migrate-to-screenly/'
SETTINGS_URL = f'{BASE_URL}/settings/'

DEFAULT_TIMEOUT_MS = 15_000


# ---------------------------------------------------------------------------
# Asset seeds
# ---------------------------------------------------------------------------


def _asset_row(asset_id: str, name: str, mimetype: str, uri: str) -> dict[str, Any]:
    """Minimum fields ``Asset.objects.create`` accepts plus the
    schedule-window defaults so the row shows on the home page too.
    Mirrors the seed helpers in tests/test_app.py."""
    return {
        'asset_id': asset_id,
        'name': name,
        'mimetype': mimetype,
        'uri': uri,
        'start_date': timezone.now() - timedelta(days=1),
        'end_date': timezone.now() + timedelta(days=1),
        'duration': 5,
        'is_enabled': 1,
        'nocache': 0,
        'play_order': 0,
        'skip_asset_check': 0,
    }


SEED_ASSETS = [
    _asset_row('a1', 'Demo Reel', 'video', '/data/anthias_assets/abc1.mp4'),
    _asset_row('a2', 'Company Site', 'webpage', 'https://example.com/promo'),
    _asset_row('a3', 'Welcome Splash', 'image', '/data/anthias_assets/abc3.png'),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='session')
def browser_context_args(
    browser_context_args: dict[str, Any],
) -> dict[str, Any]:
    return {**browser_context_args, 'viewport': {'width': 1400, 'height': 900}}


@pytest.fixture(scope='session')
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    return {
        **browser_type_launch_args,
        'args': [*browser_type_launch_args.get('args', []), '--no-sandbox'],
    }


@pytest.fixture(autouse=True)
def _default_timeout(page: Page) -> None:
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)


@pytest.fixture
def reset_assets() -> None:
    Asset.objects.all().delete()


@pytest.fixture
def seeded_assets(reset_assets: None) -> list[Asset]:
    """Three rows covering the local-file path (image+video) and the
    URL-backed path. Tests assert on names rather than ids so an
    eventual reordering doesn't make the suite brittle."""
    rows: list[Asset] = []
    for spec in SEED_ASSETS:
        rows.append(Asset.objects.create(**spec))
    return rows


# ---------------------------------------------------------------------------
# Route helpers — keep the mock contract in one place so a Screenly
# response-shape change has a single line to update.
# ---------------------------------------------------------------------------


def _mock_validate_invalid(page: Page) -> None:
    """Token validation that says "Screenly rejected this token."."""
    page.route(
        '**/api/v2/integrations/screenly/validate',
        lambda route: route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps({'valid': False}),
        ),
    )


def _mock_validate_valid(
    page: Page, group_id: str = 'GROUP01', group_title: str = 'Migrated from Anthias'
) -> None:
    page.route(
        '**/api/v2/integrations/screenly/validate',
        lambda route: route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(
                {
                    'valid': True,
                    'asset_group_id': group_id,
                    'asset_group_title': group_title,
                }
            ),
        ),
    )


def _mock_migrate_per_asset(page: Page, outcomes: dict[str, dict[str, Any]]) -> None:
    """Map each asset_id to a canned migrate response.

    ``outcomes`` is keyed by asset_id and each value is the JSON body
    the backend would have returned (``success`` boolean, plus
    ``screenly_asset_id`` / ``error`` as relevant). Anything not in
    the map gets a generic success — keeps the mock minimal for
    tests that only care about one failure.
    """

    def handler(route: Route) -> None:
        body = route.request.post_data_json or {}
        asset_id = body.get('asset_id', '')
        result = outcomes.get(
            asset_id,
            {
                'success': True,
                'screenly_asset_id': f'01OUT-{asset_id}',
            },
        )
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(result),
        )

    page.route('**/api/v2/integrations/screenly/migrate', handler)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_settings_page_links_to_migration_wizard(
    reset_assets: None, page: Page
) -> None:
    """The Settings page must surface the entry point — otherwise the
    operator has no way to discover the wizard."""
    page.goto(SETTINGS_URL)
    start = page.get_by_role('link', name='Start migration')
    expect(start).to_be_visible()
    start.click()
    expect(
        page.get_by_role('heading', name='Migrate to Screenly')
    ).to_be_visible()
    expect(
        page.get_by_role('heading', name='Get started')
    ).to_be_visible()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_intro_step_advances_to_token_step(
    reset_assets: None, page: Page
) -> None:
    """The "I have a token" button must transition Alpine state from
    intro → token; the token surface is the only one visible after."""
    page.goto(MIGRATE_URL)
    expect(page.get_by_role('heading', name='Get started')).to_be_visible()
    page.get_by_role('button', name='I have a token').click()
    expect(
        page.get_by_role('heading', name='Screenly API token')
    ).to_be_visible()
    expect(page.locator('#screenly_token')).to_be_visible()
    # Intro card should be hidden by Alpine's x-show (CSS rule on
    # [x-cloak] is what enforces this on initial paint, the toggle
    # is what enforces it on transitions).
    expect(page.get_by_role('heading', name='Get started')).not_to_be_visible()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_invalid_token_surfaces_inline_error(
    reset_assets: None, page: Page
) -> None:
    """A 200 response with ``valid: false`` must render the rejection
    message inline; the user stays on the token step (no advance to
    asset picker)."""
    _mock_validate_invalid(page)

    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()
    page.locator('#screenly_token').fill('clearly-wrong-token')
    page.get_by_role('button', name='Continue').click()

    expect(
        page.get_by_text(
            'Screenly rejected this token. Double-check it in your dashboard.'
        )
    ).to_be_visible()
    # Asset picker must NOT appear.
    expect(
        page.get_by_role('heading', name='Choose assets to migrate')
    ).not_to_be_visible()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_valid_token_loads_asset_picker_with_seed_rows(
    seeded_assets: list[Asset], page: Page
) -> None:
    """The Continue button on a valid token must call validate, then
    load /api/v2/assets, then transition to the select step with each
    seed asset rendered as a row."""
    _mock_validate_valid(page)

    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()
    page.locator('#screenly_token').fill('a-valid-looking-token')
    page.get_by_role('button', name='Continue').click()

    expect(
        page.get_by_role('heading', name='Choose assets to migrate')
    ).to_be_visible()
    # Destination group title must thread through to the UI so the
    # operator knows where assets will land.
    expect(page.get_by_text('Migrated from Anthias')).to_be_visible()
    # All three seed rows visible.
    for spec in SEED_ASSETS:
        expect(page.get_by_text(spec['name'])).to_be_visible()
    # Counter mirrors the seed count — default state is all-selected.
    expect(
        page.get_by_text(f'{len(SEED_ASSETS)} of {len(SEED_ASSETS)} selected')
    ).to_be_visible()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_select_none_disables_migrate_button(
    seeded_assets: list[Asset], page: Page
) -> None:
    """Migrate button must be disabled when nothing is selected — the
    backend would 400 on an empty selection too, but UX-wise the
    button shouldn't even be clickable."""
    _mock_validate_valid(page)

    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()
    page.locator('#screenly_token').fill('tok')
    page.get_by_role('button', name='Continue').click()
    expect(
        page.get_by_role('heading', name='Choose assets to migrate')
    ).to_be_visible()

    page.get_by_role('button', name='Select none').click()
    # The migrate button retains the dynamic "Migrate 0 assets" label
    # but is disabled — verify both.
    migrate_btn = page.locator('button:has-text("Migrate")').first
    expect(migrate_btn).to_be_disabled()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_full_migration_flow_with_mixed_results(
    seeded_assets: list[Asset], page: Page
) -> None:
    """End-to-end run: 3 assets, middle one fails. After the queue
    drains, the header reads "Migration finished with errors" (not
    the all-clear copy) and the Retry button surfaces the count."""
    _mock_validate_valid(page)
    _mock_migrate_per_asset(
        page,
        outcomes={
            'a2': {
                'success': False,
                'error': 'File not found on device: abc1.mp4',
            },
        },
    )

    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()
    page.locator('#screenly_token').fill('tok')
    page.get_by_role('button', name='Continue').click()
    expect(
        page.get_by_role('heading', name='Choose assets to migrate')
    ).to_be_visible()

    page.locator('button:has-text("Migrate")').first.click()

    # The errors-present header is the canonical signal that the
    # queue settled — wait on that rather than a generic spinner-
    # gone check.
    expect(
        page.get_by_role('heading', name='Migration finished with errors')
    ).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
    # Retry button must carry the failure count so the operator
    # knows what they're re-running.
    expect(page.get_by_role('button', name='Retry 1 failed')).to_be_visible()

    # Failed row must surface the backend's error verbatim.
    expect(
        page.get_by_text('File not found on device: abc1.mp4')
    ).to_be_visible()
    # Two success rows — we count occurrences of the "Uploaded to
    # Screenly" label instead of asserting on the ULID, which the
    # done state intentionally suppresses (operators can't act on it).
    expect(page.get_by_text('Uploaded to Screenly')).to_have_count(2)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_retry_failed_replays_only_failed_rows(
    seeded_assets: list[Asset], page: Page
) -> None:
    """Retry failed must re-run only the failed row, not the whole
    queue. Tracked via the request count keyed by asset_id."""
    _mock_validate_valid(page)

    call_log: dict[str, int] = {}

    def counting_handler(route: Route) -> None:
        body = route.request.post_data_json or {}
        asset_id = body.get('asset_id', '')
        call_log[asset_id] = call_log.get(asset_id, 0) + 1
        # First pass: a2 fails. Retry: a2 succeeds.
        if asset_id == 'a2' and call_log[asset_id] == 1:
            payload = {'success': False, 'error': 'transient'}
        else:
            payload = {
                'success': True,
                'screenly_asset_id': f'01OUT-{asset_id}',
            }
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(payload),
        )

    page.route(
        '**/api/v2/integrations/screenly/migrate',
        counting_handler,
    )

    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()
    page.locator('#screenly_token').fill('tok')
    page.get_by_role('button', name='Continue').click()
    page.locator('button:has-text("Migrate")').first.click()

    expect(page.get_by_role('button', name='Retry 1 failed')).to_be_visible()
    # First-pass call counts: every asset exactly once.
    assert call_log == {'a1': 1, 'a2': 1, 'a3': 1}, call_log

    page.get_by_role('button', name='Retry 1 failed').click()
    # Retry advances back into the running state and then settles
    # again on done. The all-clear copy is the canonical settle
    # signal — distinct from the errors-present header so we know
    # the retry actually fixed it.
    expect(
        page.get_by_role('heading', name='All assets migrated')
    ).to_be_visible()
    # Successful rows must NOT have been re-queued — only a2 should
    # have a second call.
    assert call_log == {'a1': 1, 'a2': 2, 'a3': 1}, call_log
    # And the second pass succeeded — error text gone, success
    # label now present for all three rows.
    expect(page.get_by_text('transient')).not_to_be_visible()
    expect(page.get_by_text('Uploaded to Screenly')).to_have_count(3)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_empty_library_shows_empty_state(
    reset_assets: None, page: Page
) -> None:
    """A device with no assets should land on a friendly empty state
    in the picker — not a broken table."""
    _mock_validate_valid(page)

    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()
    page.locator('#screenly_token').fill('tok')
    page.get_by_role('button', name='Continue').click()

    expect(page.get_by_text('No assets to migrate')).to_be_visible()
    expect(
        page.get_by_text('This player has no assets yet.')
    ).to_be_visible()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_show_hide_token_toggle(reset_assets: None, page: Page) -> None:
    """The token input defaults to ``type=password`` so the value
    doesn't sit on screen for a passerby, but the operator must be
    able to reveal it once to verify a long copy-paste."""
    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()

    token_input = page.locator('#screenly_token')
    expect(token_input).to_have_attribute('type', 'password')

    page.get_by_role('button', name='Show token').click()
    expect(token_input).to_have_attribute('type', 'text')

    page.get_by_role('button', name='Hide token').click()
    expect(token_input).to_have_attribute('type', 'password')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_empty_state_has_back_button(
    reset_assets: None, page: Page
) -> None:
    """A device with no assets should still offer a way back to the
    token step — without it the operator is stranded on the picker."""
    _mock_validate_valid(page)

    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()
    page.locator('#screenly_token').fill('tok')
    page.get_by_role('button', name='Continue').click()
    expect(page.get_by_text('No assets to migrate')).to_be_visible()

    # The Back button must be inside the same surface card so the
    # operator can't miss it; clicking it returns to the token step.
    page.locator('button:visible:has-text("Back")').click()
    expect(
        page.get_by_role('heading', name='Screenly API token')
    ).to_be_visible()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_uri_basename_shown_for_local_assets(
    seeded_assets: list[Asset], page: Page
) -> None:
    """For local-file assets the picker should show the basename
    (e.g. ``abc1.mp4``) — pasting the full ``/data/anthias_assets/...``
    path is noisy and tells the operator nothing about the asset."""
    _mock_validate_valid(page)

    page.goto(MIGRATE_URL)
    page.get_by_role('button', name='I have a token').click()
    page.locator('#screenly_token').fill('tok')
    page.get_by_role('button', name='Continue').click()
    expect(
        page.get_by_role('heading', name='Choose assets to migrate')
    ).to_be_visible()

    # The picker should NEVER leak the /data/ prefix.
    body = page.content()
    assert '/data/anthias_assets/' not in body

    # But it should show the basenames AND let URL-backed assets
    # keep their full URL.
    expect(page.get_by_text('abc1.mp4')).to_be_visible()
    expect(page.get_by_text('abc3.png')).to_be_visible()
    expect(page.get_by_text('https://example.com/promo')).to_be_visible()
