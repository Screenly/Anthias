import logging
import subprocess
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from viewer.media_player import (
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
def mpv(monkeypatch: pytest.MonkeyPatch) -> Iterator[_MPVFixtures]:
    fixtures = _MPVFixtures()
    fixtures.player = MPVMediaPlayer()

    patch_settings = patch('viewer.media_player.settings')
    patch_device_type = patch(
        'viewer.media_player.get_device_type', return_value='pi4'
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
    'viewer.media_player._detect_hdmi_audio_device',
    return_value='sysdefault:CARD=vc4hdmi0',
)
@patch('viewer.media_player.subprocess.Popen')
def test_play_invokes_popen_with_expected_args(
    mock_popen: Any, _mock_detect: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4'}):
        mpv.player.play()

    mock_popen.assert_called_once_with(
        [
            'mpv',
            '--no-terminal',
            '--vo=drm',
            '--hwdec=auto-safe',
            '--audio-device=alsa/sysdefault:CARD=vc4hdmi0',
            '--',
            'file:///test/video.mp4',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@patch('viewer.media_player.subprocess.Popen')
def test_play_pins_1080p_mode_on_pi4_64(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}):
        mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--drm-mode=1920x1080@60' in args[0]
    assert '--vd-lavc-threads=4' in args[0]
    assert '--hwdec=auto-safe' in args[0]
    assert '--hwdec=v4l2m2m-copy' not in args[0]


@patch('viewer.media_player.subprocess.Popen')
def test_play_pins_1080p_mode_on_pi5(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'pi5'}):
        mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--drm-mode=1920x1080@60' in args[0]
    assert '--vd-lavc-threads=4' in args[0]


@patch('viewer.media_player.subprocess.Popen')
def test_play_does_not_pin_mode_on_x86(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    with patch.dict('os.environ', {'DEVICE_TYPE': 'x86'}):
        mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--drm-mode=1920x1080@60' not in args[0]
    assert '--vd-lavc-threads=4' not in args[0]


@patch('viewer.media_player.subprocess.Popen')
def test_play_uses_local_audio_device_when_configured(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.mock_settings.__getitem__.return_value = 'local'
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()

    args, _ = mock_popen.call_args
    assert '--audio-device=alsa/plughw:CARD=Headphones' in args[0]


@patch('viewer.media_player.subprocess.Popen')
def test_play_reloads_settings_each_call(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()
    mpv.mock_settings.load.assert_called_once()


@patch('viewer.media_player.subprocess.Popen')
def test_is_playing_returns_true_when_process_running(
    mock_popen: Any, mpv: _MPVFixtures
) -> None:
    mock_process = MagicMock()
    mock_process.poll.return_value = None
    mock_popen.return_value = mock_process

    mpv.player.set_asset('file:///test/video.mp4', 30)
    mpv.player.play()

    assert mpv.player.is_playing()


@patch('viewer.media_player.subprocess.Popen')
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


@patch('viewer.media_player.subprocess.Popen')
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
    patch_settings = patch('viewer.media_player.settings')
    mock_settings = patch_settings.start()
    try:
        yield mock_settings
    finally:
        patch_settings.stop()


def test_local_on_pi5_uses_hdmi_card(alsa_settings: Any) -> None:
    alsa_settings.__getitem__.return_value = 'local'
    with patch('viewer.media_player.get_device_type', return_value='pi5'):
        assert get_alsa_audio_device() == 'sysdefault:CARD=vc4hdmi0'


@pytest.mark.parametrize('device_type', ['pi1', 'pi2', 'pi3', 'pi4'])
def test_local_on_other_pi_uses_headphones(
    alsa_settings: Any, device_type: str
) -> None:
    alsa_settings.__getitem__.return_value = 'local'
    with patch(
        'viewer.media_player.get_device_type',
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
            'viewer.media_player.get_device_type',
            return_value=device_type,
        ),
        patch(
            'viewer.media_player._detect_hdmi_audio_device',
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
        'viewer.media_player.get_device_type',
        return_value=device_type,
    ):
        assert get_alsa_audio_device() == 'sysdefault:CARD=vc4hdmi'


def test_hdmi_on_x86_falls_back_to_hid(alsa_settings: Any) -> None:
    alsa_settings.__getitem__.return_value = 'hdmi'
    with patch('viewer.media_player.get_device_type', return_value='x86'):
        assert get_alsa_audio_device() == 'sysdefault:CARD=HID'


def test_detect_hdmi_returns_first_connected_port() -> None:
    from viewer.media_player import _detect_hdmi_audio_device

    statuses = {
        '/sys/class/drm/card1-HDMI-A-1/status': 'connected\n',
        '/sys/class/drm/card1-HDMI-A-2/status': 'disconnected\n',
    }

    def fake_exists(path: str) -> bool:
        return path in statuses

    def fake_open(path: str, *args: Any, **kwargs: Any) -> Any:
        from io import StringIO

        return StringIO(statuses[path])

    with (
        patch('viewer.media_player.os.path.exists', side_effect=fake_exists),
        patch('builtins.open', side_effect=fake_open),
    ):
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi0'


def test_detect_hdmi_returns_second_port_when_only_it_is_connected() -> None:
    from viewer.media_player import _detect_hdmi_audio_device

    statuses = {
        '/sys/class/drm/card1-HDMI-A-1/status': 'disconnected\n',
        '/sys/class/drm/card1-HDMI-A-2/status': 'connected\n',
    }

    def fake_exists(path: str) -> bool:
        return path in statuses

    def fake_open(path: str, *args: Any, **kwargs: Any) -> Any:
        from io import StringIO

        return StringIO(statuses[path])

    with (
        patch('viewer.media_player.os.path.exists', side_effect=fake_exists),
        patch('builtins.open', side_effect=fake_open),
    ):
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi1'


def test_detect_hdmi_falls_back_when_no_status_files() -> None:
    from viewer.media_player import _detect_hdmi_audio_device

    with patch('viewer.media_player.os.path.exists', return_value=False):
        assert _detect_hdmi_audio_device() == 'sysdefault:CARD=vc4hdmi0'


def test_detect_hdmi_falls_back_on_oserror() -> None:
    from viewer.media_player import _detect_hdmi_audio_device

    with (
        patch('viewer.media_player.os.path.exists', return_value=True),
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

    patch_settings = patch('viewer.media_player.settings')
    patch_device_type = patch(
        'viewer.media_player.get_device_type', return_value='pi4'
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
            'viewer.media_player.get_device_type',
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
        'viewer.media_player.get_device_type',
        return_value=device_type,
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, MPVMediaPlayer)


def test_get_instance_returns_mpv_for_pi4_64(reset_media_proxy: None) -> None:
    MediaPlayerProxy.INSTANCE = None
    with (
        patch('viewer.media_player.get_device_type', return_value='pi4'),
        patch.dict('os.environ', {'DEVICE_TYPE': 'pi4-64'}),
    ):
        instance = MediaPlayerProxy.get_instance()
    assert isinstance(instance, MPVMediaPlayer)


@patch('viewer.media_player.get_device_type', return_value='pi5')
def test_get_instance_returns_same_instance(
    _: Any, reset_media_proxy: None
) -> None:
    instance1 = MediaPlayerProxy.get_instance()
    instance2 = MediaPlayerProxy.get_instance()
    assert instance1 is instance2
