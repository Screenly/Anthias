from os import getenv, path, remove, listdir, system
import yaml
import unittest

from nose.plugins.attrib import attr

from lib import db, queries

from lib.utils import generate_perfect_paper_password

from server import celery as celeryapp
from server import append_usb_assets, cleanup, cleanup_usb_assets, remove_usb_assets

from settings import settings


class CeleryTasksTestCase(unittest.TestCase):
    def setUp(self):
        self.image_url = 'https://github.com/Screenly/screenly-ose/raw/master/static/img/ose-logo.png'
        celeryapp.conf.update(CELERY_ALWAYS_EAGER=True, CELERY_RESULT_BACKEND='', CELERY_BROKER_URL='')

    def download_image(self, image_url, image_path):
        system('curl {} > {}'.format(image_url, image_path))


class TestCleanup(CeleryTasksTestCase):
    def setUp(self):
        super(TestCleanup, self).setUp()
        self.assets_path = path.join(getenv('HOME'), 'screenly_assets')
        self.image_path = path.join(self.assets_path, 'image.tmp')

    def test_cleanup(self):
        cleanup.apply()
        tmp_files = filter(lambda x: x.endswith('.tmp'), listdir(self.assets_path))
        self.assertEqual(len(tmp_files), 0)

    def tearDown(self):
        self.download_image(self.image_url, self.image_path)


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

        self.download_image(self.image_url, self.cleanup_asset_file)

    @staticmethod
    def getLocationAssets():
        with db.conn(settings['database']) as conn:
            with db.cursor(conn) as c:
                c.execute(queries.read_all(['uri', ]))
                return [asset[0] for asset in c.fetchall()]
