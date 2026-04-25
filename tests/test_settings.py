import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import TestCase, mock

user_home_dir = os.getenv('HOME')

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

CONFIG_DIR = '/tmp/.screenly/'
CONFIG_FILE = CONFIG_DIR + 'screenly.conf'


@contextmanager
def fake_settings(raw: str) -> Iterator[tuple[Any, Any]]:
    with open(CONFIG_FILE, mode='w+') as f:
        f.write(raw)

    try:
        import settings

        yield (settings, settings.settings)
        del sys.modules['settings']
    finally:
        os.remove(CONFIG_FILE)


def getenv(k: str, default: Any = None) -> Any:
    try:
        return '/tmp' if k == 'HOME' else os.environ[k]
    except KeyError:
        return default


class SettingsTest(TestCase):
    def setUp(self) -> None:
        if not os.path.exists(CONFIG_DIR):
            os.mkdir(CONFIG_DIR)
        self._getenv_patcher = mock.patch.object(
            os, 'getenv', side_effect=getenv
        )
        self._getenv_patcher.start()

    def tearDown(self) -> None:
        shutil.rmtree(CONFIG_DIR)
        self._getenv_patcher.stop()

    def test_parse_settings(self) -> None:
        with fake_settings(settings1) as (mod_settings, settings):
            self.assertEqual(settings['player_name'], 'new player')
            self.assertEqual(settings['show_splash'], False)
            self.assertEqual(settings['shuffle_playlist'], True)
            self.assertEqual(settings['debug_logging'], True)
            self.assertEqual(settings['default_duration'], 45)

    def test_default_settings(self) -> None:
        with fake_settings(empty_settings) as (mod_settings, settings):
            self.assertEqual(
                settings['player_name'],
                mod_settings.DEFAULTS['viewer']['player_name'],
            )
            self.assertEqual(
                settings['show_splash'],
                mod_settings.DEFAULTS['viewer']['show_splash'],
            )
            self.assertEqual(
                settings['shuffle_playlist'],
                mod_settings.DEFAULTS['viewer']['shuffle_playlist'],
            )
            self.assertEqual(
                settings['debug_logging'],
                mod_settings.DEFAULTS['viewer']['debug_logging'],
            )
            self.assertEqual(
                settings['default_duration'],
                mod_settings.DEFAULTS['viewer']['default_duration'],
            )

    def broken_settings_should_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            with fake_settings(broken_settings) as (mod_settings, settings):
                pass

    def test_save_settings(self) -> None:
        with fake_settings(settings1) as (mod_settings, settings):
            settings.conf_file = CONFIG_DIR + '/new.conf'
            settings['default_duration'] = 35
            settings['verify_ssl'] = True
            settings.save()

        with open(CONFIG_DIR + '/new.conf') as f:
            saved = f.read()
            with fake_settings(saved) as (mod_settings, settings):
                # changes saved?
                self.assertEqual(settings['default_duration'], 35)
                self.assertEqual(settings['verify_ssl'], True)
                # no out of thin air changes?
                self.assertEqual(settings['audio_output'], 'hdmi')
