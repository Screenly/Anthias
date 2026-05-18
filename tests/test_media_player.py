import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anthias_viewer.media_player import (
    MPVMediaPlayer,
    MediaPlayerProxy,
    VLCMediaPlayer,
    get_alsa_audio_device,
)

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
    # proxy of the AnthiasWebview C++ process (issue #2904 —
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


def _last_play_options(bus: Any) -> dict[str, str]:
    """Extract the options dict from the most recent ``playVideo`` call."""
    bus.playVideo.assert_called()
    _, kwargs = bus.playVideo.call_args
    args = bus.playVideo.call_args.args
    # The Python side calls bus.playVideo(uri, options) — positional.
    assert len(args) == 2, args
    return args[1]


def _last_play_uri(bus: Any) -> str:
    args = bus.playVideo.call_args.args
    assert len(args) == 2, args
    return args[0]


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
    """
    import anthias_viewer.media_player as mp

    out = mp._marshal_dbus_options(
        {
            'audio-device': 'alsa/sysdefault:CARD=vc4hdmi0',
            'video-rotate': 90,
            'mute': True,
            'volume': 0.75,
        }
    )
    # Conftest stubs ``gi.repository.GLib`` to a MagicMock on hosts
    # without PyGObject; ``GLib.Variant('s', '...')`` returns a
    # MagicMock whose ``call_args`` records the args. On the real
    # viewer image GLib is real and Variant returns an actual
    # variant — but the call signature is the same.
    from gi.repository import GLib

    assert set(out) == {'audio-device', 'video-rotate', 'mute', 'volume'}
    GLib.Variant.assert_any_call('s', 'alsa/sysdefault:CARD=vc4hdmi0')
    GLib.Variant.assert_any_call('i', 90)
    GLib.Variant.assert_any_call('b', True)
    GLib.Variant.assert_any_call('d', 0.75)


def test_set_browser_bus_injects_module_state() -> None:
    # Smoke test the injection hook the viewer uses after the
    # AnthiasWebview D-Bus handshake. Re-injecting a fresh proxy
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


class _VLCFixtures:
    player: VLCMediaPlayer
    mock_media: Any
    mock_vlc_player: Any
    mock_settings: Any


@pytest.fixture
def vlc() -> Iterator[_VLCFixtures]:
    fixtures = _VLCFixtures()
    with patch.object(VLCMediaPlayer, '__init__', return_value=None):
        fixtures.player = VLCMediaPlayer()

    fixtures.mock_media = MagicMock()
    fixtures.mock_vlc_player = MagicMock()
    fixtures.mock_vlc_player.get_media.return_value = fixtures.mock_media
    fixtures.player.player = fixtures.mock_vlc_player

    patch_settings = patch('anthias_viewer.media_player.settings')
    patch_device_type = patch(
        'anthias_viewer.media_player.get_device_type', return_value='pi4'
    )

    fixtures.mock_settings = patch_settings.start()
    fixtures.mock_settings.__getitem__.return_value = 'hdmi'
    patch_device_type.start()

    try:
        yield fixtures
    finally:
        patch_settings.stop()
        patch_device_type.stop()


def test_set_asset_invokes_parse(vlc: _VLCFixtures) -> None:
    vlc.player.set_asset('file:///test/video.mp4', 30)

    vlc.mock_vlc_player.get_media.assert_called_once()
    vlc.mock_media.parse.assert_called_once()


@pytest.fixture
def reset_media_proxy() -> Iterator[None]:
    MediaPlayerProxy.INSTANCE = None
    try:
        yield
    finally:
        MediaPlayerProxy.INSTANCE = None


@pytest.mark.parametrize('device_type', ['pi1', 'pi2', 'pi3', 'pi4'])
def test_get_instance_returns_vlc_for_pi_devices(
    reset_media_proxy: None, device_type: str
) -> None:
    MediaPlayerProxy.INSTANCE = None
    with (
        patch(
            'anthias_viewer.media_player.get_device_type',
            return_value=device_type,
        ),
        patch.dict('os.environ', {'DEVICE_TYPE': device_type}),
    ):
        with patch.object(VLCMediaPlayer, '__init__', return_value=None):
            instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, VLCMediaPlayer)


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


@pytest.mark.parametrize('rotation', [90, 180, 270])
@patch(
    'anthias_viewer.media_player._detect_hdmi_audio_device',
    return_value='sysdefault:CARD=vc4hdmi0',
)
def test_mpv_passes_video_rotate_on_pi4_64(
    _mock_detect: Any, rotation: int
) -> None:
    """Pi 4 (eglfs, no compositor) has no transform plumbing — the
    video pipeline has to apply rotation itself via the
    ``video-rotate`` option (forwarded to
    ``QGraphicsVideoItem::setRotation`` on the C++ side; sent as an
    ``int``, marshalled to ``Variant('i', …)``)."""
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
    assert options['video-rotate'] == rotation
    assert isinstance(options['video-rotate'], int)


@patch(
    'anthias_viewer.media_player._detect_hdmi_audio_device',
    return_value='sysdefault:CARD=vc4hdmi0',
)
def test_mpv_skips_video_rotate_at_zero_on_pi4_64(
    _mock_detect: Any,
) -> None:
    """0° must NOT emit ``video-rotate=0`` — keeps the D-Bus surface
    unchanged for the 99% of operators who never touch the dropdown,
    so the video pipeline falls back to its own default rather than
    being told to rotate by zero."""
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
            _rotated_mpv_settings(0),
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
