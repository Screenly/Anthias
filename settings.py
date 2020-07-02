#!/usr/bin/env python
# -*- coding: utf8 -*-

from os import path, getenv
from sys import exit
import configparser
import logging
from collections import UserDict

CONFIG_DIR = '.pisign/'
CONFIG_FILE = 'pisign.conf'
DEFAULTS = {
    'main': {
        'database': CONFIG_DIR + 'pisign.db',
        'listen': '0.0.0.0:8080',
        'assetdir': 'pisign_assets',
    },
    'viewer': {
        'show_splash': True,
        'audio_output': 'hdmi',
        'shuffle_playlist': False,
        'resolution': '1920x1080',
        'default_duration': '30',
        'debug_logging': False,
        'verify_ssl': True,
    }
}

# Initiate logging
logging.basicConfig(level=logging.INFO,
                    filename='/tmp/screenly_viewer.log',
                    format='%(asctime)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S')

# Silence urllib info messages ('Starting new HTTP connection')
# that are triggered by the remote url availability check in view_web
requests_log = logging.getLogger("requests")
requests_log.setLevel(logging.WARNING)

logging.debug('Starting viewer.py')


class ScreenlySettings(UserDict):
    "Screenly OSE's Settings."

    def __init__(self, *args, **kwargs):
        rv = UserDict.__init__(self, *args, **kwargs)
        self.home = getenv('HOME')
        self.conf_file = self.get_configfile()

        if not path.isfile(self.conf_file):
            logging.error('Config-file %s missing', self.conf_file)
            exit(1)
        else:
            self.load()
        return rv

    def _get(self, config, section, field, default):
        try:
            if isinstance(default, bool):
                self[field] = config.getboolean(section, field)
            elif isinstance(default, int):
                self[field] = config.getint(section, field)
            else:
                self[field] = config.get(section, field)
        except configparser.Error as e:
            logging.debug("Could not parse setting '%s.%s': %s. Using default value: '%s'." % (section, field, str(e), default))
            self[field] = default
        if field in ['database', 'assetdir']:
            self[field] = str(path.join(self.home, self[field]))

    def _set(self, config, section, field, default):
        if isinstance(default, bool):
            config.set(section, field, self.get(field, default) and 'on' or 'off')
        else:
            config.set(section, field, str(self.get(field, default)))

    def load(self):
        "Loads the latest settings from screenly.conf into memory."
        logging.debug('Reading config-file...')
        config = configparser.ConfigParser()
        config.read(self.conf_file)

        for section, defaults in list(DEFAULTS.items()):
            for field, default in list(defaults.items()):
                self._get(config, section, field, default)
        try:
            self.get_listen_ip()
            int(self.get_listen_port())
        except ValueError as e:
            logging.info("Could not parse setting 'listen': %s. Using default value: '%s'." % (str(e), DEFAULTS['main']['listen']))
            self['listen'] = DEFAULTS['main']['listen']

    def save(self):
        # Write new settings to disk.
        config = configparser.ConfigParser()
        for section, defaults in list(DEFAULTS.items()):
            config.add_section(section)
            for field, default in list(defaults.items()):
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

settings = ScreenlySettings()
