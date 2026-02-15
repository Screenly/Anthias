import logging
import os
import subprocess
from typing import ClassVar

from lib.device_helper import get_device_type
from settings import settings

VIDEO_TIMEOUT = 20  # secs


def _detect_hdmi_audio_device() -> str:
    """Auto-detect connected HDMI audio device on Pi4/Pi5.

    Pi4 has two HDMI ports:
      - HDMI-A-1 = card 1 = vc4hdmi0
      - HDMI-A-2 = card 2 = vc4hdmi1

    Checks /sys/class/drm/cardN-HDMI-A-N/status to find which port
    is connected and returns the matching ALSA device. Uses
    sysdefault instead of default to bypass PulseAudio/dmix.
    """
    for port, card_name in [
        ('card1-HDMI-A-1', 'vc4hdmi0'),
        ('card1-HDMI-A-2', 'vc4hdmi1'),
    ]:
        status_path = f'/sys/class/drm/{port}/status'
        try:
            if os.path.exists(status_path):
                with open(status_path) as f:
                    if f.read().strip() == 'connected':
                        logging.info(
                            'Detected connected HDMI: %s -> '
                            'sysdefault:CARD=%s',
                            port,
                            card_name,
                        )
                        return f'sysdefault:CARD={card_name}'
        except OSError:
            pass

    logging.warning(
        'No connected HDMI detected, falling back to sysdefault:CARD=vc4hdmi0',
    )
    return 'sysdefault:CARD=vc4hdmi0'


def get_alsa_audio_device() -> str:
    if settings['audio_output'] == 'local':
        if get_device_type() == 'pi5':
            return 'sysdefault:CARD=vc4hdmi0'

        return 'plughw:CARD=Headphones'
    else:
        if get_device_type() in ['pi4', 'pi5']:
            return _detect_hdmi_audio_device()
        elif get_device_type() in ['pi1', 'pi2', 'pi3']:
            return 'sysdefault:CARD=vc4hdmi'
        else:
            return 'sysdefault:CARD=HID'


class MediaPlayer:
    def __init__(self) -> None:
        pass

    def set_asset(self, uri: str, duration: int | str) -> None:
        raise NotImplementedError

    def play(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def is_playing(self) -> bool:
        raise NotImplementedError


class MPVMediaPlayer(MediaPlayer):
    def __init__(self) -> None:
        MediaPlayer.__init__(self)
        self.process: subprocess.Popen[bytes] | None = None
        self.uri: str = ''

    def set_asset(self, uri: str, duration: int | str) -> None:
        self.uri = uri

    def play(self) -> None:
        # Re-read settings each play so the audio_output dropdown takes
        # effect without a viewer restart, matching VLCMediaPlayer.
        settings.load()

        # Pin to 1080p on Pi4-64/Pi5: mpv's default --drm-mode=preferred
        # reads the connector's EDID-preferred mode (4K on most modern
        # TVs) and runs CPU zimg upscale, which drops below real-time
        # on the A72. Software decode of 1080p H.264 fits 4 cores fine.
        device_type = os.environ.get('DEVICE_TYPE', '')
        extra_args: list[str] = []
        if device_type in ('pi4-64', 'pi5'):
            extra_args = [
                '--drm-mode=1920x1080@60',
                '--vd-lavc-threads=4',
            ]

        self.process = subprocess.Popen(
            [
                'mpv',
                '--no-terminal',
                '--vo=drm',
                '--hwdec=auto-safe',
                *extra_args,
                f'--audio-device=alsa/{get_alsa_audio_device()}',
                '--',
                self.uri,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        try:
            if self.process:
                self.process.terminate()
                self.process = None
        except Exception as e:
            logging.error(f'Exception in stop(): {e}')

    def is_playing(self) -> bool:
        if self.process:
            return self.process.poll() is None
        return False


class VLCMediaPlayer(MediaPlayer):
    def __init__(self) -> None:
        MediaPlayer.__init__(self)

        # Imported here so Qt6 boards (which route to MPVMediaPlayer
        # via MediaPlayerProxy) don't need libvlc available just to
        # load this module.
        import vlc

        self._vlc = vlc
        options = [f'--alsa-audio-device={get_alsa_audio_device()}']
        self.instance = vlc.Instance(options)
        self.player = self.instance.media_player_new()

        self.player.audio_output_set('alsa')

    def set_asset(self, uri: str, duration: int | str) -> None:
        self.player.set_mrl(uri)
        settings.load()
        self.player.audio_output_device_set('alsa', get_alsa_audio_device())
        # Use synchronous parse() to pre-load file metadata before play() is
        # called. parse_with_options() is async and returns before metadata is
        # ready, which negates the pre-loading benefit and causes the same
        # startup gap we're trying to reduce.
        self.player.get_media().parse()

    def play(self) -> None:
        self.player.play()

    def stop(self) -> None:
        self.player.stop()

    def is_playing(self) -> bool:
        return self.player.get_state() in [
            self._vlc.State.Playing,
            self._vlc.State.Buffering,
            self._vlc.State.Opening,
        ]


class MediaPlayerProxy:
    INSTANCE: ClassVar[MediaPlayer | None] = None

    @classmethod
    def get_instance(cls) -> MediaPlayer:
        if cls.INSTANCE is None:
            # pi4-64 runs Qt6 + linuxfb like pi5/x86, so VLC's GL/GLES2/XCB
            # outputs have no parent window to draw into. Route it to mpv,
            # which renders straight to KMS via --vo=drm.
            is_pi4_64 = os.environ.get('DEVICE_TYPE') == 'pi4-64'
            if (
                get_device_type() in ['pi1', 'pi2', 'pi3', 'pi4']
                and not is_pi4_64
            ):
                cls.INSTANCE = VLCMediaPlayer()
            else:
                cls.INSTANCE = MPVMediaPlayer()

        return cls.INSTANCE
