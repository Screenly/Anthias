#!/usr/bin/env python
# -*- coding: utf-8 -*-
import hashlib
import json
import logging
import os
import zmq
import ConfigParser
from os import path, getenv
from time import sleep
from UserDict import IterableUserDict

from lib.auth import WoTTAuth, BasicAuth, NoAuth
from lib.errors import ZmqCollectorTimeout

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
        'websocket_port': '9999'
    },
    'viewer': {
        'audio_output': 'hdmi',
        'debug_logging': False,
        'default_duration': '10',
        'default_streaming_duration': '300',
        'player_name': '',
        'resolution': '1920x1080',
        'show_splash': True,
        'shuffle_playlist': False,
        'verify_ssl': True,
        'usb_assets_key': '',
        'default_assets': False
    }
}
CONFIGURABLE_SETTINGS = DEFAULTS['viewer'].copy()
CONFIGURABLE_SETTINGS['use_24_hour_clock'] = DEFAULTS['main']['use_24_hour_clock']
CONFIGURABLE_SETTINGS['date_format'] = DEFAULTS['main']['date_format']

PORT = int(getenv('PORT', 8080))
LISTEN = getenv('LISTEN', '127.0.0.1')

# Initiate logging
logging.basicConfig(level=logging.INFO,
                    format='%(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S')

# Silence urllib info messages ('Starting new HTTP connection')
# that are triggered by the remote url availability check in view_web
requests_log = logging.getLogger("requests")
requests_log.setLevel(logging.WARNING)

logging.debug('Starting viewer.py')


class ScreenlySettings(IterableUserDict):
    """Screenly OSE's Settings."""

    def __init__(self, *args, **kwargs):
        IterableUserDict.__init__(self, *args, **kwargs)
        self.home = getenv('HOME')
        self.conf_file = self.get_configfile()
        self.auth_backends_list = [NoAuth(), BasicAuth(self)]
        if os.path.isdir('/opt/wott'):
            self.auth_backends_list.append(WoTTAuth(self))
        self.auth_backends = {}
        for backend in self.auth_backends_list:
            DEFAULTS.update(backend.config)
            self.auth_backends[backend.name] = backend

        if not path.isfile(self.conf_file):
            logging.error('Config-file %s missing. Using defaults.', self.conf_file)
            self.use_defaults()
            self.save()
        else:
            self.load()

    def _get(self, config, section, field, default):
        try:
            if isinstance(default, bool):
                self[field] = config.getboolean(section, field)
            elif isinstance(default, int):
                self[field] = config.getint(section, field)
            else:
                self[field] = config.get(section, field)
                if field == 'password' and self[field] != '' and len(self[field]) != 64:   # likely not a hashed password.
                    self[field] = hashlib.sha256(self[field]).hexdigest()   # hash the original password.
        except ConfigParser.Error as e:
            logging.debug("Could not parse setting '%s.%s': %s. Using default value: '%s'." % (section, field, unicode(e), default))
            self[field] = default
        if field in ['database', 'assetdir']:
            self[field] = str(path.join(self.home, self[field]))

    def _set(self, config, section, field, default):
        if isinstance(default, bool):
            config.set(section, field, self.get(field, default) and 'on' or 'off')
        else:
            config.set(section, field, unicode(self.get(field, default)))

    def load(self):
        """Loads the latest settings from screenly.conf into memory."""
        logging.debug('Reading config-file...')
        config = ConfigParser.ConfigParser()
        config.read(self.conf_file)

        for section, defaults in DEFAULTS.items():
            for field, default in defaults.items():
                self._get(config, section, field, default)

    def use_defaults(self):
        for defaults in DEFAULTS.items():
            for field, default in defaults[1].items():
                self[field] = default

    def save(self):
        # Write new settings to disk.
        config = ConfigParser.ConfigParser()
        for section, defaults in DEFAULTS.items():
            config.add_section(section)
            for field, default in defaults.items():
                self._set(config, section, field, default)
        with open(self.conf_file, "w") as f:
            config.write(f)
        self.load()

    def get_configdir(self):
        return path.join(self.home, CONFIG_DIR)

    def get_configfile(self):
        return path.join(self.home, CONFIG_DIR, CONFIG_FILE)

    @property
    def auth(self):
        backend_name = self['auth_backend']
        if backend_name in self.auth_backends:
            return self.auth_backends[self['auth_backend']]


settings = ScreenlySettings()


class ZmqPublisher:
    INSTANCE = None

    def __init__(self):
        if self.INSTANCE is not None:
            raise ValueError("An instantiation already exists!")

        self.context = zmq.Context()

        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind('tcp://127.0.0.1:10001')
        sleep(1)

    @classmethod
    def get_instance(cls):
        if cls.INSTANCE is None:
            cls.INSTANCE = ZmqPublisher()
        return cls.INSTANCE

    def send_to_ws_server(self, msg):
        self.socket.send("ws_server {}".format(msg))

    def send_to_viewer(self, msg):
        self.socket.send_string("viewer {}".format(msg))


class ZmqConsumer:
    def __init__(self):
        self.context = zmq.Context()

        self.socket = self.context.socket(zmq.PUSH)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.connect('tcp://{}:5558'.format(LISTEN))

        sleep(1)

    def send(self, msg):
        self.socket.send_json(msg, flags=zmq.NOBLOCK)


class ZmqCollector:
    INSTANCE = None

    def __init__(self):
        if self.INSTANCE is not None:
            raise ValueError("An instantiation already exists!")

        self.context = zmq.Context()

        self.socket = self.context.socket(zmq.PULL)
        self.socket.bind('tcp://127.0.0.1:5558')

        self.poller = zmq.Poller()
        self.poller.register(self.socket, zmq.POLLIN)

        sleep(1)

    @classmethod
    def get_instance(cls):
        if cls.INSTANCE is None:
            cls.INSTANCE = ZmqCollector()
        return cls.INSTANCE

    def recv_json(self, timeout):
        if self.poller.poll(timeout):
            return json.loads(self.socket.recv(zmq.NOBLOCK))

        raise ZmqCollectorTimeout
