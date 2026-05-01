# coding=utf-8

import unittest
from datetime import datetime
from unittest.mock import patch

import sh
from django.test import TestCase

from lib.utils import handler, template_handle_unicode, url_fails

url_fail = 'http://doesnotwork.example.com'
url_redir = 'http://example.com'
uri_ = '/home/user/file'


class UtilsTest(unittest.TestCase):
    def test_unicode_correctness_in_bottle_templates(self) -> None:
        self.assertEqual(template_handle_unicode('hello'), 'hello')
        self.assertEqual(
            template_handle_unicode('Привет'),
            'Привет',
        )

    def test_json_tz(self) -> None:
        json_str = handler(datetime(2016, 7, 19, 12, 42))
        self.assertEqual(json_str, '2016-07-19T12:42:00+00:00')


class URLHelperTest(TestCase):
    def test_url_1(self) -> None:
        self.assertTrue(url_fails(url_fail))

    def test_url_2(self) -> None:
        self.assertFalse(url_fails(url_redir))

    def test_url_3(self) -> None:
        self.assertFalse(url_fails(uri_))


class StreamingURLProbeTest(TestCase):
    def test_rtsp_ffprobe_success_returns_false(self) -> None:
        with patch('lib.utils.sh.Command') as mock_command:
            mock_command.return_value.return_value = ''
            self.assertFalse(url_fails('rtsp://example.com/stream'))
            mock_command.assert_called_once_with('ffprobe')

    def test_rtmp_ffprobe_nonzero_exit_returns_true(self) -> None:
        err = sh.ErrorReturnCode_1('ffprobe', b'', b'cannot open stream')
        with patch('lib.utils.sh.Command') as mock_command:
            mock_command.return_value.side_effect = err
            self.assertTrue(url_fails('rtmp://example.com/live'))

    def test_rtsp_ffprobe_timeout_returns_true(self) -> None:
        with patch('lib.utils.sh.Command') as mock_command:
            mock_command.return_value.side_effect = sh.TimeoutException(
                124, 'ffprobe ...'
            )
            self.assertTrue(url_fails('rtsp://example.com/stream'))

    def test_rtsp_ffprobe_missing_returns_false(self) -> None:
        with patch('lib.utils.sh.Command') as mock_command:
            mock_command.side_effect = sh.CommandNotFound('ffprobe')
            self.assertFalse(url_fails('rtsp://example.com/stream'))
