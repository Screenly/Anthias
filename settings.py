#!/usr/bin/env python
# -*- coding: utf8 -*-

from datetime import datetime
from os import path, getenv
from sys import exit
import ConfigParser
import logging


def str_to_bol(string):
    return 'true' in string.lower()

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


class ScreenlySettings(object):
    "Screenly OSE's Settings."

    def __init__(self):
        self.conf_file = path.join(getenv('HOME'), '.screenly', 'screenly.conf')

        if not path.isfile(self.conf_file):
            print 'Config-file missing.'
            logging.error('Config-file missing.')
            exit(1)
        else:
            self.load_settings()

    def load_settings(self):
        "Loads the latest settings from screenly.conf into memory."

        # Get config file
        config = ConfigParser.ConfigParser()

        logging.debug('Reading config-file...')
        config.read(self.conf_file)

        # Get config values
        self.configdir = path.join(getenv('HOME'), config.get('main', 'configdir'))
        self.database = path.join(getenv('HOME'), config.get('main', 'database'))
        self.nodetype = config.get('main', 'nodetype')
        self.show_splash = str_to_bol(config.get('viewer', 'show_splash'))
        self.audio_output = config.get('viewer', 'audio_output')
        self.shuffle_playlist = str_to_bol(config.get('viewer', 'shuffle_playlist'))
        self.asset_folder = path.join(getenv('HOME'), 'screenly_assets')

        try:
            self.resolution = config.get('viewer', 'resolution')
        except:
            self.resolution = '1920x1080'

        try:
            # Expect the string in format: ip:port
            listen = config.get('main', 'listen').split(':')
            self.listen_ip = listen[0]
            self.listen_port = listen[1]
        except:
            self.listen_ip = '0.0.0.0'
            self.listen_port = '8080'

        self.get_current_time = datetime.utcnow

settings = ScreenlySettings()
