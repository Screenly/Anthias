import unittest
from settings import ScreenlySettings
from settings import DEFAULTS
from settings import load as load_settings
from settings import save as save_settings
from settings import config_dir
from copy import deepcopy
from os import getenv


class SettingsTest(unittest.TestCase):

    def test_config_dir(self):
        home = getenv('HOME')
        self.assertEquals(config_dir(home='/tmp'), '/tmp/.screenly/')
        self.assertEquals(config_dir(), home + '/' + '.screenly/')

    def default_settings(self):
        return ScreenlySettings(deepcopy(DEFAULTS))

    def test_defaults(self):
        s = self.default_settings()
        self.assertEqual(s['listen'], '0.0.0.0:8080')
        self.assertEqual(s['use_24_hour_clock'], False)
        self.assertEqual(s['show_splash'], True)
        self.assertEqual(s['shuffle_playlist'], False)
        self.assertEqual(s['resolution'], '1920x1080')
        self.assertEqual(s['default_duration'], '10')
        self.assertEqual(s['debug_logging'], False)
        self.assertEqual(s['verify_ssl'], True)

        home = getenv('HOME')

        self.assertEquals(s.get_path('assetdir', absolute=False), 'screenly_assets')
        self.assertEquals(s.get_path('assetdir', home='/tmp'), '/tmp/screenly_assets')
        self.assertEquals(s.get_path('assetdir'), home + '/' + 'screenly_assets')

        self.assertEquals(s.get_path('database', absolute=False), '.screenly/screenly.db')
        self.assertEquals(s.get_path('database', home='/tmp'), '/tmp/.screenly/screenly.db')
        self.assertEquals(s.get_path('database'), home + '/' + '.screenly/screenly.db')

        self.assertEquals(s.get_listen_ip(), '0.0.0.0')
        self.assertEquals(s.get_listen_port(), '8080')

    def test_not_found(self):
        s = self.default_settings()
        self.assertRaises(AttributeError, lambda: s['missing'])

    def test_set(self):
        s = self.default_settings()
        s['listen'] = '127.0.0.1:8080'
        self.assertEqual(s['listen'], '127.0.0.1:8080')

    def test_load_non_exists(self):
        self.assertRaises(IOError, lambda: load_settings('/tmp/notfound'))

    def load_with_conf(self, conf_str):
        file = '/tmp/settings'
        with open(file, 'w') as f:
            f.write(conf_str)
        return load_settings(file)

    def conf_equal(self, file, lines):
        with open(file) as f:
            file_lines = f.readlines()
        self.assertEquals(lines, file_lines)

    def test_load(self):
        s = self.load_with_conf('[viewer]\nverify_ssl = False')
        self.assertEqual(s['verify_ssl'], False)

    def test_load_bad_section(self):
        """option passed in bad section must be ignored"""
        s = self.load_with_conf('[main]\nverify_ssl = False')
        self.assertEqual(s['verify_ssl'], True)

    def test_load_set_save(self):
        s = self.load_with_conf('[main]\nassetdir = dir')
        s['show_splash'] = False

        save_to = '/tmp/settings.new'
        save_settings(s, save_to)

        self.conf_equal(save_to, ['[main]\n',
                                  'assetdir = dir\n',
                                  'database = .screenly/screenly.db\n',
                                  'listen = 0.0.0.0:8080\n',
                                  'use_24_hour_clock = False\n',
                                  '\n',
                                  '[viewer]\n',
                                  'audio_output = hdmi\n',
                                  'debug_logging = False\n',
                                  'default_duration = 10\n',
                                  'resolution = 1920x1080\n',
                                  'show_splash = False\n',
                                  'shuffle_playlist = False\n',
                                  'verify_ssl = True\n',
                                  '\n'])
