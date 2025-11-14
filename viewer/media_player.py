from __future__ import unicode_literals

import logging
import subprocess

import vlc

from lib.device_helper import get_device_type
from settings import settings

VIDEO_TIMEOUT = 20  # secs


class MediaPlayer:
    def __init__(self):
        pass

    def set_asset(self, uri, duration):
        raise NotImplementedError

    def play(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def is_playing(self):
        raise NotImplementedError


class FFMPEGMediaPlayer(MediaPlayer):
    def __init__(self):
        MediaPlayer.__init__(self)
        self.process = None

    def set_asset(self, uri, duration):
        self.uri = uri

    def __get_rotation_filter(self):
        rotation = settings.get('rotate_display', 0)
        rotation_angles = {
            0: '',
            90: 'transpose=1',
            180: 'hflip,vflip',
            270: 'transpose=2',
        }
        return rotation_angles.get(rotation, '')

    def play(self):
        rotation_filter = self.__get_rotation_filter()
        filters = []
        if rotation_filter:
            filters.append(rotation_filter)

        vf_arg = ','.join(filters) if filters else None

        cmd = [
            'ffplay',
            '-autoexit',
        ]
        if vf_arg:
            cmd.extend(['-vf', vf_arg])
        cmd.append(self.uri)

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self):
        try:
            if self.process:
                self.process.terminate()
                self.process = None
        except Exception as e:
            logging.error(f'Exception in stop(): {e}')

    def is_playing(self):
        if self.process:
            return self.process.poll() is None
        return False


class VLCMediaPlayer(MediaPlayer):
    def __init__(self):
        MediaPlayer.__init__(self)

        options = self.__get_options()
        self.instance = vlc.Instance(options)
        self.player = self.instance.media_player_new()

        self.player.audio_output_set('alsa')

    def get_alsa_audio_device(self):
        if settings['audio_output'] == 'local':
            if get_device_type() == 'pi5':
                return 'default:CARD=vc4hdmi0'

            return 'plughw:CARD=Headphones'
        else:
            if get_device_type() in ['pi4', 'pi5']:
                return 'default:CARD=vc4hdmi0'
            elif get_device_type() in ['pi1', 'pi2', 'pi3']:
                return 'default:CARD=vc4hdmi'
            else:
                return 'default:CARD=HID'

    def __get_options(self):
        rotation = settings.get('rotate_display', 0)
        rotation_angles = {
            0: 0,
            1: 90,
            2: 180,
            3: 270,
        }
        angle = rotation_angles.get(rotation, 0)

        return [
            f'--alsa-audio-device={self.get_alsa_audio_device()}',
            f'--video-filter=rotate{{angle={angle}}}',
        ]

    def set_asset(self, uri, duration):
        self.player.set_mrl(uri)
        settings.load()
        self.player.audio_output_device_set(
            'alsa', self.get_alsa_audio_device()
        )

    def play(self):
        self.player.play()

    def stop(self):
        self.player.stop()

    def is_playing(self):
        return self.player.get_state() in [
            vlc.State.Playing,
            vlc.State.Buffering,
            vlc.State.Opening,
        ]


class MediaPlayerProxy:
    INSTANCE = None

    @classmethod
    def get_instance(cls):
        if cls.INSTANCE is None:
            if get_device_type() in ['pi1', 'pi2', 'pi3', 'pi4']:
                cls.INSTANCE = VLCMediaPlayer()
            else:
                cls.INSTANCE = FFMPEGMediaPlayer()

        return cls.INSTANCE
