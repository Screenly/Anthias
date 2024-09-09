import os
import unittest
import time_machine

from datetime import datetime, timedelta
from lib import db, assets_helper

import settings
import viewer  # noqa: E402

asset_x = {
    'mimetype': u'web',
    'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d',
    'name': u'WireLoad',
    'uri': u'http://www.wireload.net',
    'start_date': datetime.now() - timedelta(days=3),
    'end_date': datetime.now() + timedelta(days=3),
    'duration': u'5',
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 1,
    'skip_asset_check': 0
}

asset_y = {
    'mimetype': u'image',
    'asset_id': u'7e978f8c1204a6f70770a1eb54a76e9b',
    'name': u'Google',
    'uri': u'https://www.google.com/images/srpr/logo3w.png',
    'start_date': datetime.now() - timedelta(days=1),
    'end_date': datetime.now() + timedelta(days=2),
    'duration': u'6',
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 0,
    'skip_asset_check': 0
}

asset_z = {
    'mimetype': u'image',
    'asset_id': u'7e978f8c1204a6f70770a1eb54a76e9c',
    'name': u'Google',
    'uri': u'https://www.google.com/images/srpr/logo3w.png',
    'start_date': datetime.now() - timedelta(days=1),
    'end_date': datetime.now() + timedelta(days=1),
    'duration': u'6',
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 2,
    'skip_asset_check': 0
}

asset_tomorrow = {
    'mimetype': u'image',
    'asset_id': u'7e978f8c1204a6f70770a1eb54a76e9c',
    'name': u'Google',
    'uri': u'https://www.google.com/images/srpr/logo3w.png',
    'start_date': datetime.now() + timedelta(days=1),
    'end_date': datetime.now() + timedelta(days=1),
    'duration': u'6',
    'is_enabled': 1,
    'nocache': 0,
    'is_processing': 0,
    'play_order': 2,
    'skip_asset_check': 0
}

FAKE_DB_PATH = '/tmp/fakedb'


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.old_db_path = settings.settings['database']
        viewer.db_conn = db.conn(':memory:')
        with db.commit(viewer.db_conn) as cursor:
            cursor.execute(assets_helper.create_assets_table)

    def tearDown(self):
        settings.settings['database'] = self.old_db_path
        settings.settings['shuffle_playlist'] = False
        viewer.datetime, assets_helper.get_time = (
            datetime, lambda: datetime.utcnow())
        viewer.db_conn.close()
        try:
            os.remove(FAKE_DB_PATH)
        except FileNotFoundError:
            pass

    def test_generate_asset_list_assets_should_be_y_and_x(self):
        assets_helper.create_multiple(viewer.db_conn, [asset_x, asset_y])
        assets, _ = viewer.generate_asset_list()
        self.assertEqual(assets, [asset_y, asset_x])

    def test_generate_asset_list_check_deadline_if_both_active(self):
        # if x and y currently active
        assets_helper.create_multiple(viewer.db_conn, [asset_x, asset_y])
        _, deadline = viewer.generate_asset_list()
        self.assertEqual(deadline, asset_y['end_date'])

    def test_generate_asset_list_check_deadline_if_asset_scheduled(self):
        """If asset_x is active and asset_x[end_date] == (now + 3) and
        asset_tomorrow will be active tomorrow then deadline should be
        asset_tomorrow[start_date]
        """
        assets_helper.create_multiple(
            viewer.db_conn, [asset_x, asset_tomorrow])
        _, deadline = viewer.generate_asset_list()
        self.assertEqual(deadline, asset_tomorrow['start_date'])

    def test_get_next_asset_should_be_y_and_x(self):
        assets_helper.create_multiple(viewer.db_conn, [asset_x, asset_y])
        scheduler = viewer.Scheduler()

        expect_y = scheduler.get_next_asset()
        expect_x = scheduler.get_next_asset()

        self.assertEqual([expect_y, expect_x], [asset_y, asset_x])

    def test_keep_same_position_on_playlist_update(self):
        assets_helper.create_multiple(viewer.db_conn, [asset_x, asset_y])
        scheduler = viewer.Scheduler()

        scheduler.get_next_asset()

        assets_helper.create(viewer.db_conn, asset_z)
        scheduler.update_playlist()
        self.assertEqual(scheduler.index, 1)

    def test_counter_should_increment_after_full_asset_loop(self):
        settings.settings['shuffle_playlist'] = True
        assets_helper.create_multiple(viewer.db_conn, [asset_x, asset_y])
        scheduler = viewer.Scheduler()

        self.assertEqual(scheduler.counter, 0)

        scheduler.get_next_asset()
        scheduler.get_next_asset()

        self.assertEqual(scheduler.counter, 1)

    def test_check_get_db_mtime(self):
        settings.settings['database'] = FAKE_DB_PATH
        with open(FAKE_DB_PATH, 'a'):
            os.utime(FAKE_DB_PATH, (0, 0))

        self.assertEqual(0, viewer.Scheduler().get_db_mtime())

    def test_playlist_should_be_updated_after_deadline_reached(self):
        assets_helper.create_multiple(viewer.db_conn, [asset_x, asset_y])
        _, deadline = viewer.generate_asset_list()

        traveller = time_machine.travel(deadline + timedelta(seconds=1))
        traveller.start()

        scheduler = viewer.Scheduler()
        scheduler.refresh_playlist()

        self.assertEqual([asset_x], scheduler.assets)
        traveller.stop()
