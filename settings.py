#!/usr/bin/env python
# -*- coding: utf8 -*-

from os import path, getenv
from sys import exit
import ConfigParser
import datetime
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

# Get config file
config = ConfigParser.ConfigParser()
conf_file = path.join(getenv('HOME'), '.screenly', 'screenly.conf')
if not path.isfile(conf_file):
    logging.info('Config-file missing.')
    exit(1)
else:
    logging.debug('Reading config-file...')
    config.read(conf_file)

# Get config values
configdir = path.join(getenv('HOME'), config.get('main', 'configdir'))
database = path.join(getenv('HOME'), config.get('main', 'database'))
nodetype = config.get('main', 'nodetype')
show_splash = str_to_bol(config.get('viewer', 'show_splash'))
audio_output = config.get('viewer', 'audio_output')
shuffle_playlist = str_to_bol(config.get('viewer', 'shuffle_playlist'))

try:
    resolution = config.get('viewer', 'resolution')
except:
    resolution = '1920x1080'

# This assumes nodetype never changes from "standalone" to "managed" during a run.
get_current_time = datetime.now if nodetype == "standalone" else datetime.utcnow
