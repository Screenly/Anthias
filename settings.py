#!/usr/bin/env python
# -*- coding: utf8 -*-

from os import path, getenv
from sys import exit
from time import sleep
import ConfigParser
import logging
from UserDict import IterableUserDict
from flask import request, Response
from functools import wraps
import zmq

CONFIG_DIR = '.screenly/'
CONFIG_FILE = 'screenly.conf'
DEFAULTS = {
    'main': {
        'database': CONFIG_DIR + 'screenly.db',
        'listen': '0.0.0.0:8080',
        'assetdir': 'screenly_assets',
        'use_24_hour_clock': False,
        'websocket_port': '9999'
    },
    'viewer': {
        'player_name': '',
        'show_splash': True,
        'audio_output': 'hdmi',
        'shuffle_playlist': False,
        'resolution': '1920x1080',
        'default_duration': '10',
        'default_streaming_duration': '300',
        'debug_logging': False,
        'verify_ssl': True,
    },
    'auth': {
        'user': '',
        'password': ''
    }
}
CONFIGURABLE_SETTINGS = DEFAULTS['viewer']
CONFIGURABLE_SETTINGS['use_24_hour_clock'] = DEFAULTS['main']['use_24_hour_clock']

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

        if not path.isfile(self.conf_file):
            logging.error('Config-file %s missing', self.conf_file)
            exit(1)
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
        try:
            self.get_listen_ip()
            int(self.get_listen_port())
        except ValueError as e:
            logging.info("Could not parse setting 'listen': %s. Using default value: '%s'." % (unicode(e), DEFAULTS['main']['listen']))
            self['listen'] = DEFAULTS['main']['listen']

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

    def get_listen_ip(self):
        return self['listen'].split(':')[0]

    def get_listen_port(self):
        return self['listen'].split(':')[1]

    def check_user(self, user, password):
        if not self['user'] or not self['password']:
            logging.debug('Username or password not configured: skip authentication')
            return True

        return self['user'] == user and self['password'] == password


settings = ScreenlySettings()


class ZmqPublisher:
    INSTANCE = None

    def __init__(self):
        if self.INSTANCE is not None:
            raise ValueError("An instantiation already exists!")

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.connect('tcp://127.0.0.1:10001')
        sleep(1)

    @classmethod
    def get_instance(cls):
        if cls.INSTANCE is None:
            cls.INSTANCE = ZmqPublisher()
        return cls.INSTANCE

    def send(self, msg):
        self.socket.send(msg)


def authenticate():
    return Response("Access denied", 401, {"WWW-Authenticate": "Basic realm=private"})


def auth_basic(orig):
    @wraps(orig)
    def decorated(*args, **kwargs):
        if not settings['user'] or not settings['password']:
            return orig(*args, **kwargs)
        auth = request.authorization
        if not auth or not settings.check_user(auth.username, auth.password):
            return authenticate()
        return orig(*args, **kwargs)
    return decorated
