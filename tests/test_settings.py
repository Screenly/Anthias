import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import pytest

user_home_dir = os.getenv('HOME')

settings1 = """
[viewer]
player_name = new player
show_splash = off
audio_output = hdmi
shuffle_playlist = on
verify_ssl = off
debug_logging = on
resolution = 1920x1080
default_duration = 45

[main]
assetdir = "{}/anthias_assets".format(user_home_dir)
database = "{}/.anthias/anthias.db".format(user_home_dir)
use_ssl = False

"""

empty_settings = """
[viewer]

[main]

"""

broken_settings = """
[viewer]
show_splash = offf

[main]

"""

# Each xdist worker gets its own /tmp root so the four tests in this
# module — which all rewrite the same on-disk config file — don't race
# against one another. Without the worker suffix, worker A's file
# write/cleanup interleaves with worker B's import/remove and tests
# fail intermittently with FileNotFoundError.
_WORKER_ID = os.environ.get('PYTEST_XDIST_WORKER', 'main')
_TMP_HOME = f'/tmp/.anthias-test-{_WORKER_ID}'
CONFIG_DIR = f'{_TMP_HOME}/.anthias/'
CONFIG_FILE = CONFIG_DIR + 'anthias.conf'


@contextmanager
def fake_settings(raw: str) -> Iterator[tuple[Any, Any]]:
    with open(CONFIG_FILE, mode='w+') as f:
        f.write(raw)

    # Force a re-import so AnthiasSettings() is instantiated against the
    # CONFIG_FILE we just wrote. Without this, a prior test that imported
    # `settings` cleanly would leave the module cached, and `import
    # settings` here would skip __init__ entirely — silently accepting
    # any config (including the broken-by-design fixture).
    # Force a fresh import: pop the submodule from sys.modules AND
    # delete the cached attribute on the parent package, otherwise
    # `from anthias_server import settings` returns the stale module
    # object via the parent's namespace and __init__ never re-runs.
    sys.modules.pop('anthias_server.settings', None)
    import anthias_server as _anthias_server
    if hasattr(_anthias_server, 'settings'):
        del _anthias_server.settings
    try:
        from anthias_server import settings

        yield (settings, settings.settings)
    finally:
        sys.modules.pop('anthias_server.settings', None)
        if hasattr(_anthias_server, 'settings'):
            del _anthias_server.settings
        os.remove(CONFIG_FILE)


def getenv(k: str, default: Any = None) -> Any:
    try:
        return _TMP_HOME if k == 'HOME' else os.environ[k]
    except KeyError:
        return default


@pytest.fixture
def settings_env() -> Iterator[None]:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    getenv_patcher = mock.patch.object(os, 'getenv', side_effect=getenv)
    getenv_patcher.start()
    try:
        yield
    finally:
        shutil.rmtree(_TMP_HOME, ignore_errors=True)
        getenv_patcher.stop()


def test_parse_settings(settings_env: None) -> None:
    with fake_settings(settings1) as (mod_settings, settings):
        assert settings['player_name'] == 'new player'
        assert settings['show_splash'] is False
        assert settings['shuffle_playlist'] is True
        assert settings['debug_logging'] is True
        assert settings['default_duration'] == 45


def test_default_settings(settings_env: None) -> None:
    with fake_settings(empty_settings) as (mod_settings, settings):
        assert (
            settings['player_name']
            == mod_settings.DEFAULTS['viewer']['player_name']
        )
        assert (
            settings['show_splash']
            == mod_settings.DEFAULTS['viewer']['show_splash']
        )
        assert (
            settings['shuffle_playlist']
            == mod_settings.DEFAULTS['viewer']['shuffle_playlist']
        )
        assert (
            settings['debug_logging']
            == mod_settings.DEFAULTS['viewer']['debug_logging']
        )
        assert (
            settings['default_duration']
            == mod_settings.DEFAULTS['viewer']['default_duration']
        )


def test_broken_settings_should_raise_value_error(settings_env: None) -> None:
    with pytest.raises(ValueError):
        with fake_settings(broken_settings) as (mod_settings, settings):
            pass


def test_save_settings(settings_env: None) -> None:
    with fake_settings(settings1) as (mod_settings, settings):
        settings.conf_file = CONFIG_DIR + '/new.conf'
        settings['default_duration'] = 35
        settings['verify_ssl'] = True
        settings.save()

    with open(CONFIG_DIR + '/new.conf') as f:
        saved = f.read()
        with fake_settings(saved) as (mod_settings, settings):
            # changes saved?
            assert settings['default_duration'] == 35
            assert settings['verify_ssl'] is True
            # no out of thin air changes?
            assert settings['audio_output'] == 'hdmi'
