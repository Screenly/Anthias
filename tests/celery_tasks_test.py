from os import getenv, path, remove, listdir
import yaml
import unittest

from nose.plugins.attrib import attr

from lib import db, queries

from lib.utils import generate_perfect_paper_password

from server import celery as celeryapp
from server import append_usb_assets, cleanup, cleanup_usb_assets, remove_usb_assets, upgrade_screenly

from settings import settings


class CeleryTasksTestCase(unittest.TestCase):
    def setUp(self):
        celeryapp.conf.update(CELERY_ALWAYS_EAGER=True, CELERY_RESULT_BACKEND='', CELERY_BROKER_URL='')


class TestUpgradeScreenly(CeleryTasksTestCase):
    def setUp(self):
        super(TestUpgradeScreenly, self).setUp()
        self.upgrade_screenly_task = upgrade_screenly.apply(args=['test', 'true', 'true'])
        self.upgrade_screenly_result = self.upgrade_screenly_task.get()

    def test_state(self):
        self.assertEqual(self.upgrade_screenly_task.state, 'SUCCESS')

    def test_result(self):
        self.assertEqual(self.upgrade_screenly_result, {'status': 'Invalid -b parameter.\n'})


class TestClenup(CeleryTasksTestCase):
    def setUp(self):
        super(TestClenup, self).setUp()

    def test_cleanup(self):
        cleanup.apply()
        tmp_files = filter(lambda x: x.endswith('.tmp'), listdir(path.join(getenv('HOME'), 'screenly_assets')))
        self.assertEqual(len(tmp_files), 0)


@attr('usb_assets')
class TestUsbAssets(CeleryTasksTestCase):
    def setUp(self):
        super(TestUsbAssets, self).setUp()

        self.mountpoint = '/tmp/USB'
        self.key_file = '%s/usb_assets_key.yaml' % self.mountpoint
        self.asset_file = '%s/image.png' % self.mountpoint
        self.cleanup_folder = '%s/cleanup_folder' % self.mountpoint
        self.cleanup_asset_file = '%s/image.png' % self.cleanup_folder

        settings['usb_assets_key'] = generate_perfect_paper_password(20, False)
        settings.save()

        key_data = {"screenly": {"key": settings['usb_assets_key']}}
        with open(self.key_file, 'w') as f:
            yaml.dump(key_data, f)

    def tearDown(self):
        remove(self.key_file)

    def test_append_usb_assets(self):
        append_usb_assets.apply(args=[self.mountpoint])
        self.assertTrue(self.asset_file in self.getLocationAssets())

    def test_remove_usb_assets(self):
        remove_usb_assets.apply(args=[self.mountpoint])
        self.assertTrue(self.asset_file not in self.getLocationAssets())

    def test_cleanup_usb_assets(self):
        append_usb_assets.apply(args=[self.cleanup_folder])
        remove(self.cleanup_asset_file)
        cleanup_usb_assets.apply(args=[self.mountpoint])
        self.assertTrue(self.asset_file not in self.getLocationAssets())

    @staticmethod
    def getLocationAssets():
        with db.conn(settings['database']) as conn:
            with db.cursor(conn) as c:
                c.execute(queries.read_all(['uri', ]))
                return [asset[0] for asset in c.fetchall()]
