# coding=utf-8
from datetime import datetime
import unittest
from lib import utils

settings = """
[viewer]
show_splash = off
audio_output = hdmi
shuffle_playlist = on
verify_ssl = off
debug_logging = on
resolution = 1920x1080
default_duration = 45

[main]
listen = 192.168.0.10:3333
assetdir = /home/pi/screenly_assets
database = /tmp/screenly.db
"""

CONFIG_DIR = '/tmp/.screenly/'
CONFIG_FILE = CONFIG_DIR + 'screenly.conf'


class UtilsTest(unittest.TestCase):
    def test_unicode_correctness_in_bottle_templates(self):
        self.assertEqual(utils.template_handle_unicode('hello'), u'hello')
        self.assertEqual(utils.template_handle_unicode('Привет'), u'\u041f\u0440\u0438\u0432\u0435\u0442')

    def test_json_tz(self):
        json_str = utils.handler(datetime(2016, 7, 19, 12, 42))
        self.assertEqual(json_str, '2016-07-19T12:42:00+00:00')
