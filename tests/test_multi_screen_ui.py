"""
Browser-driven checks of the multi-screen management UI (Playwright).

Covers the controller's Screens page, the Settings → Multi-screen
management section (standalone and waiting for approval) and a managed
screen's "Managed by …" page. Under ``MARKETING_SCREENSHOTS=1`` the
same tests are the source of the ``screens*``, ``multi-screen-*`` and
``managed`` captures the website's multi-screen docs page and home
page slider use (see ``tests/conftest.py`` for the fixture).

Nothing here talks to a second device. The controller and managed
states are seeded straight into the database uvicorn reads (the suite
runs with ``ANTHIAS_INTEGRATION_TEST=1``), and the settings uvicorn
reads are written to its own config file. The pytest process's
``settings`` singleton can't be used for that: the root conftest points
it at a per-session temp file so unit tests never touch the real one.

Seeded screens look connected (a live push connection, just checked),
so the Celery worker's status poll and housekeeping leave them alone
for the length of a test.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from playwright.sync_api import Page, expect

from anthias_server.app.models import ApiToken, Asset, ManagementController
from anthias_server.controller.models import (
    AuditEntry,
    JoinRequest,
    Node,
    NodeStatus,
    PublishState,
)
from anthias_server.settings import AnthiasSettings
from tests._seed_data import home_seed_assets
from tests.conftest import MarketingShotFn

BASE_URL = 'http://localhost:8080'
LOGIN_URL = f'{BASE_URL}/login/'
SCREENS_PATH = '/screens/'
SETTINGS_PATH = '/settings/'
SETTINGS_URL = f'{BASE_URL}{SETTINGS_PATH}'

DEFAULT_TIMEOUT_MS = 15_000

OPERATOR = 'peter'
# A throwaway account in the test database.
OPERATOR_PASSWORD = 'tps-report-cover-sheet'  # NOSONAR
CONTROLLER_NAME = 'Initech HQ'
CHECK_CODE = 'K7QX-4MRA-2ZTD'


def _fp() -> str:
    return secrets.token_hex(32)


def _healthy(
    free: str, free_bytes: int, ram_mib: int, uptime_hours: float
) -> dict[str, Any]:
    return {
        'free_space': free,
        'free_space_bytes': free_bytes,
        'memory_total_mib': 4096,
        'memory_available_mib': ram_mib,
        'loadavg': 0.4,
        'uptime_hours': uptime_hours,
        'under_voltage_supported': True,
        'under_voltage_active': False,
        'under_voltage_seen': False,
        'storage_status': 'ok',
        'display_power': 'on',
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_timeout(page: Page) -> None:
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)


@pytest.fixture
def server_settings(page: Page) -> Iterator[AnthiasSettings]:
    """The settings uvicorn reads, restored byte for byte afterwards.

    A fresh ``AnthiasSettings`` resolves ``$HOME/.anthias/anthias.conf``
    on its own, which is the file the server process uses.

    The server keeps its copy in memory and re-reads the file only in a
    few views, and ``@authorized`` checks ``auth_backend`` before any of
    them run. So after restoring the file, Settings (which re-reads it)
    is opened, signing in first if the server still asks: otherwise the
    next test would find authentication on.
    """
    server = AnthiasSettings()
    conf = Path(server.conf_file)
    original = conf.read_bytes() if conf.exists() else None
    try:
        yield server
    finally:
        if original is None:
            conf.unlink(missing_ok=True)
        else:
            conf.write_bytes(original)
        page.goto(SETTINGS_URL)
        if page.url.startswith(LOGIN_URL):
            _submit_login(page)
            page.wait_for_url(SETTINGS_URL)
        page.goto(BASE_URL)
        expect(page).to_have_url(f'{BASE_URL}/')


@pytest.fixture
def auth_on(server_settings: AnthiasSettings) -> AnthiasSettings:
    User.objects.create_superuser(
        username=OPERATOR, password=OPERATOR_PASSWORD
    )
    server_settings['auth_backend'] = 'auth_basic'
    server_settings['controller_mode'] = False
    server_settings['player_name'] = CONTROLLER_NAME
    server_settings.save()
    return server_settings


@pytest.fixture
def controller(auth_on: AnthiasSettings) -> AnthiasSettings:
    auth_on['controller_mode'] = True
    auth_on.save()
    return auth_on


@pytest.fixture
def playlist() -> None:
    """The home-page seeds that are URLs: a publish includes all of
    them, where a seeded local file would be listed as missing."""
    Asset.objects.all().delete()
    for fields in home_seed_assets():
        if fields['uri'].startswith('https://'):
            Asset.objects.create(**fields)


@pytest.fixture
def screens() -> list[Node]:
    """Five managed screens covering every state worth showing."""
    now = timezone.now()
    common: dict[str, Any] = {
        'token_ciphertext': 'not-a-real-token',
        'last_checked_at': now,
        'version': 'v2026.11.1',
    }
    live: dict[str, Any] = {
        'status': NodeStatus.ONLINE,
        'last_seen_at': now - timedelta(seconds=20),
        'health_at': now - timedelta(minutes=2),
        'push_connection': 'seeded',
        'push_seen_at': now,
        'publish_state': PublishState.OK,
        'published_at': now - timedelta(minutes=12),
        'publish_detail': '0 added, 1 updated, 3 unchanged, 0 removed',
    }
    rows = [
        {
            **live,
            'name': 'Lobby',
            'host': '192.168.10.21',
            'device_model': 'Raspberry Pi 5 Model B Rev 1.0',
            'health': _healthy('48.2 GB', 48_200_000_000, 3412, 310),
        },
        {
            **live,
            'name': 'Break room',
            'host': '192.168.10.22',
            'device_model': 'Raspberry Pi 4 Model B Rev 1.5',
            'health': _healthy('21.7 GB', 21_700_000_000, 1630, 76),
        },
        {
            **live,
            'name': 'Conference room B',
            'host': '192.168.10.23',
            'device_model': 'Raspberry Pi 4 Model B Rev 1.4',
            'health': {
                **_healthy('12.9 GB', 12_900_000_000, 1290, 30),
                'under_voltage_seen': True,
            },
        },
        {
            **live,
            'name': 'Reception',
            'host': '192.168.10.24',
            'device_model': 'Generic x86_64 PC',
            'health': _healthy('211.4 GB', 211_400_000_000, 6120, 1100),
            'publish_state': PublishState.RUNNING,
            'published_at': now - timedelta(days=1),
            'publish_detail': '',
        },
        {
            'name': 'Warehouse',
            'host': '192.168.10.31',
            'device_model': 'Raspberry Pi 3 Model B Plus Rev 1.3',
            'status': NodeStatus.OFFLINE,
            'status_detail': 'No heartbeat for 3 minutes.',
            'last_seen_at': now - timedelta(hours=2),
            'publish_state': PublishState.OK,
            'published_at': now - timedelta(days=2),
            'publish_detail': '2 added, 0 updated, 2 unchanged, 0 removed',
        },
    ]
    return [
        Node.objects.create(**common, **row, fingerprint=_fp()) for row in rows
    ]


@pytest.fixture
def join_request() -> JoinRequest:
    now = timezone.now()
    return JoinRequest.objects.create(
        request_id=secrets.token_hex(16),
        name='Mezzanine',
        host='192.168.10.25',
        fingerprint=_fp(),
        token_ciphertext='not-a-real-token',
        csr_pem='not-a-real-csr',
        version='v2026.11.1',
        device_model='Raspberry Pi 5 Model B Rev 1.0',
        check_code=CHECK_CODE,
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=24),
    )


@pytest.fixture
def activity(screens: list[Node]) -> None:
    now = timezone.now()
    by_name = {node.name: node for node in screens}
    entries = [
        (
            'join request',
            'ok',
            'Mezzanine',
            '',
            1,
            'asked to join from 192.168.10.25, check code ' + CHECK_CODE,
        ),
        (
            'publish',
            'ok',
            'Lobby',
            OPERATOR,
            12,
            '0 added, 1 updated, 3 unchanged, 0 removed; 4.2 MB uploaded',
        ),
        (
            'publish',
            'ok',
            'Break room',
            OPERATOR,
            12,
            '0 added, 1 updated, 3 unchanged, 0 removed; 4.2 MB uploaded',
        ),
        (
            'publish',
            'ok',
            'Conference room B',
            OPERATOR,
            12,
            '0 added, 1 updated, 3 unchanged, 0 removed; 4.2 MB uploaded',
        ),
        ('display_on', 'ok', 'Lobby', OPERATOR, 95, 'display on'),
        (
            'approve',
            'ok',
            'Reception',
            OPERATOR,
            60 * 26,
            'approved (check code 9PLC-2VNE-HQ4A); certificate issued',
        ),
    ]
    for action, outcome, name, actor, minutes, detail in entries:
        AuditEntry.objects.create(
            created_at=now - timedelta(minutes=minutes),
            action=action,
            outcome=outcome,
            node=by_name.get(name),
            node_name=name,
            actor=actor,
            detail=detail,
        )


def _token(name: str) -> ApiToken:
    user = User.objects.get(username=OPERATOR)
    return ApiToken.objects.create(
        name=name,
        prefix='ant_seed',
        token_hash=secrets.token_hex(32),
        user=user,
    )


@pytest.fixture
def pending_join(auth_on: AnthiasSettings) -> ManagementController:
    auth_on['player_name'] = 'Mezzanine'
    auth_on.save()
    return ManagementController.objects.create(
        name=CONTROLLER_NAME,
        url='https://192.168.10.20:8443',
        fingerprint=_fp(),
        token=_token(f'Controller {CONTROLLER_NAME}'),
        paired_at=timezone.now() - timedelta(minutes=1),
        approved=False,
        request_id=secrets.token_hex(16),
        check_code=CHECK_CODE,
    )


@pytest.fixture
def managed(auth_on: AnthiasSettings) -> ManagementController:
    auth_on['player_name'] = 'Lobby'
    auth_on.save()
    return ManagementController.objects.create(
        name=CONTROLLER_NAME,
        url='https://192.168.10.20:8443',
        fingerprint=_fp(),
        token=_token(f'Controller {CONTROLLER_NAME}'),
        paired_at=timezone.now() - timedelta(days=3),
        approved=True,
        request_id=secrets.token_hex(16),
        check_code=CHECK_CODE,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _submit_login(page: Page) -> None:
    page.locator('#username').fill(OPERATOR)
    page.locator('#password').fill(OPERATOR_PASSWORD)
    page.locator('.auth-submit').click()


def _sign_in(page: Page, path: str) -> None:
    """Sign in through the login form and land on ``path``."""
    page.goto(f'{LOGIN_URL}?next={path}')
    _submit_login(page)
    page.wait_for_url(f'{BASE_URL}{path}')


def _scroll_to_top(page: Page, selector: str) -> None:
    """Put ``selector`` at the top of the viewport, below the navbar."""
    page.evaluate(
        """(selector) => {
            const el = document.querySelector(selector);
            const nav = document.querySelector('.app-nav');
            const offset = nav ? nav.getBoundingClientRect().height : 0;
            const top = el.getBoundingClientRect().top + window.scrollY;
            window.scrollTo(0, Math.max(0, top - offset - 16));
        }""",
        selector,
    )


def _hide_toasts(page: Page) -> None:
    page.add_style_tag(content='.app-toast-stack { display: none; }')


# ---------------------------------------------------------------------------
# Screens page (controller)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_screens_page_lists_screens_and_requests(
    controller: AnthiasSettings,
    playlist: None,
    screens: list[Node],
    join_request: JoinRequest,
    activity: None,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """A controller's Screens page shows the pending request with its
    check code, every screen with status and health, and the publish
    and activity sections. Source of ``screens`` and
    ``screens-publish``."""
    _sign_in(page, SCREENS_PATH)
    expect(
        page.get_by_role('heading', name='Screens', exact=True).first
    ).to_be_visible()

    requests = page.locator('#join-requests')
    expect(requests).to_contain_text('Mezzanine')
    expect(requests.locator('.screens__check-code')).to_have_text(CHECK_CODE)

    table = page.locator('#screens-table')
    for node in screens:
        expect(table.locator(f'tr[data-screen="{node.pk}"]')).to_contain_text(
            node.name
        )
    expect(table).to_contain_text('4 of 5 online')
    expect(table).to_contain_text('1 needs attention')
    expect(
        table.locator(f'tr[data-screen="{screens[2].pk}"] .screens__warn')
    ).to_have_text('Under-voltage since boot')
    expect(
        table.locator(f'tr[data-screen="{screens[4].pk}"]')
    ).to_have_attribute('data-status', 'offline')

    expect(page.locator('#btn-publish-all')).to_be_visible()
    publish = page.locator('#publish')
    expect(publish).to_contain_text("this screen's 4 assets")
    expect(publish).not_to_contain_text('Not included right now')
    expect(page.locator('#screens-activity')).to_contain_text(
        'asked to join from 192.168.10.25'
    )
    # With screens joined, the join information is folded away.
    expect(page.locator('#join-information')).not_to_have_attribute('open', '')

    _hide_toasts(page)
    marketing_screenshot('screens')

    _scroll_to_top(page, '#publish')
    marketing_screenshot('screens-publish')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_screens_page_first_run_shows_join_information(
    controller: AnthiasSettings,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """Before any screen joins, the Screens page walks through adding
    one and shows the join information unfolded. Source of
    ``screens-join``."""
    _sign_in(page, SCREENS_PATH)
    expect(page.locator('#screens-empty')).to_contain_text(
        'Add your first screen'
    )
    expect(page.locator('#join-information')).to_have_attribute('open', '')
    snippet = page.locator('#join-snippet')
    expect(snippet).to_have_value(
        # Base64 JSON after the prefix; only its shape is stable.
        re.compile(r'^anthias-join:[A-Za-z0-9_-]+$')
    )
    expect(page.locator('#join-expiry')).to_contain_text('Valid until')

    _hide_toasts(page)
    marketing_screenshot('screens-join')


# ---------------------------------------------------------------------------
# Settings → Multi-screen management (screen side)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_settings_management_offers_join_and_controller(
    auth_on: AnthiasSettings,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """With authentication on, a standalone screen can either join a
    controller or become one. Source of ``multi-screen-settings``."""
    _sign_in(page, SETTINGS_PATH)
    section = page.locator('#management')
    expect(section.locator('#btn-join-controller')).to_be_visible()
    expect(section.locator('#btn-controller-on')).to_be_visible()

    _scroll_to_top(page, '#management')
    marketing_screenshot('multi-screen-settings')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_settings_management_shows_check_code_while_waiting(
    pending_join: ManagementController,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """A screen waiting for approval shows the controller's name and
    the check code to compare. Source of ``multi-screen-waiting``."""
    _sign_in(page, SETTINGS_PATH)
    section = page.locator('#management')
    expect(section).to_contain_text(
        f'Waiting for approval on {CONTROLLER_NAME}'
    )
    expect(section.locator('#management-check-code')).to_have_text(CHECK_CODE)
    expect(section.locator('#btn-cancel-join')).to_be_visible()

    _scroll_to_top(page, '#management')
    marketing_screenshot('multi-screen-waiting')


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_managed_screen_names_its_controller(
    managed: ManagementController,
    page: Page,
    marketing_screenshot: MarketingShotFn,
) -> None:
    """Signed out, a managed screen's web interface names its
    controller and links to it; signing in there is only for leaving.
    Source of ``managed``."""
    page.goto(LOGIN_URL)
    expect(
        page.get_by_role('heading', name=f'Managed by {CONTROLLER_NAME}')
    ).to_be_visible()
    expect(
        page.get_by_role('link', name=f'Open {CONTROLLER_NAME}')
    ).to_have_attribute('href', 'https://192.168.10.20/screens/')
    expect(page.locator('#username')).to_be_visible()
    marketing_screenshot('managed')
