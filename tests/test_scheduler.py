import logging
import os
from datetime import timedelta

import pytest
import time_machine
from django.utils import timezone

from anthias_app.models import Asset
from settings import settings
from viewer.scheduling import Scheduler, generate_asset_list

logging.disable(logging.CRITICAL)

ASSET_X = {
    'mimetype': 'web',
    'asset_id': '4c8dbce552edb5812d3a866cfe5f159d',
    'name': 'WireLoad',
    'uri': 'http://www.wireload.net',
    'start_date': timezone.now() - timedelta(days=3),
    'end_date': timezone.now() + timedelta(days=3),
    'duration': 5,
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 1,
    'skip_asset_check': 0,
}

ASSET_Y = {
    'mimetype': 'image',
    'asset_id': '7e978f8c1204a6f70770a1eb54a76e9b',
    'name': 'Google',
    'uri': 'https://www.google.com/images/srpr/logo3w.png',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=2),
    'duration': 6,
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 0,
    'skip_asset_check': 0,
}

ASSET_Z = {
    'mimetype': 'image',
    'asset_id': '7e978f8c1204a6f70770a1eb54a76e9c',
    'name': 'Google',
    'uri': 'https://www.google.com/images/srpr/logo3w.png',
    'start_date': timezone.now() - timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 6,
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 2,
    'skip_asset_check': 0,
}

ASSET_TOMORROW = {
    'mimetype': 'image',
    'asset_id': '7e978f8c1204a6f70770a1eb54a76e9c',
    'name': 'Google',
    'uri': 'https://www.google.com/images/srpr/logo3w.png',
    'start_date': timezone.now() + timedelta(days=1),
    'end_date': timezone.now() + timedelta(days=1),
    'duration': 6,
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 2,
    'skip_asset_check': 0,
}

FAKE_DB_PATH = '/tmp/fakedb'


def create_assets(assets):
    for asset in assets:
        Asset.objects.create(**asset)


@pytest.mark.django_db
class TestScheduler:
    def teardown_method(self):
        settings['shuffle_playlist'] = False

    def test_asset_list_sorted_by_play_order(self):
        create_assets([ASSET_X, ASSET_Y])
        assets, _ = generate_asset_list()
        assert assets == [ASSET_Y, ASSET_X]

    def test_deadline_if_both_active(self):
        create_assets([ASSET_X, ASSET_Y])
        _, deadline = generate_asset_list()
        assert deadline == ASSET_Y['end_date']

    def test_deadline_if_asset_scheduled(self):
        create_assets([ASSET_X, ASSET_TOMORROW])
        _, deadline = generate_asset_list()
        assert deadline == ASSET_TOMORROW['start_date']

    def test_get_next_asset_order(self):
        create_assets([ASSET_X, ASSET_Y])
        scheduler = Scheduler()
        expected_y = scheduler.get_next_asset()
        expected_x = scheduler.get_next_asset()
        assert [expected_y, expected_x] == [
            ASSET_Y,
            ASSET_X,
        ]

    def test_keep_position_on_playlist_update(self):
        create_assets([ASSET_X, ASSET_Y])
        scheduler = Scheduler()
        scheduler.get_next_asset()
        create_assets([ASSET_Z])
        scheduler.update_playlist()
        assert scheduler.index == 1

    def test_counter_increments_after_full_loop(self):
        settings['shuffle_playlist'] = True
        create_assets([ASSET_X, ASSET_Y])
        scheduler = Scheduler()
        assert scheduler.counter == 0
        scheduler.get_next_asset()
        scheduler.get_next_asset()
        assert scheduler.counter == 1

    def test_get_db_mtime(self):
        settings['database'] = FAKE_DB_PATH
        with open(FAKE_DB_PATH, 'a'):
            os.utime(FAKE_DB_PATH, (0, 0))
        assert Scheduler().get_db_mtime() == 0

    def test_playlist_updated_after_deadline(self):
        create_assets([ASSET_X, ASSET_Y])
        _, deadline = generate_asset_list()
        traveller = time_machine.travel(deadline + timedelta(seconds=1))
        traveller.start()
        scheduler = Scheduler()
        scheduler.refresh_playlist()
        assert scheduler.assets == [ASSET_X]
        traveller.stop()
