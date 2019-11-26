# coding=utf-8
from datetime import datetime
import unittest
from lib import utils


class UtilsTest(unittest.TestCase):
    def test_unicode_correctness_in_bottle_templates(self):
        self.assertEqual(utils.template_handle_unicode('hello'), 'hello')
        self.assertEqual(utils.template_handle_unicode('Привет'), '\u041f\u0440\u0438\u0432\u0435\u0442')

    def test_json_tz(self):
        json_str = utils.handler(datetime(2016, 7, 19, 12, 42))
        self.assertEqual(json_str, '2016-07-19T12:42:00+00:00')
