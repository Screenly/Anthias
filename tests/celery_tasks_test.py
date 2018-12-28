from os import getenv, path, listdir
import unittest

from server import celery as celeryapp
from server import cleanup, upgrade_screenly


class CeleryTasksTestCase(unittest.TestCase):
    def setUp(self):
        celeryapp.conf.update(CELERY_ALWAYS_EAGER=True, CELERY_RESULT_BACKEND='', CELERY_BROKER_URL='')
        self.upgrade_screenly_task = upgrade_screenly.apply(args=['test', 'true', 'true'])
        self.upgrade_screenly_result = self.upgrade_screenly_task.get()


class TestUpgradeScreenly(CeleryTasksTestCase):
    def test_state(self):
        self.assertEqual(self.upgrade_screenly_task.state, 'SUCCESS')

    def test_result(self):
        self.assertEqual(self.upgrade_screenly_result, {'status': 'Invalid -b parameter.\n'})

    def test_cleanup(self):
        cleanup.apply()
        tmp_files = filter(lambda x: x.endswith('.tmp'), listdir(path.join(getenv('HOME'), 'screenly_assets')))
        self.assertEqual(len(tmp_files), 0)
