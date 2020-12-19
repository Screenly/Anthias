from platform import machine

import sh
import vlc

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


class VLCMediaPlayer(MediaPlayer):
    def __init__(self):
        MediaPlayer.__init__(self)
        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()

        self.player.audio_output_set('alsa')

    def set_asset(self, uri, duration):
        # @TODO: HDMI or 3.5mm jack audio output
        self.player.set_mrl(uri)

    def play(self):
        self.player.play()

    def stop(self):
        self.player.stop()

    def is_playing(self):
        return self.player.get_state() in [vlc.State.Playing, vlc.State.Buffering, vlc.State.Opening]


class OMXMediaPlayer(MediaPlayer):
    def __init__(self):
        MediaPlayer.__init__(self)
        self._arch = machine()

        self._run = None
        self._player_args = list()
        self._player_kwargs = dict()

    def set_asset(self, uri, duration):
        settings.load()

        if self._arch in ('armv6l', 'armv7l'):
            self._player_args = ['omxplayer', uri]
            self._player_kwargs = {'o': settings['audio_output'], 'layer': 1, '_bg': True, '_ok_code': [0, 124, 143]}
        else:
            self._player_args = ['mplayer', uri, '-nosound']
            self._player_kwargs = {'_bg': True, '_ok_code': [0, 124]}

        if duration and duration != 'N/A':
            self._player_args = ['timeout', VIDEO_TIMEOUT + int(duration.split('.')[0])] + self._player_args

    def play(self):
        self._run = sh.Command(self._player_args[0])(*self._player_args[1:], **self._player_kwargs)

    def stop(self):
        try:
            sh.killall('omxplayer.bin', _ok_code=[1])
        except OSError:
            pass

    def is_playing(self):
        return bool(self._run.process.alive)
