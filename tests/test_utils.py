# coding=utf-8

import unittest
from datetime import datetime

from django.test import TestCase

from lib.utils import handler, template_handle_unicode, url_fails

url_fail = 'http://doesnotwork.example.com'
url_redir = 'http://example.com'
uri_ = '/home/user/file'


class UtilsTest(unittest.TestCase):
    def test_unicode_correctness_in_bottle_templates(self):
        self.assertEqual(template_handle_unicode('hello'), 'hello')
        self.assertEqual(
            template_handle_unicode('Привет'),
            '\u041f\u0440\u0438\u0432\u0435\u0442',
        )

    def test_json_tz(self):
        json_str = handler(datetime(2016, 7, 19, 12, 42))
        self.assertEqual(json_str, '2016-07-19T12:42:00+00:00')


class URLHelperTest(TestCase):
    def test_url_1(self):
        self.assertTrue(url_fails(url_fail))

    def test_url_2(self):
        self.assertFalse(url_fails(url_redir))

    def test_url_3(self):
        self.assertFalse(url_fails(uri_))
