import unittest
from os import getenv, listdir, path, system

from celery_tasks import celery as celeryapp
from celery_tasks import cleanup


class CeleryTasksTestCase(unittest.TestCase):
    REPO_URL = 'https://github.com/Screenly/screenly-ose'

    def setUp(self) -> None:
        self.image_url = f'{self.REPO_URL}/raw/master/static/img/standby.png'
        celeryapp.conf.update(
            CELERY_ALWAYS_EAGER=True,
            CELERY_RESULT_BACKEND='',
            CELERY_BROKER_URL='',
        )

    def download_image(self, image_url: str, image_path: str) -> None:
        system('curl {} > {}'.format(image_url, image_path))


class TestCleanup(CeleryTasksTestCase):
    def setUp(self) -> None:
        super(TestCleanup, self).setUp()
        self.assets_path = path.join(getenv('HOME') or '', 'anthias_assets')
        self.image_path = path.join(self.assets_path, 'image.tmp')

    def test_cleanup(self) -> None:
        cleanup.apply()
        tmp_files = [
            x for x in listdir(self.assets_path) if x.endswith('.tmp')
        ]
        self.assertEqual(len(tmp_files), 0)

    def tearDown(self) -> None:
        self.download_image(self.image_url, self.image_path)
