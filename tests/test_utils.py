# coding=utf-8

from datetime import datetime
from unittest.mock import patch

import pytest
import sh

from lib.utils import handler, template_handle_unicode, url_fails

url_fail = 'http://doesnotwork.example.com'
url_redir = 'http://example.com'
uri_ = '/home/user/file'


def test_unicode_correctness_in_bottle_templates() -> None:
    assert template_handle_unicode('hello') == 'hello'
    assert template_handle_unicode('Привет') == 'Привет'


def test_json_tz() -> None:
    json_str = handler(datetime(2016, 7, 19, 12, 42))
    assert json_str == '2016-07-19T12:42:00+00:00'


@pytest.mark.django_db
def test_url_1() -> None:
    assert url_fails(url_fail)


@pytest.mark.django_db
def test_url_2() -> None:
    assert not url_fails(url_redir)


@pytest.mark.django_db
def test_url_3() -> None:
    assert not url_fails(uri_)


@pytest.mark.django_db
def test_rtsp_ffprobe_success_returns_false() -> None:
    with patch('lib.utils.sh.Command') as mock_command:
        mock_command.return_value.return_value = ''
        assert not url_fails('rtsp://example.com/stream')
        mock_command.assert_called_once_with('ffprobe')


@pytest.mark.django_db
def test_rtmp_ffprobe_nonzero_exit_returns_true() -> None:
    err = sh.ErrorReturnCode_1('ffprobe', b'', b'cannot open stream')
    with patch('lib.utils.sh.Command') as mock_command:
        mock_command.return_value.side_effect = err
        assert url_fails('rtmp://example.com/live')


@pytest.mark.django_db
def test_rtsp_ffprobe_timeout_returns_true() -> None:
    with patch('lib.utils.sh.Command') as mock_command:
        mock_command.return_value.side_effect = sh.TimeoutException(
            124, 'ffprobe ...'
        )
        assert url_fails('rtsp://example.com/stream')


@pytest.mark.django_db
def test_rtsp_ffprobe_missing_returns_false() -> None:
    with patch('lib.utils.sh.Command') as mock_command:
        mock_command.side_effect = sh.CommandNotFound('ffprobe')
        assert not url_fails('rtsp://example.com/stream')
