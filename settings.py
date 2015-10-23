#!/usr/bin/env python
# -*- coding: utf8 -*-

from os import path, getenv
import ConfigParser
import logging
import copy

CONFIG_DIR = '.screenly/'
CONFIG_FILE = 'screenly.conf'


def config_dir(home=getenv('HOME')):
    return path.join(home, CONFIG_DIR)


def config_file(home=getenv('HOME')):
    return path.join(home, CONFIG_DIR, CONFIG_FILE)

DEFAULTS = {
    'main': {
        'database': CONFIG_DIR + 'screenly.db',
        'listen': '0.0.0.0:8080',
        'assetdir': 'screenly_assets',
        'use_24_hour_clock': False
    },
    'viewer': {
        'show_splash': True,
        'audio_output': 'hdmi',
        'shuffle_playlist': False,
        'resolution': '1920x1080',
        'default_duration': '10',
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


def load(conf_path=config_file()):
    def _get_fn(parser, default):
        if isinstance(default, bool):
            return parser.getboolean
        elif isinstance(default, int):
            return parser.getint
        return parser.get

    parser = ConfigParser.ConfigParser()
    with open(conf_path) as fp:
        parser.readfp(fp)

    conf = copy.deepcopy(DEFAULTS)
    # Iterate over default config and load
    for section, options in conf.items():
        for option, default in options.items():
            try:
                conf[section][option] = _get_fn(parser, default)(section, option)
            except ConfigParser.Error as e:
                logging.debug("Could not parse setting '%s.%s': %s. Using default value: '%s'." % (
                    section, option, unicode(e), default))

    return ScreenlySettings(conf)


class ScreenlySettings:
    def __init__(self, conf):
        self.conf = conf

    def _find_section(self, item):
        # Try to find in all sections
        for section in self.conf.keys():
            if item in self.conf[section]:
                return section

        raise AttributeError(item)

    def __getitem__(self, item):
        section = self._find_section(item)
        return self.conf[section][item]

    def __setitem__(self, key, value):
        section = self._find_section(key)
        self.conf[section][key] = value

    def get_path(self, item, absolute=True, home=getenv('HOME')):
        return path.join(home, self[item]) if absolute else self[item]

    def get_listen_ip(self):
        return self['listen'].split(':')[0]

    def get_listen_port(self):
        return self['listen'].split(':')[1]


def save(settings, conf_path=config_file()):
    parser = ConfigParser.ConfigParser()

    # Use sorting to make options order interpreter-independent
    for section, options in sorted(settings.conf.items()):
        parser.add_section(section)
        for option, value in sorted(options.items()):
            parser.set(section, option, value)

    with open(conf_path, mode='w') as f:
        parser.write(f)