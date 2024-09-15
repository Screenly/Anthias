from __future__ import unicode_literals
from builtins import object

import sh
import vlc

from lib.raspberry_pi_helper import get_device_type
from settings import settings

VIDEO_TIMEOUT = 20  # secs


class MediaPlayer(object):
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
    INSTANCE = None

    def __init__(self):
        MediaPlayer.__init__(self)
        self.run = None
        self.player_args = list()
        self.player_kwargs = dict()

    @classmethod
    def get_instance(cls):
        if cls.INSTANCE is None:
            cls.INSTANCE = cls()
        return cls.INSTANCE

    def set_asset(self, uri, duration):
        self.player_args = ['ffplay', uri, '-autoexit']
        self.player_kwargs = {
            '_bg': True,
            '_ok_code': [0, 124],
        }

    def play(self):
        self.run = sh.Command(self.player_args[0])(*self.player_args[1:], **self.player_kwargs)

    def stop(self):
        try:
            self.run.kill()
        except OSError:
            pass

    def is_playing(self):
        return bool(self.run.process.alive)


class VLCMediaPlayer(MediaPlayer):
    INSTANCE = None

    def __init__(self):
        MediaPlayer.__init__(self)

        options = self.__get_options()
        self.instance = vlc.Instance(options)
        self.player = self.instance.media_player_new()

        self.player.audio_output_set('alsa')

    @classmethod
    def get_instance(cls):
        if cls.INSTANCE is None:
            cls.INSTANCE = VLCMediaPlayer()
        return cls.INSTANCE

    def get_alsa_audio_device(self):
        if settings['audio_output'] == 'local':
            return 'plughw:CARD=Headphones'
        else:
            if get_device_type() == 'pi4':
                return 'default:CARD=vc4hdmi0'
            elif get_device_type() in ['pi1', 'pi2', 'pi3']:
                return 'default:CARD=vc4hdmi'
            else:
                return 'default:CARD=HID'

    def __get_options(self):
        return [
            f'--alsa-audio-device={self.get_alsa_audio_device()}',
        ]

    def set_asset(self, uri, duration):
        self.player.set_mrl(uri)
        settings.load()
        self.player.audio_output_device_set(
            'alsa', self.get_alsa_audio_device())

    def play(self):
        self.player.play()

    def stop(self):
        self.player.stop()

    def is_playing(self):
        return self.player.get_state() in [
            vlc.State.Playing, vlc.State.Buffering, vlc.State.Opening]
