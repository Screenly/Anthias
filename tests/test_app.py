import os
import shutil
import tempfile
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from anthias_app.models import Asset
from settings import settings

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


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestWeb:
    @pytest.fixture(autouse=True)
    def setup(self):
        Asset.objects.all().delete()

    def test_add_asset_url(self, page, live_server):
        page.goto(live_server.url)
        page.click('[data-bs-target="#addAssetModal"]')
        page.wait_for_selector('#addAssetModal.show')
        page.fill('#add-uri', 'https://example.com')
        page.check('#add-skip-check')
        page.click('#addAssetForm button[type="submit"]')
        page.wait_for_timeout(3000)

        assets = Asset.objects.all()
        assert len(assets) == 1
        asset = assets.first()
        assert asset.name == 'https://example.com'
        assert asset.uri == 'https://example.com'
        assert asset.mimetype == 'webpage'
        assert asset.duration == settings['default_duration']

    def test_edit_asset(self, page, live_server):
        Asset.objects.create(**asset_x)
        page.goto(live_server.url)
        page.wait_for_selector('[data-asset-id]')
        page.click(
            f'[data-asset-id="{asset_x["asset_id"]}"]'
            ' button[onclick*="openEditModal"]'
        )
        page.wait_for_selector('#editAssetModal.show')
        page.fill('#edit-duration', '333')
        page.click('#editAssetForm button[type="submit"]')
        page.wait_for_timeout(3000)

        assets = Asset.objects.all()
        assert len(assets) == 1
        assert assets.first().duration == 333

    def test_add_asset_image_upload(self, page, live_server):
        image_file = '/tmp/image.png'
        page.goto(live_server.url)
        page.click('[data-bs-target="#addAssetModal"]')
        page.wait_for_selector('#addAssetModal.show')
        page.click('[data-bs-target="#tab-upload"]')
        page.set_input_files('#add-file-upload', image_file)
        page.click('#addAssetForm button[type="submit"]')
        page.wait_for_timeout(3000)

        assets = Asset.objects.all()
        assert len(assets) == 1
        asset = assets.first()
        assert asset.name == 'image.png'
        assert asset.mimetype == 'image'
        assert asset.duration == settings['default_duration']

    def test_add_asset_video_upload(self, page, live_server):
        with TemporaryCopy(
            'tests/assets/asset.mov', 'video.mov'
        ) as video_file:
            page.goto(live_server.url)
            page.click('[data-bs-target="#addAssetModal"]')
            page.wait_for_selector('#addAssetModal.show')
            page.click('[data-bs-target="#tab-upload"]')
            page.set_input_files(
                '#add-file-upload', video_file
            )
            page.click(
                '#addAssetForm button[type="submit"]'
            )
            page.wait_for_timeout(3000)

            assets = Asset.objects.all()
            assert len(assets) == 1
            asset = assets.first()
            assert asset.name == 'video.mov'
            assert asset.mimetype == 'video'
            assert asset.duration == 5

    def test_add_two_assets_upload(self, page, live_server):
        with (
            TemporaryCopy(
                'tests/assets/asset.mov', 'video.mov'
            ) as video_file,
            TemporaryCopy(
                'static/img/standby.png', 'standby.png'
            ) as image_file,
        ):
            page.goto(live_server.url)
            page.click('[data-bs-target="#addAssetModal"]')
            page.wait_for_selector('#addAssetModal.show')
            page.click('[data-bs-target="#tab-upload"]')

            page.set_input_files(
                '#add-file-upload', image_file
            )
            page.click(
                '#addAssetForm button[type="submit"]'
            )
            page.wait_for_timeout(2000)

            page.click('[data-bs-target="#addAssetModal"]')
            page.wait_for_selector('#addAssetModal.show')
            page.click('[data-bs-target="#tab-upload"]')
            page.set_input_files(
                '#add-file-upload', video_file
            )
            page.click(
                '#addAssetForm button[type="submit"]'
            )
            page.wait_for_timeout(3000)

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

    def test_add_asset_streaming(self, page, live_server):
        page.goto(live_server.url)
        page.click('[data-bs-target="#addAssetModal"]')
        page.wait_for_selector('#addAssetModal.show')
        page.fill(
            '#add-uri', 'rtsp://localhost:8091/asset.mov'
        )
        page.check('#add-skip-check')
        page.click('#addAssetForm button[type="submit"]')
        page.wait_for_timeout(3000)

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

    def test_remove_asset(self, page, live_server):
        Asset.objects.create(**asset_x)
        page.goto(live_server.url)
        page.wait_for_selector('[data-asset-id]')

        page.on('dialog', lambda dialog: dialog.accept())
        page.click(
            f'[data-asset-id="{asset_x["asset_id"]}"]'
            ' button[onclick*="deleteAsset"]'
        )
        page.wait_for_timeout(3000)
        assert Asset.objects.count() == 0

    def test_enable_asset(self, page, live_server):
        Asset.objects.create(**asset_x)
        page.goto(live_server.url)
        page.wait_for_selector('[data-asset-id]')
        toggle = page.locator(
            '.form-check.form-switch input[type="checkbox"]'
        ).first
        toggle.click()
        page.wait_for_timeout(3000)

        assets = Asset.objects.all()
        assert len(assets) == 1
        assert assets.first().is_enabled is True

    def test_disable_asset(self, page, live_server):
        Asset.objects.create(
            **{**asset_x, 'is_enabled': 1}
        )
        page.goto(live_server.url)
        page.wait_for_selector('[data-asset-id]')
        toggle = page.locator(
            '.form-check.form-switch input[type="checkbox"]'
        ).first
        toggle.click()
        page.wait_for_timeout(3000)

        assets = Asset.objects.all()
        assert len(assets) == 1
        assert assets.first().is_enabled is False

    def test_reorder_asset(self, page, live_server):
        Asset.objects.create(
            **{**asset_x, 'is_enabled': 1}
        )
        Asset.objects.create(**asset_y)
        page.goto(live_server.url)
        page.wait_for_selector('[data-asset-id]')

        source = page.locator(
            f'[data-asset-id="{asset_x["asset_id"]}"]'
            ' .drag-handle'
        )
        target = page.locator(
            f'[data-asset-id="{asset_y["asset_id"]}"]'
            ' .drag-handle'
        )
        source.drag_to(target)
        page.wait_for_timeout(3000)

        x = Asset.objects.get(asset_id=asset_x['asset_id'])
        y = Asset.objects.get(asset_id=asset_y['asset_id'])
        assert x.play_order == 0
        assert y.play_order == 1

    def test_settings_page_should_work(
        self, page, live_server
    ):
        page.goto(f'{live_server.url}/settings')
        content = page.content()
        assert (
            'Error: 500 Internal Server Error' not in content
        )
        assert (
            'Error: 504 Gateway Time-out' not in content
        )
        assert (
            'Error: 504 Gateway Timeout' not in content
        )

    def test_system_info_page_should_work(
        self, page, live_server
    ):
        page.goto(f'{live_server.url}/system_info')
        expect(
            page.locator(
                'text=Error: 500 Internal Server Error'
            )
        ).not_to_be_visible()
