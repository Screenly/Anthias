from __future__ import unicode_literals
from builtins import object

import vlc

from lib.raspberry_pi_helper import lookup_raspberry_pi_version
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


class VLCMediaPlayer(MediaPlayer):
    def __init__(self):
        MediaPlayer.__init__(self)

        options = self.__get_options()
        self.instance = vlc.Instance(options)
        self.player = self.instance.media_player_new()

        self.player.audio_output_set('alsa')

    def get_alsa_audio_device(self):
        if settings['audio_output'] == 'local':
            return 'plughw:CARD=Headphones'
        else:
            if lookup_raspberry_pi_version() == 'pi4':
                return 'default:CARD=vc4hdmi0'
            else:
                return 'default:CARD=vc4hdmi'

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
