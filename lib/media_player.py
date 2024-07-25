from __future__ import unicode_literals
from builtins import object
from platform import machine

import sh
import vlc

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

    def __get_options(self):
        options = []

        if settings['audio_output'] == 'local':
            options += [
                '--alsa-audio-device=plughw:CARD=Headphones',
            ]

        return options

    def set_asset(self, uri, duration):
        self.player.set_mrl(uri)
        settings.load()

        # @TODO: Refactor this conditional statement.
        if settings['audio_output'] == 'local':
            self.player.audio_output_device_set('alsa', 'plughw:CARD=Headphones')
        elif settings['audio_output'] == 'hdmi':
            self.player.audio_output_device_set('alsa', 'default')

    def play(self):
        self.player.play()

    def stop(self):
        self.player.stop()

    def is_playing(self):
        return self.player.get_state() in [vlc.State.Playing, vlc.State.Buffering, vlc.State.Opening]
