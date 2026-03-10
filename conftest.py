import os

import pytest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'anthias_django.settings')
os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')


@pytest.fixture(scope='session')
def browser_type_launch_args():
    return {
        'headless': True,
        'args': ['--no-sandbox', '--disable-dev-shm-usage'],
    }
