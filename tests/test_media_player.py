import logging
import subprocess
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


@pytest.fixture
def mpv() -> Iterator[_MPVFixtures]:
    fixtures = _MPVFixtures()
    fixtures.player = MPVMediaPlayer()

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


@patch(
    'anthias_viewer.media_player._detect_hdmi_audio_device',
    return_value='sysdefault:CARD=vc4hdmi0',
)
@patch('anthias_viewer.media_player.subprocess.Popen')
def test_play_invokes_popen_with_expected_args(
    mock_popen: Any, _mock_detect: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}):
        mpv.player.play()

    mock_popen.assert_called_once_with(
        [
            'mpv',
            '--no-terminal',
            '--vo=gpu',
            '--gpu-context=wayland',
            '--hwdec=auto-safe',
            '--vd-lavc-threads=4',
            '--audio-device=alsa/sysdefault:CARD=vc4hdmi0',
            '--',
            'file:///test/video.mp4',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_play_tunes_decoder_threads_on_pi4_64(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}):
        mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--vd-lavc-threads=4' in args[0]
    assert '--hwdec=auto-safe' in args[0]
    # --drm-mode pinning is gone: under cage+Wayland the GPU does the
    # scaling, so the A72 no longer runs CPU zimg upscale at 4K.
    assert '--drm-mode=1920x1080@60' not in args[0]


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_play_tunes_decoder_threads_on_pi5(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi5'}):
        mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--vd-lavc-threads=4' in args[0]
    assert '--drm-mode=1920x1080@60' not in args[0]


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_play_omits_pi_tuning_on_x86(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'x86'}):
        mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--drm-mode=1920x1080@60' not in args[0]
    assert '--vd-lavc-threads=4' not in args[0]


@pytest.mark.parametrize('device_type', ['x86', 'arm64', 'pi4-64', 'pi5'])
@patch('anthias_viewer.media_player.subprocess.Popen')
def test_play_uses_wayland_vo_on_all_qt6_boards(
    mock_popen: Any, mpv: _MPVFixtures, device_type: str
) -> None:
    # All Qt6 boards run under cage (a wlroots kiosk compositor); cage
    # holds DRM master, so --vo=drm would be denied. Every Qt6 board
    # must route through --vo=gpu --gpu-context=wayland instead.
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': device_type}):
        mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--vo=gpu' in args[0]
    assert '--gpu-context=wayland' in args[0]
    assert '--vo=drm' not in args[0]


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_play_uses_default_alsa_device_on_arm64(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    # No portable per-SoC HDMI card name exists across Rockchip /
    # Allwinner / Amlogic, so arm64 defers to ALSA's
    # `default` device rather than the Pi-firmware vc4hdmi* / HID
    # cards the regular dispatch would otherwise pick.
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'arm64'}):
        mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--audio-device=alsa/default' in args[0]


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_play_uses_local_audio_device_when_configured(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.mock_settings.__getitem__.return_value = 'local'
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--audio-device=alsa/plughw:CARD=Headphones' in args[0]


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_play_reloads_settings_each_call(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()
    mpv.mock_settings.load.assert_called_once()


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_is_playing_returns_true_when_process_running(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mock_process = MagicMock()
    mock_process.poll.return_value = None
    mock_popen.return_value = mock_process

    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()

    assert mpv.player.is_playing()


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_is_playing_returns_false_when_process_finished(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mock_process = MagicMock()
    mock_process.poll.return_value = 0
    mock_popen.return_value = mock_process

    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()

    assert not mpv.player.is_playing()


def test_is_playing_returns_false_when_no_process(mpv: _MPVFixtures) -> None:
    assert not mpv.player.is_playing()


@patch('anthias_viewer.media_player.subprocess.Popen')
def test_stop_terminates_process(mock_popen: Any, mpv: _MPVFixtures) -> None:
    mock_process = MagicMock()
    mock_popen.return_value = mock_process

    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()
    mpv.player.stop()

    mock_process.terminate.assert_called_once()
    assert mpv.player.process is None


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
