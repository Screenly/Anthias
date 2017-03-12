from splinter import Browser
from time import sleep
from selenium.common.exceptions import ElementNotVisibleException
from settings import settings
from lib import db
from lib import assets_helper
import unittest
from datetime import datetime, timedelta

asset_x = {
    'mimetype': u'web',
    'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d',
    'name': u'WireLoad',
    'uri': u'http://www.wireload.net',
    'start_date': datetime.now() - timedelta(days=1),
    'end_date': datetime.now() + timedelta(days=1),
    'duration': u'5',
    'is_enabled': 0,
    'nocache': 0,
    'play_order': 1,
}

asset_y = {
    'mimetype': u'image',
    'asset_id': u'7e978f8c1204a6f70770a1eb54a76e9b',
    'name': u'Google',
    'uri': u'https://www.google.com/images/srpr/logo3w.png',
    'start_date': datetime.now() - timedelta(days=1),
    'end_date': datetime.now() + timedelta(days=1),
    'duration': u'6',
    'is_enabled': 1,
    'nocache': 0,
    'play_order': 0,
}


def wait_for_and_do(browser, query, callback):
    not_filled = True
    n = 0

    while not_filled:
        try:
            callback(browser.find_by_css(query).first)
            not_filled = False
        except ElementNotVisibleException, e:
            if n > 20:
                raise e
            n += 1


class WebTest(unittest.TestCase):
    def setUp(self):
        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            for asset in assets:
                assets_helper.delete(conn, asset['asset_id'])

    def tearDown(self):
        pass

    def test_add_asset_url(self):
        with Browser() as browser:
            browser.visit('http://localhost:8080')

            wait_for_and_do(browser, '#add-asset-button', lambda btn: btn.click())
            sleep(1)
            wait_for_and_do(browser, 'input[name="uri"]', lambda field: field.fill('http://example.com'))
            wait_for_and_do(browser, 'input[name="duration"]', lambda field: field.fill('30'))
            sleep(1)
            wait_for_and_do(browser, '#add-form', lambda form: form.click())
            sleep(1)

            wait_for_and_do(browser, '#save-asset', lambda btn: btn.click())
            sleep(3)  # backend need time to process request

        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)

            self.assertEqual(len(assets), 1)
            asset = assets[0]

            self.assertEqual(asset['name'], u'http://example.com')
            self.assertEqual(asset['uri'], u'http://example.com')
            self.assertEqual(asset['mimetype'], u'webpage')
            self.assertEqual(asset['duration'], u'30')

    def test_edit_asset(self):
        with db.conn(settings['database']) as conn:
            assets_helper.create(conn, asset_x)

        with Browser() as browser:
            browser.visit('http://localhost:8080')
            wait_for_and_do(browser, '.edit-asset-button', lambda btn: btn.click())
            sleep(1)

            wait_for_and_do(browser, 'input[name="duration"]', lambda field: field.fill('333'))
            sleep(1)  # wait for new-asset panel animation

            wait_for_and_do(browser, '#add-form', lambda form: form.click())
            sleep(1)

            wait_for_and_do(browser, '#save-asset', lambda btn: btn.click())
            sleep(3)  # backend need time to process request

        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)

            self.assertEqual(len(assets), 1)
            asset = assets[0]

            self.assertEqual(asset['duration'], u'333')

    def test_add_asset_image_upload(self):
        image_file = '/tmp/image.png'

        with Browser() as browser:
            browser.visit('http://localhost:8080')

            browser.find_by_id('add-asset-button').click()
            sleep(1)

            wait_for_and_do(browser, 'a[href="#tab-file_upload"]', lambda tab: tab.click())
            wait_for_and_do(browser, 'input[name="file_upload"]', lambda input: input.fill(image_file))
            wait_for_and_do(browser, 'input[name="duration"]', lambda field: field.fill('30'))
            sleep(1)  # wait for new-asset panel animation

            wait_for_and_do(browser, '#add-form', lambda form: form.click())
            sleep(1)

            wait_for_and_do(browser, '#save-asset', lambda btn: btn.click())
            sleep(3)  # backend need time to process request

        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)

            self.assertEqual(len(assets), 1)
            asset = assets[0]

            self.assertEqual(asset['name'], u'image.png')
            self.assertEqual(asset['mimetype'], u'image')
            self.assertEqual(asset['duration'], u'30')

    def test_add_asset_video_upload(self):
        video_file = '/tmp/video.mov'

        with Browser() as browser:
            browser.visit('http://localhost:8080')

            browser.find_by_id('add-asset-button').click()
            sleep(1)

            wait_for_and_do(browser, 'a[href="#tab-file_upload"]', lambda tab: tab.click())
            wait_for_and_do(browser, 'input[name="file_upload"]', lambda input: input.fill(video_file))
            sleep(1)  # wait for new-asset panel animation

            wait_for_and_do(browser, '#add-form', lambda form: form.click())
            sleep(1)

            wait_for_and_do(browser, '#save-asset', lambda btn: btn.click())
            sleep(3)  # backend need time to process request

        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)

            self.assertEqual(len(assets), 1)
            asset = assets[0]

            self.assertEqual(asset['name'], u'video.mov')
            self.assertEqual(asset['mimetype'], u'video')
            self.assertEqual(asset['duration'], u'33')

    def test_rm_asset(self):
        with db.conn(settings['database']) as conn:
            assets_helper.create(conn, asset_x)

        with Browser() as browser:
            browser.visit('http://localhost:8080')

            wait_for_and_do(browser, '.delete-asset-button', lambda btn: btn.click())
            wait_for_and_do(browser, '.confirm-delete', lambda btn: btn.click())
            sleep(3)  # backend need time to process request

        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            self.assertEqual(len(assets), 0)

    def test_enable_asset(self):
        with db.conn(settings['database']) as conn:
            assets_helper.create(conn, asset_x)

        with Browser() as browser:
            browser.visit('http://localhost:8080')
            wait_for_and_do(browser, 'span[class="on"]', lambda btn: btn.click())
            sleep(3)  # backend need time to process request

        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            self.assertEqual(len(assets), 1)

            asset = assets[0]
            self.assertEqual(asset['is_enabled'], 1)

    def test_disable_asset(self):
        with db.conn(settings['database']) as conn:
            _asset_x = asset_x.copy()
            _asset_x['is_enabled'] = 1
            assets_helper.create(conn, _asset_x)

        with Browser() as browser:
            browser.visit('http://localhost:8080')

            wait_for_and_do(browser, 'span[class="off"]', lambda btn: btn.click())
            sleep(3)  # backend need time to process request

        with db.conn(settings['database']) as conn:
            assets = assets_helper.read(conn)
            self.assertEqual(len(assets), 1)

            asset = assets[0]
            self.assertEqual(asset['is_enabled'], 0)

    def test_reorder_asset(self):
        with db.conn(settings['database']) as conn:
            _asset_x = asset_x.copy()
            _asset_x['is_enabled'] = 1
            assets_helper.create(conn, _asset_x)
            assets_helper.create(conn, asset_y)

        with Browser() as browser:
            browser.visit('http://localhost:8080')

            asset_x_for_drag = browser.find_by_id(asset_x['asset_id'])
            sleep(1)

            asset_y_to_reorder = browser.find_by_id(asset_y['asset_id'])
            asset_x_for_drag.drag_and_drop(asset_y_to_reorder)
            sleep(3)  # backend need time to process request

        with db.conn(settings['database']) as conn:
            x = assets_helper.read(conn, asset_x['asset_id'])
            y = assets_helper.read(conn, asset_y['asset_id'])

            self.assertEqual(x['play_order'], 0)
            self.assertEqual(y['play_order'], 1)

    def test_settings_page_should_work(self):
        with Browser() as browser:
            browser.visit('http://localhost:8080/settings')
            self.assertEqual(browser.is_text_present('Error: 500 Internal Server Error'), False,
                             '500: internal server error not expected')

    def test_system_info_page_should_work(self):
        with Browser() as browser:
            browser.visit('http://localhost:8080/system_info')
            self.assertEqual(browser.is_text_present('Error: 500 Internal Server Error'), False,
                             '500: internal server error not expected')
