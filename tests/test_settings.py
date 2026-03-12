import os
import shutil
from contextlib import contextmanager

CONFIG_DIR = '/tmp/.screenly/'
CONFIG_FILE = CONFIG_DIR + 'screenly.conf'

settings1 = """
[viewer]
player_name = new player
show_splash = off
audio_output = hdmi
shuffle_playlist = on
verify_ssl = off
debug_logging = on
resolution = 1920x1080
default_duration = 45

[main]
assetdir = "{}/screenly_assets".format(user_home_dir)
database = "{}/.screenly/screenly.db".format(user_home_dir)
use_ssl = False

"""

empty_settings = """
[viewer]

[main]

"""

broken_settings = """
[viewer]
show_splash = offf

[main]

"""


@contextmanager
def fake_settings(raw):
    with open(CONFIG_FILE, mode='w+') as f:
        f.write(raw)

    try:
        import settings

        settings.settings.conf_file = CONFIG_FILE
        settings.settings.load()
        yield (settings, settings.settings)
    finally:
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)


def getenv(k, default=None):
    try:
        return '/tmp' if k == 'HOME' else os.environ[k]
    except KeyError:
        return default


class TestSettings:
    def setup_method(self):
        if not os.path.exists(CONFIG_DIR):
            os.mkdir(CONFIG_DIR)
        self.orig_getenv = os.getenv
        os.getenv = getenv

    def teardown_method(self):
        shutil.rmtree(CONFIG_DIR)
        os.getenv = self.orig_getenv

    def test_parse_settings(self):
        with fake_settings(settings1) as (mod_settings, settings):
            assert settings['player_name'] == 'new player'
            assert settings['show_splash'] is False
            assert settings['shuffle_playlist'] is True
            assert settings['debug_logging'] is True
            assert settings['default_duration'] == 45

    def test_default_settings(self):
        with fake_settings(empty_settings) as (
            mod_settings,
            settings,
        ):
            assert (
                settings['player_name']
                == mod_settings.DEFAULTS['viewer']['player_name']
            )
            assert (
                settings['show_splash']
                == mod_settings.DEFAULTS['viewer']['show_splash']
            )
            assert (
                settings['shuffle_playlist']
                == mod_settings.DEFAULTS['viewer']['shuffle_playlist']
            )
            assert (
                settings['debug_logging']
                == mod_settings.DEFAULTS['viewer']['debug_logging']
            )
            assert (
                settings['default_duration']
                == mod_settings.DEFAULTS['viewer']['default_duration']
            )

    def test_save_settings(self):
        with fake_settings(settings1) as (mod_settings, settings):
            settings.conf_file = CONFIG_DIR + '/new.conf'
            settings['default_duration'] = 35
            settings['verify_ssl'] = True
            settings.save()

        with open(CONFIG_DIR + '/new.conf') as f:
            saved = f.read()
            with fake_settings(saved) as (
                mod_settings,
                settings,
            ):
                assert settings['default_duration'] == 35
                assert settings['verify_ssl'] is True
                assert settings['audio_output'] == 'hdmi'
