import logging
import signal
import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anthias_viewer.media_player import (
    GstFbdevMediaPlayer,
    MPVMediaPlayer,
    MediaPlayerProxy,
    get_alsa_audio_device,
)
from anthias_viewer import media_player as media_player_module

logging.disable(logging.CRITICAL)


class _MPVFixtures:
    player: MPVMediaPlayer
    mock_settings: Any
    mock_bus: Any


@pytest.fixture
def mpv() -> Iterator[_MPVFixtures]:
    fixtures = _MPVFixtures()
    fixtures.player = MPVMediaPlayer()

    # MPVMediaPlayer now hands play() / stop() through a pydbus
    # proxy of the AnthiasViewer C++ process (issue #2904 —
    # libmpv lives inside the webview). Inject a Mock so tests
    # can assert on the options dict shipped over D-Bus without
    # standing up a real D-Bus session.
    fixtures.mock_bus = MagicMock()
    patch_bus = patch(
        'anthias_viewer.media_player._browser_bus', fixtures.mock_bus
    )
    # Production wraps each option value in GLib.Variant('s', ...)
    # so pydbus can marshal it as ``a{sv}``. Tests bypass that wrap
    # so assertions like ``options['hwdec'] == 'auto-copy'`` keep
    # working — the wrap is integration concern, the option
    # composition is what the tests cover.
    patch_marshal = patch(
        'anthias_viewer.media_player._marshal_dbus_options',
        side_effect=lambda opts: opts,
    )
    patch_settings = patch('anthias_viewer.media_player.settings')
    patch_device_type = patch(
        'anthias_viewer.media_player.get_device_type', return_value='pi4'
    )

    patch_bus.start()
    patch_marshal.start()
    fixtures.mock_settings = patch_settings.start()
    fixtures.mock_settings.__getitem__.return_value = 'hdmi'
    patch_device_type.start()

    try:
        yield fixtures
    finally:
        patch_bus.stop()
        patch_marshal.stop()
        patch_settings.stop()
        patch_device_type.stop()


def _last_play_options(bus: Any) -> dict[str, Any]:
    """Extract the options dict from the most recent ``playVideo`` call.

    Option values can be str / int / bool / float — the C++ side
    receives a ``QVariantMap`` and ``_marshal_dbus_options`` picks
    the GVariant signature by Python type. The ``isinstance``
    assertion narrows the bus-fetched value (which mypy sees as
    ``Any``) back into ``dict[str, Any]`` without using ``cast``.
    """
    bus.playVideo.assert_called()
    args = bus.playVideo.call_args.args
    # The Python side calls bus.playVideo(uri, options) — positional.
    assert len(args) == 2, args
    options = args[1]
    assert isinstance(options, dict)
    return options


def _last_play_uri(bus: Any) -> str:
    args = bus.playVideo.call_args.args
    assert len(args) == 2, args
    uri = args[0]
    assert isinstance(uri, str)
    return uri


@patch(
    'anthias_viewer.media_player._detect_hdmi_audio_device',
    return_value='sysdefault:CARD=vc4hdmi0',
)
def test_play_calls_browser_bus_with_expected_options_on_pi4_64(
    _mock_detect: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}):
        mpv.player.play()

    assert _last_play_uri(mpv.mock_bus) == 'file:///test/video.mp4'
    options = _last_play_options(mpv.mock_bus)
    assert options['audio-device'] == 'alsa/sysdefault:CARD=vc4hdmi0'


def test_play_omits_libmpv_era_options(mpv: _MPVFixtures) -> None:
    # The libmpv era options dict carried ``hwdec``, ``video-sync``,
    # ``vd-lavc-threads``, ``vo``, ``drm-mode``. Under QtMultimedia
    # + libavcodec the decoder + sync are handled inside the
    # backend, so the Python side must not send any of those —
    # defensive, in case the C++ side starts forwarding unknown
    # keys to the player as element properties and one happens
    # to collide.
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}):
        mpv.player.play()
    options = _last_play_options(mpv.mock_bus)
    for legacy in ('hwdec', 'video-sync', 'vd-lavc-threads', 'vo', 'drm-mode'):
        assert legacy not in options, legacy


def test_play_uses_default_alsa_device_on_arm64(mpv: _MPVFixtures) -> None:
    # No portable per-SoC HDMI card name exists across Rockchip /
    # Allwinner / Amlogic, so arm64 defers to ALSA's `default`
    # device rather than the Pi-firmware vc4hdmi* / HID cards the
    # regular dispatch would otherwise pick.
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'arm64'}):
        mpv.player.play()

    options = _last_play_options(mpv.mock_bus)
    assert options['audio-device'] == 'alsa/default'


def test_play_uses_local_audio_device_when_configured(
    mpv: _MPVFixtures,
) -> None:
    mpv.mock_settings.__getitem__.return_value = 'local'
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()

    options = _last_play_options(mpv.mock_bus)
    assert options['audio-device'] == 'alsa/plughw:CARD=Headphones'


def test_play_reloads_settings_each_call(mpv: _MPVFixtures) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()
    mpv.mock_settings.load.assert_called_once()


def test_play_no_bus_logs_and_clears_state(mpv: _MPVFixtures) -> None:
    # If the webview proxy hasn't been injected (failed handshake,
    # crashed webview that hasn't respawned), play() must not
    # latch is_playing() into a false-true state.
    with patch('anthias_viewer.media_player._browser_bus', None):
        mpv.player.set_asset('file:///test/video.mp4', 30)
        mpv.player.play()
    assert not mpv.player.is_playing()


def test_play_bus_failure_clears_playing_state(mpv: _MPVFixtures) -> None:
    # A pydbus transport error on play() (webview just crashed,
    # SIGPIPE on the bus, …) must reset the local flag so a
    # downstream is_playing() call doesn't report a phantom video.
    mpv.mock_bus.playVideo.side_effect = RuntimeError('bus down')
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()
    assert not mpv.player.is_playing()


def test_is_playing_returns_true_after_play(mpv: _MPVFixtures) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()
    assert mpv.player.is_playing()


def test_is_playing_returns_false_after_stop(mpv: _MPVFixtures) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()
    mpv.player.stop()
    assert not mpv.player.is_playing()


def test_is_playing_returns_false_before_play(mpv: _MPVFixtures) -> None:
    assert not mpv.player.is_playing()


def test_stop_calls_browser_bus_stop_video(mpv: _MPVFixtures) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()
    mpv.player.stop()
    mpv.mock_bus.stopVideo.assert_called_once()


def test_stop_without_play_is_safe(mpv: _MPVFixtures) -> None:
    # Defensive: viewer code may call stop() on an idle player
    # (e.g. when rotating from a video back to a webpage and the
    # video had already self-finished). Must not raise even if
    # play() was never called.
    mpv.player.stop()
    mpv.mock_bus.stopVideo.assert_called_once()


def test_stop_without_bus_is_safe(mpv: _MPVFixtures) -> None:
    with patch('anthias_viewer.media_player._browser_bus', None):
        # No bus, no exception.
        mpv.player.stop()
    assert not mpv.player.is_playing()


def test_marshal_dbus_options_wraps_in_glib_variant() -> None:
    """pydbus refuses to coerce a Python scalar to ``GLib.Variant``
    when the slot is declared ``a{sv}`` ("Expected GLib.Variant, but
    got str" at runtime). Regression: a viewer deploy without this
    wrap surfaced the error on every video play. The marshal is
    type-aware so a future non-string option (``int`` / ``bool`` /
    ``float``) round-trips without bespoke wrapping at the call site
    — verify every supported type picks its own GVariant signature.

    Patches ``GLib.Variant`` to a sentinel-returning callable so the
    test passes both with the conftest's MagicMock stub (hosts
    without PyGObject) AND with real PyGObject on the viewer image
    (where ``GLib.Variant`` is a real class and MagicMock helpers
    like ``assert_any_call`` aren't available on it).
    """
    import anthias_viewer.media_player as mp

    # Make the marshal hand back tuples we can compare on, regardless
    # of whether the real GLib.Variant is installed in the test env.
    with patch(
        'gi.repository.GLib.Variant',
        side_effect=lambda signature, value: (signature, value),
    ):
        out = mp._marshal_dbus_options(
            {
                'audio-device': 'alsa/sysdefault:CARD=vc4hdmi0',
                'video-rotate': 90,
                'mute': True,
                'volume': 0.75,
            }
        )

    assert set(out) == {'audio-device', 'video-rotate', 'mute', 'volume'}
    assert out['audio-device'] == ('s', 'alsa/sysdefault:CARD=vc4hdmi0')
    assert out['video-rotate'] == ('i', 90)
    assert out['mute'] == ('b', True)
    assert out['volume'] == ('d', 0.75)


def test_set_browser_bus_injects_module_state() -> None:
    # Smoke test the injection hook the viewer uses after the
    # AnthiasViewer D-Bus handshake. Re-injecting a fresh proxy
    # (post-webview-restart) must replace the previous one rather
    # than appending.
    import anthias_viewer.media_player as mp

    saved = mp._browser_bus
    try:
        first = MagicMock()
        second = MagicMock()
        mp.set_browser_bus(first)
        assert mp.get_browser_bus() is first
        mp.set_browser_bus(second)
        assert mp.get_browser_bus() is second
    finally:
        mp.set_browser_bus(saved)


@pytest.fixture
def alsa_settings() -> Iterator[Any]:
    patch_settings = patch('anthias_viewer.media_player.settings')
    mock_settings = patch_settings.start()
    try:
        yield mock_settings
    finally:
        patch_settings.stop()


def test_local_on_pi5_uses_detected_hdmi_device(alsa_settings: Any) -> None:
    alsa_settings.__getitem__.return_value = 'local'
    with (
        patch(
            'anthias_viewer.media_player.get_device_type', return_value='pi5'
        ),
        patch(
            'anthias_viewer.media_player._detect_hdmi_audio_device',
            return_value='sysdefault:CARD=vc4hdmi1',
        ) as mock_detect,
    ):
        assert get_alsa_audio_device() == 'sysdefault:CARD=vc4hdmi1'
        mock_detect.assert_called_once()


@pytest.mark.parametrize('device_type', ['pi1', 'pi2', 'pi3', 'pi4'])
def test_local_on_other_pi_uses_headphones(
    alsa_settings: Any, device_type: str
) -> None:
    alsa_settings.__getitem__.return_value = 'local'
    with patch(
        'anthias_viewer.media_player.get_device_type',
        return_value=device_type,
    ):
        assert get_alsa_audio_device() == 'plughw:CARD=Headphones'


@pytest.mark.parametrize('device_type', ['pi4', 'pi5'])
def test_hdmi_on_pi4_pi5_uses_detected_device(
    alsa_settings: Any, device_type: str
) -> None:
    alsa_settings.__getitem__.return_value = 'hdmi'
    with (
        patch(
            'anthias_viewer.media_player.get_device_type',
            return_value=device_type,
        ),
        patch(
            'anthias_viewer.media_player._detect_hdmi_audio_device',
            return_value='sysdefault:CARD=vc4hdmi1',
        ) as mock_detect,
    ):
        assert get_alsa_audio_device() == 'sysdefault:CARD=vc4hdmi1'
        mock_detect.assert_called_once()


@pytest.mark.parametrize('device_type', ['pi1', 'pi2', 'pi3'])
def test_hdmi_on_pi1_pi2_pi3_uses_vc4hdmi(
    alsa_settings: Any, device_type: str
) -> None:
    alsa_settings.__getitem__.return_value = 'hdmi'
    with patch(
        'anthias_viewer.media_player.get_device_type',
        return_value=device_type,
    ):
        assert get_alsa_audio_device() == 'sysdefault:CARD=vc4hdmi'


def test_hdmi_on_x86_falls_back_to_hid(alsa_settings: Any) -> None:
    alsa_settings.__getitem__.return_value = 'hdmi'
    with patch(
        'anthias_viewer.media_player.get_device_type', return_value='x86'
    ):
        assert get_alsa_audio_device() == 'sysdefault:CARD=HID'


class _FakeDirEntry:
    """Minimal os.DirEntry stand-in for scandir tests."""

    def __init__(self, name: str, base: str = '/sys/class/drm') -> None:
        self.name = name
        self.path = f'{base}/{name}'


def _patch_drm(entries: list[str], statuses: dict[str, str]) -> Any:
    """Build patches that fake os.scandir() + open() for status reads."""
    from io import StringIO

    fake_entries = [_FakeDirEntry(n) for n in entries]

    def fake_open(path: str, *args: Any, **kwargs: Any) -> Any:
        if path in statuses:
            return StringIO(statuses[path])
        raise FileNotFoundError(path)

    return (
        patch(
            'anthias_viewer.media_player.os.scandir', return_value=fake_entries
        ),
        patch('builtins.open', side_effect=fake_open),
    )


def test_detect_hdmi_returns_first_connected_port() -> None:
    from anthias_viewer.media_player import _detect_hdmi_audio_device

    scandir_patch, open_patch = _patch_drm(
        entries=['card0', 'card1', 'card1-HDMI-A-1', 'card1-HDMI-A-2'],
        statuses={
            '/sys/class/drm/card1-HDMI-A-1/status': 'connected\n',
            '/sys/class/drm/card1-HDMI-A-2/status': 'disconnected\n',
        },
    )
    with scandir_patch, open_patch:
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi0'


def test_detect_hdmi_prefers_first_port_when_both_connected() -> None:
    from anthias_viewer.media_player import _detect_hdmi_audio_device

    scandir_patch, open_patch = _patch_drm(
        entries=['card1-HDMI-A-1', 'card1-HDMI-A-2'],
        statuses={
            '/sys/class/drm/card1-HDMI-A-1/status': 'connected\n',
            '/sys/class/drm/card1-HDMI-A-2/status': 'connected\n',
        },
    )
    with scandir_patch, open_patch:
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi0'


def test_detect_hdmi_prefers_hdmi_a_1_across_mixed_card_indices() -> None:
    """If card0 hosts HDMI-A-2 and card1 hosts HDMI-A-1, HDMI-A-1 still wins.

    Guards against accidentally sorting on the full entry name
    (card0-... < card1-...) instead of the HDMI-A-N suffix.
    """
    from anthias_viewer.media_player import _detect_hdmi_audio_device

    scandir_patch, open_patch = _patch_drm(
        entries=['card0-HDMI-A-2', 'card1-HDMI-A-1'],
        statuses={
            '/sys/class/drm/card0-HDMI-A-2/status': 'connected\n',
            '/sys/class/drm/card1-HDMI-A-1/status': 'connected\n',
        },
    )
    with scandir_patch, open_patch:
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi0'


def test_detect_hdmi_returns_second_port_when_only_it_is_connected() -> None:
    from anthias_viewer.media_player import _detect_hdmi_audio_device

    scandir_patch, open_patch = _patch_drm(
        entries=['card1-HDMI-A-1', 'card1-HDMI-A-2'],
        statuses={
            '/sys/class/drm/card1-HDMI-A-1/status': 'disconnected\n',
            '/sys/class/drm/card1-HDMI-A-2/status': 'connected\n',
        },
    )
    with scandir_patch, open_patch:
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi1'


def test_detect_hdmi_discovers_non_card1_layouts() -> None:
    """DRM card index is probe-order-dependent; HDMI-A-N mapping is stable."""
    from anthias_viewer.media_player import _detect_hdmi_audio_device

    scandir_patch, open_patch = _patch_drm(
        entries=['card2-HDMI-A-1', 'card2-HDMI-A-2'],
        statuses={
            '/sys/class/drm/card2-HDMI-A-1/status': 'disconnected\n',
            '/sys/class/drm/card2-HDMI-A-2/status': 'connected\n',
        },
    )
    with scandir_patch, open_patch:
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi1'


def test_detect_hdmi_falls_back_when_no_status_files() -> None:
    from anthias_viewer.media_player import _detect_hdmi_audio_device

    with patch('anthias_viewer.media_player.os.scandir', return_value=[]):
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi0'


def test_detect_hdmi_logs_only_on_transitions() -> None:
    """Repeated identical results log at DEBUG; transitions re-log loudly.

    Guards against log spam since the helper runs on every play()/
    set_asset(). Manipulates the module-level cache directly so the
    test is independent of state left by other tests.
    """
    import anthias_viewer.media_player as mp

    saved = mp._last_detected_device
    mp._last_detected_device = None
    try:
        # 1. Three identical "no HDMI" calls — WARN once, DEBUG twice.
        with (
            patch('anthias_viewer.media_player.os.scandir', return_value=[]),
            patch('anthias_viewer.media_player.logging.warning') as mock_warn,
            patch('anthias_viewer.media_player.logging.debug') as mock_debug,
        ):
            for _ in range(3):
                assert (
                    mp._detect_hdmi_audio_device()
                    == 'sysdefault:CARD=vc4hdmi0'
                )
            assert mock_warn.call_count == 1
            fallback_debugs = [
                c
                for c in mock_debug.call_args_list
                if 'falling back' in (c.args[0] if c.args else '')
            ]
            assert len(fallback_debugs) == 2

        # 2. Transition: HDMI-A-2 comes online — should re-log at INFO.
        scandir_patch, open_patch = _patch_drm(
            entries=['card1-HDMI-A-1', 'card1-HDMI-A-2'],
            statuses={
                '/sys/class/drm/card1-HDMI-A-1/status': 'disconnected\n',
                '/sys/class/drm/card1-HDMI-A-2/status': 'connected\n',
            },
        )
        with (
            scandir_patch,
            open_patch,
            patch('anthias_viewer.media_player.logging.info') as mock_info,
            patch('anthias_viewer.media_player.logging.debug') as mock_debug,
        ):
            assert mp._detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi1'
            assert mp._detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi1'
            assert mock_info.call_count == 1
            success_debugs = [
                c
                for c in mock_debug.call_args_list
                if 'Detected connected HDMI' in (c.args[0] if c.args else '')
            ]
            assert len(success_debugs) == 1

        # 3. Cable yanked: back to fallback — WARN re-fires on transition.
        with (
            patch('anthias_viewer.media_player.os.scandir', return_value=[]),
            patch('anthias_viewer.media_player.logging.warning') as mock_warn,
        ):
            assert mp._detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi0'
            assert mock_warn.call_count == 1
    finally:
        mp._last_detected_device = saved


def test_detect_hdmi_falls_back_on_oserror() -> None:
    from anthias_viewer.media_player import _detect_hdmi_audio_device

    scandir_patch, _ = _patch_drm(
        entries=['card1-HDMI-A-1', 'card1-HDMI-A-2'],
        statuses={},
    )
    with (
        scandir_patch,
        patch('builtins.open', side_effect=OSError('boom')),
    ):
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi0'


class _GstFixtures:
    player: GstFbdevMediaPlayer
    mock_settings: Any


@pytest.fixture
def gstfb() -> Iterator[_GstFixtures]:
    fixtures = _GstFixtures()
    # Pin the framebuffer geometry/format so the test doesn't depend on
    # the host's /sys/class/graphics/fb0.
    with patch(
        'anthias_viewer.media_player._fb_geometry',
        return_value=(1920, 1080, 'RGB16'),
    ):
        fixtures.player = GstFbdevMediaPlayer()

    patch_settings = patch('anthias_viewer.media_player.settings')
    patch_rotation = patch(
        'anthias_viewer.media_player._screen_rotation', return_value=0
    )
    patch_alsa = patch(
        'anthias_viewer.media_player.get_alsa_audio_device',
        return_value='sysdefault:CARD=vc4hdmi0',
    )
    fixtures.mock_settings = patch_settings.start()
    patch_rotation.start()
    patch_alsa.start()
    try:
        yield fixtures
    finally:
        patch_settings.stop()
        patch_rotation.stop()
        patch_alsa.stop()


def test_set_asset_stores_uri_and_reloads_settings(
    gstfb: _GstFixtures,
) -> None:
    gstfb.player.set_asset('file:///test/video.mp4', 30)
    assert gstfb.player.uri == 'file:///test/video.mp4'
    gstfb.mock_settings.load.assert_called_once()


def _flag(cmd: list[str], name: str) -> str:
    return cmd[cmd.index(name) + 1]


def test_build_command_spawns_looping_player_module(
    gstfb: _GstFixtures,
) -> None:
    gstfb.player.uri = 'file:///test/video.mp4'
    cmd = gstfb.player._build_command()
    # The helper module loops gaplessly in-process and pins aspect-fit
    # caps from the decoder's CAPS event (issue #2987) — the fb
    # geometry and audio device are resolved here and handed over.
    assert cmd[0] == sys.executable
    # Direct-by-path execution: ``-m`` would import the package
    # __init__ (Django settings, redis) in the child and stall the
    # slot start on a Pi 3.
    assert cmd[1].endswith('anthias_viewer/gst_fbdev_player.py')
    assert _flag(cmd, '--uri') == 'file:///test/video.mp4'
    assert _flag(cmd, '--fb-width') == '1920'
    assert _flag(cmd, '--fb-height') == '1080'
    assert _flag(cmd, '--fb-format') == 'RGB16'
    assert _flag(cmd, '--rotation') == '0'
    assert _flag(cmd, '--audio-device') == 'sysdefault:CARD=vc4hdmi0'


def test_build_command_wraps_bare_path_as_file_uri(
    gstfb: _GstFixtures,
) -> None:
    # Local assets store a bare absolute path; playbin's uri property
    # rejects anything without a scheme, so it must be file://-wrapped
    # (regression: a bare path black-screened every local video).
    gstfb.player.uri = '/data/.anthias/assets/abc123'
    cmd = gstfb.player._build_command()
    assert _flag(cmd, '--uri') == 'file:///data/.anthias/assets/abc123'


def test_build_command_passes_through_scheme_uris(
    gstfb: _GstFixtures,
) -> None:
    for uri in (
        # NOSONAR: the http:// case is the point — _as_gst_uri must pass
        # any already-schemed URI through untouched; no connection is made.
        'http://example.com/v.mp4',  # NOSONAR
        'https://host/v.mp4',
        'file:///already/a/uri.mp4',
    ):
        gstfb.player.uri = uri
        cmd = gstfb.player._build_command()
        assert _flag(cmd, '--uri') == uri


def test_build_command_quotes_spaces_in_bare_path(
    gstfb: _GstFixtures,
) -> None:
    gstfb.player.uri = '/data/.anthias/assets/my clip.mp4'
    cmd = gstfb.player._build_command()
    assert _flag(cmd, '--uri') == 'file:///data/.anthias/assets/my%20clip.mp4'


def test_build_command_passes_rotation(gstfb: _GstFixtures) -> None:
    gstfb.player.uri = 'file:///test/video.mp4'
    with patch(
        'anthias_viewer.media_player._screen_rotation', return_value=90
    ):
        cmd = gstfb.player._build_command()
    assert _flag(cmd, '--rotation') == '90'


def test_play_spawns_player_and_is_playing(gstfb: _GstFixtures) -> None:
    gstfb.player.uri = 'file:///test/video.mp4'
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # alive
    with patch(
        'anthias_viewer.media_player.subprocess.Popen', return_value=fake_proc
    ) as mock_popen:
        gstfb.player.play()
    mock_popen.assert_called_once()
    argv = mock_popen.call_args.args[0]
    assert argv[1].endswith('gst_fbdev_player.py')
    # New session group so stop() can kill the player's whole group.
    assert mock_popen.call_args.kwargs['start_new_session'] is True
    assert gstfb.player.is_playing() is True


def test_stop_kills_process_group(gstfb: _GstFixtures) -> None:
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.pid = 4242
    gstfb.player._proc = fake_proc
    with (
        patch('anthias_viewer.media_player.os.getpgid', return_value=4242),
        patch('anthias_viewer.media_player.os.killpg') as mock_killpg,
    ):
        gstfb.player.stop()
    mock_killpg.assert_called_once_with(4242, signal.SIGTERM)
    assert gstfb.player._proc is None


def test_is_playing_false_when_process_exited(gstfb: _GstFixtures) -> None:
    fake_proc = MagicMock()
    fake_proc.poll.return_value = 0  # exited
    gstfb.player._proc = fake_proc
    assert gstfb.player.is_playing() is False


def test_fb_geometry_prefers_vscreeninfo_ioctl() -> None:
    # The visible xres/yres from FBIOGET_VSCREENINFO are what
    # fbdevsink centers against; virtual_size can be larger (panning /
    # double-buffer configs) and must not win when the ioctl works.
    import struct

    def fake_ioctl(fd: Any, request: int, buf: bytearray) -> int:
        assert request == media_player_module._FBIOGET_VSCREENINFO
        struct.pack_into('=2I', buf, 0, 1920, 1080)
        struct.pack_into('=I', buf, 24, 16)
        return 0

    def fake_open(path: str, *a: Any, **k: Any) -> Any:
        if path == '/dev/fb0':
            return MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None)
        data = '3840,2160\n' if 'virtual_size' in path else '16\n'
        return MagicMock(
            __enter__=lambda s: MagicMock(read=lambda: data),
            __exit__=lambda *a: None,
        )

    with (
        patch('builtins.open', side_effect=fake_open),
        patch('fcntl.ioctl', side_effect=fake_ioctl),
    ):
        w, h, fb_fmt = media_player_module._fb_geometry()
    assert (w, h, fb_fmt) == (1920, 1080, 'RGB16')


def test_fb_geometry_falls_back_to_sysfs() -> None:
    def fake_open(path: str, *a: Any, **k: Any) -> Any:
        if path == '/dev/fb0':
            raise OSError('no fb device')
        data = '1280,720\n' if 'virtual_size' in path else '32\n'
        return MagicMock(
            __enter__=lambda s: MagicMock(read=lambda: data),
            __exit__=lambda *a: None,
        )

    with patch('builtins.open', side_effect=fake_open):
        w, h, fb_fmt = media_player_module._fb_geometry()
    assert (w, h, fb_fmt) == (1280, 720, 'BGRx')


@pytest.fixture
def reset_media_proxy() -> Iterator[None]:
    MediaPlayerProxy.INSTANCE = None
    try:
        yield
    finally:
        MediaPlayerProxy.INSTANCE = None


@pytest.mark.parametrize('device_type', ['pi1', 'pi2', 'pi3'])
def test_get_instance_returns_gst_fbdev_for_pi_devices(
    reset_media_proxy: None, device_type: str
) -> None:
    MediaPlayerProxy.INSTANCE = None
    with (
        patch(
            'anthias_viewer.media_player.get_device_type',
            return_value=device_type,
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': device_type}),
        patch.object(GstFbdevMediaPlayer, '__init__', return_value=None),
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, GstFbdevMediaPlayer)


def test_get_instance_pi4_never_routes_to_fbdev(
    reset_media_proxy: None,
) -> None:
    # A Pi 4 reports device_type 'pi4' (Qt6/eglfs). Even when DEVICE_TYPE
    # is missing/mis-set (here: not the 'pi4-64' the override looks for,
    # so force_mpv is False), it must NOT fall to the linuxfb fbdev
    # player — 'pi4' is deliberately absent from the dispatch list.
    MediaPlayerProxy.INSTANCE = None
    with (
        patch(
            'anthias_viewer.media_player.get_device_type', return_value='pi4'
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': 'pi4'}),
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, MPVMediaPlayer)


@pytest.mark.parametrize('device_type', ['pi5', 'x86'])
def test_get_instance_returns_mpv_for_pi5_and_x86(
    reset_media_proxy: None, device_type: str
) -> None:
    MediaPlayerProxy.INSTANCE = None
    with patch(
        'anthias_viewer.media_player.get_device_type',
        return_value=device_type,
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, MPVMediaPlayer)


def test_get_instance_returns_mpv_for_arm64(
    reset_media_proxy: None,
) -> None:
    # device_helper.get_device_type() falls back to 'pi1' on any
    # aarch64 host whose /proc/device-tree/model isn't a Pi regex
    # match — so the override must come from DEVICE_TYPE env, the
    # only thing that distinguishes a Rock Pi / Orange Pi from a
    # genuinely old Pi 1.
    MediaPlayerProxy.INSTANCE = None
    with (
        patch(
            'anthias_viewer.media_player.get_device_type',
            return_value='pi1',
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': 'arm64'}),
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, MPVMediaPlayer)


def test_get_instance_returns_mpv_for_generic_arm64(
    reset_media_proxy: None,
) -> None:
    # Legacy ``generic-arm64`` DEVICE_TYPE label (pre-rename images
    # still in the wild) must also force MPV — the Rock Pi 4 in
    # particular reports this label and would crash on VLC's
    # absent backend without the override.
    MediaPlayerProxy.INSTANCE = None
    with (
        patch(
            'anthias_viewer.media_player.get_device_type',
            return_value='pi1',
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': 'generic-arm64'}),
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, MPVMediaPlayer)


def test_get_instance_returns_mpv_for_pi4_64(reset_media_proxy: None) -> None:
    MediaPlayerProxy.INSTANCE = None
    with (
        patch(
            'anthias_viewer.media_player.get_device_type', return_value='pi4'
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}),
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, MPVMediaPlayer)


def test_get_instance_returns_mpv_for_pi3_64(reset_media_proxy: None) -> None:
    # A 64-bit Pi 3 still reports model 'Raspberry Pi 3', so
    # get_device_type() returns 'pi3' — which is in the Gst-fbdev
    # dispatch list. The baked DEVICE_TYPE='pi3-64' env override is the
    # only signal that this is the Qt6/eglfs image (which ships no
    # GStreamer fbdev stack), so it must force MPV.
    MediaPlayerProxy.INSTANCE = None
    with (
        patch(
            'anthias_viewer.media_player.get_device_type', return_value='pi3'
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': 'pi3-64'}),
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, MPVMediaPlayer)


@patch('anthias_viewer.media_player.get_device_type', return_value='pi5')
def test_get_instance_returns_same_instance(
    _: Any, reset_media_proxy: None
) -> None:
    instance1 = MediaPlayerProxy.get_instance()
    instance2 = MediaPlayerProxy.get_instance()
    assert instance1 is instance2


# ---------------------------------------------------------------------------
# Screen rotation (issue #2856)


def _rotated_mpv_settings(rotation: int) -> Any:
    """Build a settings mock that answers `audio_output` like the
    default fixture but also surfaces `screen_rotation`. Used by the
    mpv rotation tests below."""
    table = {'audio_output': 'hdmi', 'screen_rotation': rotation}
    mock = MagicMock()
    mock.__getitem__.side_effect = lambda key: table[key]
    return mock


@pytest.mark.parametrize('device_type', ['x86', 'arm64', 'pi5'])
@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@patch(
    'anthias_viewer.media_player._detect_hdmi_audio_device',
    return_value='sysdefault:CARD=vc4hdmi0',
)
def test_mpv_never_passes_video_rotate_under_cage(
    _mock_detect: Any,
    rotation: int,
    device_type: str,
) -> None:
    """x86 / arm64 / pi5 run under cage and inherit the compositor
    transform via wlr-randr (issue #2856 — driven from
    src/anthias_viewer/__init__.py). Passing ``video-rotate`` on
    those boards would double-rotate, so the option must be
    omitted regardless of the Settings page value."""
    player = MPVMediaPlayer()
    mock_bus = MagicMock()
    # See history: we patch get_device_type alongside DEVICE_TYPE
    # because get_alsa_audio_device() reads /sys/class/drm when
    # device_type resolves to pi4/pi5.
    audio_device_type = device_type if device_type == 'pi5' else 'x86'
    with (
        patch('anthias_viewer.media_player._browser_bus', mock_bus),
        patch(
            'anthias_viewer.media_player._marshal_dbus_options',
            side_effect=lambda opts: opts,
        ),
        patch(
            'anthias_viewer.media_player.settings',
            _rotated_mpv_settings(rotation),
        ),
        patch(
            'anthias_viewer.media_player.get_device_type',
            return_value=audio_device_type,
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': device_type}),
    ):
        player.set_asset('file:///test/video.mp4', 30)
        player.play()
    options = _last_play_options(mock_bus)
    assert 'video-rotate' not in options


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@patch(
    'anthias_viewer.media_player._detect_hdmi_audio_device',
    return_value='sysdefault:CARD=vc4hdmi0',
)
def test_mpv_never_passes_video_rotate_on_pi4_64(
    _mock_detect: Any, rotation: int
) -> None:
    """Pi 4 (eglfs) rotates the whole screen via QT_QPA_EGLFS_ROTATION
    (set in src/anthias_viewer/__init__.py:_build_webview_env), and the
    QGraphicsVideoItem inherits that transform. Emitting ``video-rotate``
    on top would double-rotate the frames, so the option must be omitted
    at every angle — including 0°."""
    player = MPVMediaPlayer()
    mock_bus = MagicMock()
    with (
        patch('anthias_viewer.media_player._browser_bus', mock_bus),
        patch(
            'anthias_viewer.media_player._marshal_dbus_options',
            side_effect=lambda opts: opts,
        ),
        patch(
            'anthias_viewer.media_player.settings',
            _rotated_mpv_settings(rotation),
        ),
        patch(
            'anthias_viewer.media_player.get_device_type',
            return_value='pi5',
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}),
    ):
        player.set_asset('file:///test/video.mp4', 30)
        player.play()
    options = _last_play_options(mock_bus)
    assert 'video-rotate' not in options


def test_proxy_reset_clears_cached_instance(reset_media_proxy: None) -> None:
    """When the operator changes rotation in Settings, the viewer
    calls MediaPlayerProxy.reset() so the next play() rebuilds VLC
    with the new transform-filter options."""
    fake = MagicMock()
    MediaPlayerProxy.INSTANCE = fake
    MediaPlayerProxy.reset()
    assert MediaPlayerProxy.INSTANCE is None
    fake.stop.assert_called_once()


def test_upload_gate_codecs_are_h264_or_hevc() -> None:
    """QtMultimedia + libavcodec (the ffmpeg-backed Qt 6 media
    plugin) auto-selects the appropriate decoder at playback time
    — V4L2 stateless ``rpi-hevc-dec`` for HEVC and
    ``bcm2835-codec`` for H.264 on Pi 4 via the +rpt1
    ``libav*`` packages, the Hantro G2 on Pi 5, ``rkvdec`` on
    Rock Pi 4, VA-API on x86. The application no longer
    maintains an explicit per-codec hwdec table — but the
    upload gate from PR #2885 still restricts accepted codecs to
    whichever ones the board can play. This test asserts the
    gate's accepted codecs are entirely the h264 / hevc family
    libavcodec's v4l2_request paths cover, so a clip that passes
    the gate is by construction playable. New codecs (av1, vp9,
    mpeg2) need to be both gated on the server side and confirmed
    available in the runtime ffmpeg before the gate is relaxed."""
    from anthias_server.processing import _HW_DECODE_VIDEO_CODECS

    supported = {'h264', 'hevc'}
    for board, codecs in _HW_DECODE_VIDEO_CODECS.items():
        extra = set(codecs) - supported
        assert not extra, (
            f'{board!r} gate accepts {sorted(extra)} which is not '
            f'in the libavcodec h264/hevc dispatch — add the '
            f'matching decoder or remove from the gate.'
        )


# ---------------------------------------------------------------------------
# Webview-gone respawn routing (#3027 / Sentry ANTHIAS-1A)
# ---------------------------------------------------------------------------


class TestPlayRoutesThroughWebviewWrapper:
    """play()/stop() must run their D-Bus calls through the injected
    webview-gone wrapper so a crashed webview is respawned + retried,
    not logged at ERROR with the screen left dark."""

    def test_play_uses_injected_wrapper(self, mpv: _MPVFixtures) -> None:
        # A wrapper that just invokes the call (the happy path) — play
        # must go through it rather than touching the bus directly.
        calls: list[str] = []

        def wrapper(send: Any) -> None:
            calls.append('wrapped')
            send()

        with patch.object(media_player_module, '_send_to_webview', wrapper):
            mpv.player.uri = 'https://example.com/v.mp4'
            mpv.player.play()

        assert calls == ['wrapped']
        mpv.mock_bus.playVideo.assert_called_once()
        assert mpv.player.is_playing() is True

    def test_stop_uses_injected_wrapper(self, mpv: _MPVFixtures) -> None:
        calls: list[str] = []

        def wrapper(send: Any) -> None:
            calls.append('wrapped')
            send()

        with patch.object(media_player_module, '_send_to_webview', wrapper):
            mpv.player.stop()

        assert calls == ['wrapped']
        mpv.mock_bus.stopVideo.assert_called_once()

    def test_play_recovers_when_wrapper_respawns(
        self, mpv: _MPVFixtures
    ) -> None:
        # Simulate the real wrapper: first send hits a webview-gone
        # error, the wrapper respawns and the retried send succeeds.
        attempts = {'n': 0}

        def respawning_wrapper(send: Any) -> None:
            attempts['n'] += 1
            mpv.mock_bus.playVideo.side_effect = [
                RuntimeError('NoReply'),
                None,
            ]
            # The production wrapper retries send() once after respawn;
            # emulate that here so the second call lands.
            try:
                send()
            except RuntimeError:
                send()

        with patch.object(
            media_player_module, '_send_to_webview', respawning_wrapper
        ):
            mpv.player.uri = 'https://example.com/v.mp4'
            mpv.player.play()

        assert mpv.mock_bus.playVideo.call_count == 2
        assert mpv.player.is_playing() is True

    def test_play_clears_state_when_wrapper_reraises(
        self, mpv: _MPVFixtures
    ) -> None:
        # A non-webview-gone error propagates out of the wrapper; play
        # must log + clear _playing so it doesn't think a video is up.
        def reraising_wrapper(send: Any) -> None:
            raise RuntimeError('Disconnected')

        with patch.object(
            media_player_module, '_send_to_webview', reraising_wrapper
        ):
            mpv.player.uri = 'https://example.com/v.mp4'
            mpv.player.play()

        assert mpv.player.is_playing() is False

    def test_direct_call_when_no_wrapper_injected(
        self, mpv: _MPVFixtures
    ) -> None:
        # Standalone / test use (no injection): calls run directly, so
        # the prior behaviour is preserved.
        assert media_player_module._send_to_webview is None
        mpv.player.uri = 'https://example.com/v.mp4'
        mpv.player.play()
        mpv.mock_bus.playVideo.assert_called_once()
        assert mpv.player.is_playing() is True
