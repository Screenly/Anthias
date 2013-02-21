#!/usr/bin/env python
# -*- coding: utf8 -*-

from os import path, getenv
from sys import exit
import ConfigParser
import logging
from UserDict import IterableUserDict

CONFIG_DIR = path.join(getenv('HOME'), '.screenly')

DEFAULTS = {
    'main': {
        'database': '.screenly/screenly.db',
        'nodetype': 'standalone',
        'listen': '0.0.0.0:8080',
    },
    'viewer': {
        'show_splash': True,
        'audio_output': 'hdmi',
        'shuffle_playlist': False,
        'resolution': '1920x1080',
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


class ScreenlySettings(IterableUserDict):
    "Screenly OSE's Settings."

    def __init__(self, *args, **kwargs):
        rv = IterableUserDict.__init__(self, *args, **kwargs)
        self.conf_file = path.join(CONFIG_DIR, 'screenly.conf')

        if not path.isfile(self.conf_file):
            print 'Config-file missing.'
            logging.error('Config-file missing.')
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
        except ConfigParser.Error as e:
            logging.debug("Could not parse setting '%s.%s': %s. Using default value: '%s'." % (section, field, unicode(e), default))
            self[field] = default

    def _set(self, config, section, field, default):
        if isinstance(default, bool):
            config.set(section, field, self.get(field, default) and 'on' or 'off')
        else:
            config.set(section, field, unicode(self.get(field, default)))

    def load(self):
        "Loads the latest settings from screenly.conf into memory."
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
        return CONFIG_DIR

    def get_database(self):
        return path.join(getenv('HOME'), self['database'])

    def get_asset_folder(self):
        return path.join(getenv('HOME'), 'screenly_assets')

    def get_listen_ip(self):
        return self['listen'].split(':')[0]

    def get_listen_port(self):
        return self['listen'].split(':')[1]

settings = ScreenlySettings()
