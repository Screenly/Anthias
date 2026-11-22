"""The boot splash must hand the display to WiFi setup promptly.

On a first boot with no network the setup portal is usually up well
inside the boot splash's minute, and asset_loop -- the only thing that
paints the setup screen -- did not start until that minute was over.
Testers watched "Detecting network" for minutes with the passphrase
they needed already generated.
"""

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

import anthias_viewer as viewer
from anthias_server.settings import settings
from tests import test_wifi_connect

viewer_wifi_state = test_wifi_connect.viewer_wifi_state
_FakeClock = test_wifi_connect._FakeClock

REPO_ROOT = Path(__file__).resolve().parent.parent


def _hold(portal_up_at: float | None) -> _FakeClock:
    clock = _FakeClock()

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if portal_up_at is not None and clock.now >= portal_up_at:
            viewer._wifi_setup_active = True

    with (
        mock.patch.object(viewer, 'monotonic', clock.monotonic),
        mock.patch.object(viewer, 'sleep', side_effect=sleep),
    ):
        viewer._hold_boot_splash()
    return clock


def test_boot_splash_runs_its_full_delay_without_a_portal(
    viewer_wifi_state: None,
) -> None:
    clock = _hold(portal_up_at=None)
    assert clock.now == viewer.SPLASH_DELAY


def test_boot_splash_yields_as_soon_as_the_portal_is_up(
    viewer_wifi_state: None,
) -> None:
    clock = _hold(portal_up_at=7)
    assert clock.now == 7
    assert clock.now < viewer.SPLASH_DELAY


def test_boot_splash_is_skipped_when_the_portal_is_already_up(
    viewer_wifi_state: None,
) -> None:
    viewer._wifi_setup_active = True
    clock = _hold(portal_up_at=None)
    assert clock.now == 0


@pytest.mark.parametrize(
    ('since_start', 'expected'),
    [
        (0, viewer.WIFI_SETUP_BOOT_POLL_INTERVAL_S),
        (
            viewer.WIFI_SETUP_BOOT_POLL_WINDOW_S - 1,
            viewer.WIFI_SETUP_BOOT_POLL_INTERVAL_S,
        ),
        (
            viewer.WIFI_SETUP_BOOT_POLL_WINDOW_S,
            viewer.WIFI_SETUP_POLL_INTERVAL_S,
        ),
        (86400, viewer.WIFI_SETUP_POLL_INTERVAL_S),
    ],
)
def test_reporter_polls_quickly_only_around_boot(
    since_start: float, expected: float
) -> None:
    assert viewer._wifi_setup_poll_interval(since_start) == expected


def test_boot_poll_is_quicker_than_the_idle_poll() -> None:
    assert (
        viewer.WIFI_SETUP_BOOT_POLL_INTERVAL_S
        < viewer.WIFI_SETUP_POLL_INTERVAL_S
    )


def _run_main(portal_up: bool) -> mock.Mock:
    def hold() -> None:
        viewer._wifi_setup_active = portal_up

    with (
        mock.patch.dict(settings, {'show_splash': True}),
        mock.patch.object(viewer, 'setup'),
        mock.patch.object(viewer, 'ViewerSubscriber'),
        mock.patch.object(viewer, '_publish_cec_availability'),
        mock.patch.object(viewer, '_publish_display_resolution_loop'),
        mock.patch.object(viewer, '_publish_smart_loop'),
        mock.patch.object(viewer, '_wifi_setup_watch_loop'),
        mock.patch.object(viewer, 'wait_for_server'),
        mock.patch.object(viewer, 'wait_for_db_schema'),
        mock.patch.object(viewer, 'Scheduler'),
        mock.patch.object(viewer, 'view_webpage'),
        mock.patch.object(viewer, 'view_image') as view_image,
        mock.patch.object(viewer, 'sleep'),
        mock.patch.object(viewer, '_hold_boot_splash', side_effect=hold),
        mock.patch.object(viewer, 'start_loop'),
    ):
        viewer.main()
    return view_image


def _standby_paints(view_image: mock.Mock) -> list[Any]:
    return [
        c
        for c in view_image.call_args_list
        if c.args == (viewer.STANDBY_SCREEN,)
    ]


def test_main_goes_straight_to_setup_after_the_splash(
    viewer_wifi_state: None,
) -> None:
    # One standby frame before the server is up, and none after the
    # splash: asset_loop's first tick paints the setup screen.
    assert len(_standby_paints(_run_main(portal_up=True))) == 1


def test_main_still_paints_standby_after_the_splash_without_a_portal(
    viewer_wifi_state: None,
) -> None:
    assert len(_standby_paints(_run_main(portal_up=False))) == 2


def test_splash_explains_a_device_with_no_network() -> None:
    template = (
        REPO_ROOT / 'src/anthias_server/app/templates/splash-page.html'
    ).read_text()
    assert 'id="splash-no-network-hint"' in template
    assert 'WiFi' in template
    script = (
        REPO_ROOT / 'src/anthias_server/app/static/src/splash.ts'
    ).read_text()
    assert 'splash-no-network-hint' in script
    assert 'Still looking for a network' in script
