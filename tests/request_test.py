# coding=utf-8
from __future__ import unicode_literals
from datetime import datetime
import os
import unittest
import mock

from django.http import HttpRequest
from rest_framework.request import Request

# This is needed to avoid exceptions when importing from api.helpers.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "anthias_django.settings")

from api.helpers import prepare_asset

request_ok_json = """{"name": "https://mail.ru", "mimetype": "webpage", "uri": "https://mail.ru", "is_active": false,
                "start_date": "2016-07-19T12:42:00.000Z", "end_date": "2016-07-26T12:42:00.000Z", "duration": "30",
                "is_enabled": 0, "nocache": 0, "play_order": 0, "skip_asset_check": 0}"""

request_json_no_name = """{"name": null, "mimetype": "webpage", "uri": "https://mail.ru", "is_active": false,
                "start_date": "2016-07-19T12:42:00.000Z", "end_date": "2016-07-26T12:42:00.000Z", "duration": "30",
                "is_enabled": 0, "nocache": 0, "play_order": 0, "skip_asset_check": 0}"""

request_json_no_mime = """{"name": "https://mail.ru", "mimetype": null, "uri": "https://mail.ru", "is_active": false,
                "start_date": "2016-07-19T12:42:00.000Z", "end_date": "2016-07-26T12:42:00.000Z", "duration": "30",
                "is_enabled": 0, "nocache": 0, "play_order": 0, "skip_asset_check": 0}"""

request_json2 = """{"name": null, "mimetype": null, "uri": null, "is_active": false,
                "start_date": "2016-07-19T12:42:00.000Z", "end_date": "2016-07-26T12:42:00.000Z", "duration": "30",
                "is_enabled": 0, "nocache": 0, "play_order": 0, "skip_asset_check": 0}"""


class RequestParseTest(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def create_request(self, model):
        request = Request(HttpRequest())
        request.data['model'] = model
        return request

    def test_asset_should_be_correct_V1_0(self):
        asset = prepare_asset(self.create_request(request_ok_json))
        self.assertEqual(asset['duration'], 30)
        self.assertEqual(asset['is_enabled'], 0)
        self.assertEqual(asset['mimetype'], u'webpage')
        self.assertEqual(asset['name'], u'https://mail.ru')
        self.assertEqual(asset['end_date'], datetime(2016, 7, 26, 12, 42))
        self.assertEqual(asset['start_date'], datetime(2016, 7, 19, 12, 42))

    def test_exception_should_rise_if_no_name_presented_V1_0(self):
        with self.assertRaises(Exception):
            prepare_asset(self.create_request(request_json_no_name))

    def test_exception_should_rise_if_no_mime_presented_V1_0(self):
        with self.assertRaises(Exception):
            prepare_asset(self.create_request(request_json_no_mime))

    def test_asset_should_be_correct_V1_1(self):
        asset = prepare_asset(self.create_request(request_ok_json))
        self.assertEqual(asset['duration'], 30)
        self.assertEqual(asset['is_enabled'], 0)
        self.assertEqual(asset['mimetype'], u'webpage')
        self.assertEqual(asset['name'], u'https://mail.ru')
        self.assertEqual(asset['end_date'], datetime(2016, 7, 26, 12, 42))
        self.assertEqual(asset['start_date'], datetime(2016, 7, 19, 12, 42))

    def test_exception_should_rise_if_no_name_presented_V1_1(self):
        with self.assertRaises(Exception):
            prepare_asset(self.create_request(request_json_no_name))

    def test_exception_should_rise_if_no_mime_presented_V1_1(self):
        with self.assertRaises(Exception):
            prepare_asset(self.create_request(request_json_no_mime))
