"""A post-setup splash must yield to commands and coalesce portal cycles."""

from collections.abc import Iterator
from threading import Event
from unittest import mock

import pytest

import anthias_viewer as viewer
from anthias_server.settings import settings
from anthias_viewer import playback
from tests import test_wifi_connect

viewer_wifi_state = test_wifi_connect.viewer_wifi_state
# Documentation-only fixture; requests and the webview are mocked.
_ADDRESS = 'http://192.0.2.1'  # NOSONAR (S5332)


@pytest.fixture(autouse=True)
def replay_state(viewer_wifi_state: None) -> Iterator[Event]:
    old_stopped = viewer.loop_is_stopped
    old_blanked = viewer.display_blanked
    viewer.loop_is_stopped = False
    viewer.display_blanked = False
    viewer._wifi_setup_splash_showing = False
    event = Event()
    try:
        with mock.patch.object(playback, 'skip_event', event):
            yield event
    finally:
        viewer.loop_is_stopped = old_stopped
        viewer.display_blanked = old_blanked
        viewer._wifi_setup_splash_showing = False


@pytest.mark.parametrize('phase', ['address_wait', 'display'])
@pytest.mark.parametrize('command', ['blank', 'stop', 'next'])
def test_playback_commands_interrupt_both_splash_phases(
    replay_state: Event, phase: str, command: str
) -> None:
    def interrupt(timeout: float) -> bool:
        if command == 'blank':
            viewer.blank_display()
        elif command == 'stop':
            viewer.stop_playback()
        else:
            playback.skip_asset(viewer.scheduler)
        return replay_state.is_set()

    addresses = [] if phase == 'address_wait' else [_ADDRESS]
    with (
        mock.patch.dict(settings, {'show_splash': True}),
        mock.patch.object(viewer, '_is_wayland_board', return_value=False),
        mock.patch.object(
            viewer, '_post_wifi_setup_addresses', return_value=addresses
        ),
        mock.patch.object(viewer, 'view_webpage'),
        mock.patch.object(replay_state, 'wait', side_effect=interrupt) as wait,
    ):
        viewer._show_post_wifi_setup_splash()

    wait.assert_called_once()
    assert replay_state.is_set()
    assert not viewer._wifi_setup_splash_showing
    if command != 'next':
        assert viewer.loop_is_stopped
    if command == 'blank':
        assert viewer.display_blanked


def _poll_portal(active: bool) -> None:
    response = mock.Mock()
    response.json.return_value = {'active': active}
    with mock.patch('anthias_viewer.requests.get', return_value=response):
        viewer._poll_wifi_setup_once()


@pytest.mark.parametrize('phase', ['address_wait', 'display'])
def test_a_portal_cycle_during_replay_does_not_queue_another_splash(
    replay_state: Event, phase: str
) -> None:
    clock = test_wifi_connect._FakeClock()
    cycled = False

    def wait(timeout: float) -> bool:
        nonlocal cycled
        if not cycled:
            _poll_portal(True)
            _poll_portal(False)
            cycled = True
        clock.sleep(timeout)
        return False

    ready = [_ADDRESS]
    answers = [[], ready] if phase == 'address_wait' else [ready]
    with (
        mock.patch.dict(settings, {'show_splash': True}),
        mock.patch.object(viewer, 'monotonic', clock.monotonic),
        mock.patch.object(replay_state, 'wait', side_effect=wait),
        mock.patch.object(
            viewer, '_post_wifi_setup_addresses', side_effect=answers
        ),
        mock.patch.object(viewer, 'view_webpage') as page,
    ):
        viewer._show_post_wifi_setup_splash()

    page.assert_called_once_with(viewer.SPLASH_PAGE_URL)
    assert clock.now == viewer.SPLASH_DELAY + (
        viewer.POST_WIFI_SETUP_ADDRESS_POLL_S if phase == 'address_wait' else 0
    )
    assert not viewer._wifi_setup_splash_pending
    assert not viewer._wifi_setup_splash_showing
    _poll_portal(True)
    _poll_portal(False)
    assert viewer._wifi_setup_splash_pending


def test_a_portal_that_stays_up_preempts_the_display_window(
    replay_state: Event,
) -> None:
    def wait(timeout: float) -> bool:
        _poll_portal(True)
        return False

    with (
        mock.patch.dict(settings, {'show_splash': True}),
        mock.patch.object(replay_state, 'wait', side_effect=wait) as waiting,
        mock.patch.object(
            viewer,
            '_post_wifi_setup_addresses',
            return_value=[_ADDRESS],
        ),
        mock.patch.object(viewer, 'view_webpage'),
    ):
        viewer._show_post_wifi_setup_splash()

    waiting.assert_called_once()
    assert viewer._wifi_setup_active
    assert not viewer._wifi_setup_splash_showing
    _poll_portal(False)
    assert viewer._wifi_setup_splash_pending


def test_a_failed_replay_does_not_block_future_replays() -> None:
    with (
        mock.patch.dict(settings, {'show_splash': True}),
        mock.patch.object(
            viewer,
            'view_webpage',
            side_effect=RuntimeError('webview unavailable'),
        ),
        pytest.raises(RuntimeError, match='webview unavailable'),
    ):
        viewer._show_post_wifi_setup_splash()

    assert not viewer._wifi_setup_splash_showing
    _poll_portal(True)
    _poll_portal(False)
    assert viewer._wifi_setup_splash_pending
