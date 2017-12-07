import unittest
import os
import sh
import shutil
import sys
from contextlib import contextmanager

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
assetdir = /home/pi/screenly_assets
database = /home/pi/.screenly/screenly.db
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

CONFIG_DIR = '/tmp/.screenly/'
CONFIG_FILE = CONFIG_DIR + 'screenly.conf'


@contextmanager
def fake_settings(raw):
    with open(CONFIG_FILE, mode='w+') as f:
        f.write(raw)

    try:
        import settings
        yield (settings, settings.settings)
        del sys.modules['settings']
    finally:
        os.remove(CONFIG_FILE)


def getenv(k, default=None):
    try:
        return '/tmp' if k == 'HOME' else os.environ[k]
    except KeyError:
        return default


class SettingsTest(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(CONFIG_DIR):
            os.mkdir(CONFIG_DIR)
        self.orig_getenv = os.getenv

        os.getenv = getenv

    def tearDown(self):
        shutil.rmtree(CONFIG_DIR)
        os.getenv = self.orig_getenv

    def test_screenly_should_exit_if_no_settings_file_found(self):
        new_env = os.environ.copy()
        new_env["HOME"] = "/tmp"
        project_dir = os.path.dirname(__file__)

        with self.assertRaises(sh.ErrorReturnCode_1):
            sh.python(project_dir + '/../viewer.py', _env=new_env)

        with self.assertRaises(sh.ErrorReturnCode_1):
            sh.python(project_dir + '/../server.py', _env=new_env)

    def test_parse_settings(self):
        with fake_settings(settings1) as (mod_settings, settings):
            self.assertEquals(settings['player_name'], 'new player')
            self.assertEquals(settings['show_splash'], False)
            self.assertEquals(settings['shuffle_playlist'], True)
            self.assertEquals(settings['debug_logging'], True)
            self.assertEquals(settings['default_duration'], '45')

    def test_default_settings(self):
        with fake_settings(empty_settings) as (mod_settings, settings):
            self.assertEquals(settings['player_name'], mod_settings.DEFAULTS['viewer']['player_name'])
            self.assertEquals(settings['show_splash'], mod_settings.DEFAULTS['viewer']['show_splash'])
            self.assertEquals(settings['shuffle_playlist'], mod_settings.DEFAULTS['viewer']['shuffle_playlist'])
            self.assertEquals(settings['debug_logging'], mod_settings.DEFAULTS['viewer']['debug_logging'])
            self.assertEquals(settings['default_duration'], mod_settings.DEFAULTS['viewer']['default_duration'])

    def broken_settings_should_raise_value_error(self):
        with self.assertRaises(ValueError):
            with fake_settings(broken_settings) as (mod_settings, settings):
                pass

    def test_save_settings(self):
        with fake_settings(settings1) as (mod_settings, settings):
            settings.conf_file = CONFIG_DIR + '/new.conf'
            settings['default_duration'] = 35
            settings['verify_ssl'] = True
            settings.save()

        with open(CONFIG_DIR + '/new.conf') as f:
            saved = f.read()
            with fake_settings(saved) as (mod_settings, settings):
                # changes saved?
                self.assertEqual(settings['default_duration'], '35')
                self.assertEqual(settings['verify_ssl'], True)
                # no out of thin air changes?
                self.assertEqual(settings['audio_output'], 'hdmi')
