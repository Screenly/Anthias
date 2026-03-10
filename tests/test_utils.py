# coding=utf-8

from datetime import datetime

import pytest

from lib.utils import handler, template_handle_unicode, url_fails


class TestUtils:
    def test_unicode_correctness_in_bottle_templates(self):
        assert template_handle_unicode('hello') == 'hello'
        assert (
            template_handle_unicode('Привет')
            == '\u041f\u0440\u0438\u0432\u0435\u0442'
        )

    def test_json_tz(self):
        json_str = handler(datetime(2016, 7, 19, 12, 42))
        assert json_str == '2016-07-19T12:42:00+00:00'


@pytest.mark.django_db
class TestURLHelper:
    def test_url_fails_for_bad_domain(self):
        assert url_fails('http://doesnotwork.example.com')

    def test_url_succeeds_for_redirect(self):
        assert not url_fails('http://example.com')

    def test_url_succeeds_for_local_path(self):
        assert not url_fails('/home/user/file')
