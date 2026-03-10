import os
import shutil
import tempfile
from time import sleep

import pytest
from datetime import timedelta
from django.utils import timezone
from selenium import webdriver
from selenium.common.exceptions import ElementNotVisibleException
from splinter import Browser

from anthias_app.models import Asset
from settings import settings

main_page_url = 'http://localhost:8080'
settings_url = 'http://foo:bar@localhost:8080/settings'
system_info_url = 'http://foo:bar@localhost:8080/system_info'

asset_x = {
    'mimetype': 'web',
    'asset_id': '4c8dbce552edb5812d3a866cfe5f159d',
    'name': 'WireLoad',
    'uri': 'http://www.wireload.net',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 5,
    'is_enabled': 0,
    'nocache': 0,
    'play_order': 1,
    'skip_asset_check': 0,
}

asset_y = {
    'mimetype': 'image',
    'asset_id': '7e978f8c1204a6f70770a1eb54a76e9b',
    'name': 'Google',
    'uri': 'https://www.google.com/images/srpr/logo3w.png',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 6,
    'is_enabled': 1,
    'nocache': 0,
    'play_order': 0,
    'skip_asset_check': 0,
}


class TemporaryCopy:
    def __init__(self, original_path, base_path):
        self.original_path = original_path
        self.base_path = base_path

    def __enter__(self):
        temp_dir = tempfile.gettempdir()
        self.path = os.path.join(temp_dir, self.base_path)
        shutil.copy2(self.original_path, self.path)
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.remove(self.path)


def get_browser():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-dev-shm-usage')
    return Browser('chrome', headless=True, options=chrome_options)


def wait_for_and_do(browser, query, callback):
    not_filled = True
    n = 0
    while not_filled:
        try:
            callback(browser.find_by_css(query).first)
            not_filled = False
        except ElementNotVisibleException as e:
            if n > 20:
                raise e
            n += 1


@pytest.mark.integration
@pytest.mark.django_db
class TestWeb:
    @pytest.fixture(autouse=True)
    def setup(self):
        Asset.objects.all().delete()

    @pytest.mark.skip(reason='fixme')
    def test_add_asset_url(self):
        with get_browser() as browser:
            browser.visit(main_page_url)
            wait_for_and_do(
                browser,
                '#add-asset-button',
                lambda btn: btn.click(),
            )
            sleep(1)
            wait_for_and_do(
                browser,
                'input[name="uri"]',
                lambda field: field.fill(
                    'https://example.com'
                ),
            )
            sleep(1)
            wait_for_and_do(
                browser,
                '#tab-uri',
                lambda form: form.click(),
            )
            sleep(1)
            wait_for_and_do(
                browser,
                '#save-asset',
                lambda btn: btn.click(),
            )
            sleep(3)

        assets = Asset.objects.all()
        assert len(assets) == 1
        asset = assets.first()
        assert asset.name == 'https://example.com'
        assert asset.uri == 'https://example.com'
        assert asset.mimetype == 'webpage'
        assert asset.duration == settings['default_duration']

    @pytest.mark.skip(reason='migrate to HTMX-based tests')
    def test_edit_asset(self):
        Asset.objects.create(**asset_x)
        with get_browser() as browser:
            browser.visit(main_page_url)
            wait_for_and_do(
                browser,
                '.edit-asset-button',
                lambda btn: btn.click(),
            )
            sleep(1)
            wait_for_and_do(
                browser,
                'input[name="duration"]',
                lambda field: field.fill('333'),
            )
            sleep(1)
            wait_for_and_do(
                browser,
                '#edit-form',
                lambda form: form.click(),
            )
            sleep(3)
            wait_for_and_do(
                browser,
                '.edit-asset-modal #save-asset',
                lambda btn: btn.click(),
            )
            sleep(3)

        assets = Asset.objects.all()
        assert len(assets) == 1
        assert assets.first().duration == 333

    def test_add_asset_image_upload(self):
        image_file = '/tmp/image.png'
        with get_browser() as browser:
            browser.visit(main_page_url)
            browser.find_by_id('add-asset-button').click()
            sleep(1)
            wait_for_and_do(
                browser,
                '.nav-link.upload-asset-tab',
                lambda tab: tab.click(),
            )
            wait_for_and_do(
                browser,
                'input[name="file_upload"]',
                lambda file_input: file_input.fill(image_file),
            )
            sleep(1)
            sleep(3)

        assets = Asset.objects.all()
        assert len(assets) == 1
        asset = assets.first()
        assert asset.name == 'image.png'
        assert asset.mimetype == 'image'
        assert asset.duration == settings['default_duration']

    def test_add_asset_video_upload(self):
        with TemporaryCopy(
            'tests/assets/asset.mov', 'video.mov'
        ) as video_file:
            with get_browser() as browser:
                browser.visit(main_page_url)
                browser.find_by_id('add-asset-button').click()
                sleep(1)
                wait_for_and_do(
                    browser,
                    '.nav-link.upload-asset-tab',
                    lambda tab: tab.click(),
                )
                wait_for_and_do(
                    browser,
                    'input[name="file_upload"]',
                    lambda file_input: file_input.fill(
                        video_file
                    ),
                )
                sleep(1)
                sleep(3)

            assets = Asset.objects.all()
            assert len(assets) == 1
            asset = assets.first()
            assert asset.name == 'video.mov'
            assert asset.mimetype == 'video'
            assert asset.duration == 5

    def test_add_two_assets_upload(self):
        with (
            TemporaryCopy(
                'tests/assets/asset.mov', 'video.mov'
            ) as video_file,
            TemporaryCopy(
                'static/img/standby.png', 'standby.png'
            ) as image_file,
        ):
            with get_browser() as browser:
                browser.visit(main_page_url)
                browser.find_by_id(
                    'add-asset-button'
                ).click()
                sleep(1)
                wait_for_and_do(
                    browser,
                    '.nav-link.upload-asset-tab',
                    lambda tab: tab.click(),
                )
                wait_for_and_do(
                    browser,
                    'input[name="file_upload"]',
                    lambda file_input: file_input.fill(
                        image_file
                    ),
                )
                wait_for_and_do(
                    browser,
                    'input[name="file_upload"]',
                    lambda file_input: file_input.fill(
                        video_file
                    ),
                )
                sleep(3)

            assets = Asset.objects.all()
            assert len(assets) == 2
            assert assets[0].name == 'standby.png'
            assert assets[0].mimetype == 'image'
            assert (
                assets[0].duration
                == settings['default_duration']
            )
            assert assets[1].name == 'video.mov'
            assert assets[1].mimetype == 'video'
            assert assets[1].duration == 5

    @pytest.mark.skip(reason='fixme')
    def test_add_asset_streaming(self):
        with get_browser() as browser:
            browser.visit(main_page_url)
            wait_for_and_do(
                browser,
                '#add-asset-button',
                lambda btn: btn.click(),
            )
            sleep(1)
            wait_for_and_do(
                browser,
                'input[name="uri"]',
                lambda field: field.fill(
                    'rtsp://localhost:8091/asset.mov'
                ),
            )
            sleep(1)
            wait_for_and_do(
                browser,
                '#add-form',
                lambda form: form.click(),
            )
            sleep(1)
            wait_for_and_do(
                browser,
                '#save-asset',
                lambda btn: btn.click(),
            )
            sleep(10)

        assets = Asset.objects.all()
        assert len(assets) == 1
        asset = assets.first()
        assert (
            asset.name == 'rtsp://localhost:8091/asset.mov'
        )
        assert (
            asset.uri == 'rtsp://localhost:8091/asset.mov'
        )
        assert asset.mimetype == 'streaming'
        assert (
            asset.duration
            == settings['default_streaming_duration']
        )

    @pytest.mark.skip(reason='migrate to HTMX-based tests')
    def test_remove_asset(self):
        Asset.objects.create(**asset_x)
        with get_browser() as browser:
            browser.visit(main_page_url)
            wait_for_and_do(
                browser,
                '.delete-asset-button',
                lambda btn: btn.click(),
            )
            wait_for_and_do(
                browser,
                '.confirm-delete',
                lambda btn: btn.click(),
            )
            sleep(3)
        assert Asset.objects.count() == 0

    def test_enable_asset(self):
        Asset.objects.create(**asset_x)
        with get_browser() as browser:
            browser.visit(main_page_url)
            sleep(2)
            toggle = browser.find_by_css(
                '.form-switch input[type="checkbox"]'
            ).first
            browser.execute_script(
                'arguments[0].scrollIntoView(true);',
                toggle._element,
            )
            sleep(1)
            browser.execute_script(
                'arguments[0].click();', toggle._element
            )
            sleep(2)
            browser.find_by_css(
                '.form-switch input[type="checkbox"]'
            ).first
            sleep(5)

        assets = Asset.objects.all()
        assert len(assets) == 1
        assert assets.first().is_enabled is True

    def test_disable_asset(self):
        Asset.objects.all().delete()
        Asset.objects.create(**{**asset_x, 'is_enabled': 1})
        with get_browser() as browser:
            browser.visit(main_page_url)
            sleep(2)
            toggle = browser.find_by_css(
                '.form-switch input[type="checkbox"]'
            ).first
            browser.execute_script(
                'arguments[0].scrollIntoView(true);',
                toggle._element,
            )
            sleep(1)
            browser.execute_script(
                'arguments[0].click();', toggle._element
            )
            sleep(2)
            browser.find_by_css(
                '.form-switch input[type="checkbox"]'
            ).first
            sleep(5)

        assets = Asset.objects.all()
        assert len(assets) == 1
        assert assets.first().is_enabled is False

    @pytest.mark.skip(reason='migrate to HTMX-based tests')
    def test_reorder_asset(self):
        Asset.objects.create(
            **{**asset_x, 'is_enabled': 1}
        )
        Asset.objects.create(**asset_y)
        with get_browser() as browser:
            browser.visit(main_page_url)
            asset_x_for_drag = browser.find_by_id(
                asset_x['asset_id']
            )
            sleep(1)
            asset_y_to_reorder = browser.find_by_id(
                asset_y['asset_id']
            )
            asset_x_for_drag.drag_and_drop(
                asset_y_to_reorder
            )
            sleep(3)

        x = Asset.objects.get(asset_id=asset_x['asset_id'])
        y = Asset.objects.get(asset_id=asset_y['asset_id'])
        assert x.play_order == 0
        assert y.play_order == 1

    def test_settings_page_should_work(self):
        with get_browser() as browser:
            browser.visit(settings_url)
            assert not (
                'Error: 500 Internal Server Error'
                in browser.html
                or 'Error: 504 Gateway Time-out'
                in browser.html
                or 'Error: 504 Gateway Timeout'
                in browser.html
            )

    def test_system_info_page_should_work(self):
        with get_browser() as browser:
            browser.visit(system_info_url)
            assert not browser.is_text_present(
                'Error: 500 Internal Server Error'
            )
