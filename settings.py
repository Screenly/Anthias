#!/usr/bin/env python
# -*- coding: utf-8 -*-

import configparser
import json
import logging
from collections import UserDict
from os import getenv, path
from time import sleep
from typing import Any, ClassVar

import zmq

from lib.auth import (
    Auth,
    BasicAuth,
    NoAuth,
    _is_legacy_sha256,
)
from lib.errors import ZmqCollectorTimeoutError

CONFIG_DIR = '.screenly/'
CONFIG_FILE = 'screenly.conf'
DEFAULTS = {
    'main': {
        'analytics_opt_out': False,
        'assetdir': 'screenly_assets',
        'database': CONFIG_DIR + 'screenly.db',
        'date_format': 'mm/dd/yyyy',
        'use_24_hour_clock': False,
        'use_ssl': False,
        'auth_backend': '',
        'websocket_port': '9999',
        'django_secret_key': '',
    },
    'viewer': {
        'audio_output': 'hdmi',
        'debug_logging': False,
        'default_duration': 10,
        'default_streaming_duration': '300',
        'player_name': '',
        'resolution': '1920x1080',
        'show_splash': True,
        'shuffle_playlist': False,
        'verify_ssl': True,
        'default_assets': False,
    },
}
CONFIGURABLE_SETTINGS = DEFAULTS['viewer'].copy()
CONFIGURABLE_SETTINGS['use_24_hour_clock'] = DEFAULTS['main'][
    'use_24_hour_clock'
]
CONFIGURABLE_SETTINGS['date_format'] = DEFAULTS['main']['date_format']

PORT = int(getenv('PORT', 8080))
LISTEN = getenv('LISTEN', '127.0.0.1')

# Initiate logging
logging.basicConfig(
    level=logging.INFO, format='%(message)s', datefmt='%a, %d %b %Y %H:%M:%S'
)

# Silence urllib info messages ('Starting new HTTP connection')
# that are triggered by the remote url availability check in view_web
requests_log = logging.getLogger('requests')
requests_log.setLevel(logging.WARNING)

logging.debug('Starting viewer')


class AnthiasSettings(UserDict[str, Any]):
    """Anthias' Settings."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        UserDict.__init__(self, *args, **kwargs)
        home = getenv('HOME')
        if not home:
            # Without HOME, all config/state paths (config file, DB,
            # asset dir) would silently resolve relative to the cwd.
            # Fail loudly instead of writing to unexpected locations.
            raise EnvironmentError(
                'HOME environment variable must be set for AnthiasSettings.'
            )
        self.home = home
        self.conf_file = self.get_configfile()
        self.auth_backends_list: list[Auth] = [NoAuth(), BasicAuth(self)]
        self.auth_backends: dict[str, Auth] = {}
        # Set by _get() when an insecure password was wiped during load();
        # __init__ persists the cleaned state to disk so the warning isn't
        # repeated on every subsequent load.
        self._needs_save_after_load = False
        for backend in self.auth_backends_list:
            DEFAULTS.update(backend.config)
            self.auth_backends[backend.name] = backend

        if not path.isfile(self.conf_file):
            logging.error(
                'Config-file %s missing. Using defaults.', self.conf_file
            )
            self.use_defaults()
            self.save()
        else:
            self.load()
            if self._needs_save_after_load:
                self._needs_save_after_load = False
                self.save()

    def _get(
        self,
        config: configparser.ConfigParser,
        section: str,
        field: str,
        default: Any,
    ) -> None:
        try:
            if isinstance(default, bool):
                self[field] = config.getboolean(section, field)
            elif isinstance(default, int):
                self[field] = config.getint(section, field)
            else:
                self[field] = config.get(section, field)
                if field == 'password' and self[field] != '':
                    # Both legacy SHA256 hashes and any non-Django-format
                    # value (incl. plaintext) are unsafe to keep — they
                    # cannot be verified by the new PBKDF2-based path. Clear
                    # the password and disable basic auth so the device
                    # stays reachable; the operator must re-set credentials
                    # via the UI.
                    #
                    # Note: we deliberately do NOT call hash_password() here.
                    # `settings.py` is imported (and AnthiasSettings()
                    # instantiated) before django.setup() runs in the viewer
                    # process, so calling Django's password hashers would
                    # raise ImproperlyConfigured at startup.
                    if (
                        _is_legacy_sha256(self[field])
                        or '$' not in self[field]
                    ):
                        reason = (
                            'legacy SHA256 hash'
                            if _is_legacy_sha256(self[field])
                            else 'unrecognized format (possibly plaintext)'
                        )
                        logging.error(
                            'Insecure password (%s) detected in %s; '
                            'clearing it and disabling basic auth. The '
                            'device will accept unauthenticated requests '
                            'until you re-set the password via the web UI.',
                            reason,
                            self.conf_file,
                        )
                        self[field] = ''
                        self['auth_backend'] = ''
                        self._needs_save_after_load = True
        except configparser.Error as e:
            logging.debug(
                "Could not parse setting '%s.%s': %s. "
                "Using default value: '%s'.",
                section,
                field,
                str(e),
                default,
            )
            self[field] = default
        if field in ['database', 'assetdir']:
            self[field] = str(path.join(self.home, self[field]))

    def _set(
        self,
        config: configparser.ConfigParser,
        section: str,
        field: str,
        default: Any,
    ) -> None:
        if isinstance(default, bool):
            config.set(
                section, field, self.get(field, default) and 'on' or 'off'
            )
        else:
            config.set(section, field, str(self.get(field, default)))

    def load(self) -> None:
        """Loads the latest settings from screenly.conf into memory."""
        logging.debug('Reading config-file...')
        config = configparser.ConfigParser()
        config.read(self.conf_file)

        for section, defaults in list(DEFAULTS.items()):
            for field, default in list(defaults.items()):
                self._get(config, section, field, default)

    def use_defaults(self) -> None:
        for defaults in list(DEFAULTS.items()):
            for field, default in list(defaults[1].items()):
                self[field] = default

    def save(self) -> None:
        # Write new settings to disk.
        config = configparser.ConfigParser()
        for section, defaults in list(DEFAULTS.items()):
            config.add_section(section)
            for field, default in list(defaults.items()):
                self._set(config, section, field, default)
        with open(self.conf_file, 'w') as f:
            config.write(f)
        self.load()

    def get_configdir(self) -> str:
        return path.join(self.home, CONFIG_DIR)

    def get_configfile(self) -> str:
        return path.join(self.home, CONFIG_DIR, CONFIG_FILE)

    @property
    def auth(self) -> Auth | None:
        backend_name = self['auth_backend']
        if backend_name in self.auth_backends:
            return self.auth_backends[self['auth_backend']]
        return None


settings = AnthiasSettings()


class ZmqPublisher:
    INSTANCE: ClassVar['ZmqPublisher | None'] = None

    def __init__(self) -> None:
        if self.INSTANCE is not None:
            raise ValueError('An instance already exists!')

        self.context = zmq.Context()

        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind('tcp://0.0.0.0:10001')
        sleep(1)

    @classmethod
    def get_instance(cls) -> 'ZmqPublisher':
        if cls.INSTANCE is None:
            cls.INSTANCE = ZmqPublisher()
        return cls.INSTANCE

    def send_to_ws_server(self, msg: str) -> None:
        self.socket.send('ws_server {}'.format(msg).encode('utf-8'))

    def send_to_viewer(self, msg: str) -> None:
        self.socket.send_string('viewer {}'.format(msg))


class ZmqConsumer:
    def __init__(self) -> None:
        self.context = zmq.Context()

        self.socket = self.context.socket(zmq.PUSH)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.connect('tcp://anthias-server:5558')

        sleep(1)

    def send(self, msg: Any) -> None:
        self.socket.send_json(msg, flags=zmq.NOBLOCK)


class ZmqCollector:
    INSTANCE: ClassVar['ZmqCollector | None'] = None

    def __init__(self) -> None:
        if self.INSTANCE is not None:
            raise ValueError('An instance already exists!')

        self.context = zmq.Context()

        self.socket = self.context.socket(zmq.PULL)
        self.socket.bind('tcp://0.0.0.0:5558')

        self.poller = zmq.Poller()
        self.poller.register(self.socket, zmq.POLLIN)

        sleep(1)

    @classmethod
    def get_instance(cls) -> 'ZmqCollector':
        if cls.INSTANCE is None:
            cls.INSTANCE = ZmqCollector()
        return cls.INSTANCE

    def recv_json(self, timeout: int) -> Any:
        if self.poller.poll(timeout):
            return json.loads(self.socket.recv(zmq.NOBLOCK))

        raise ZmqCollectorTimeoutError
