# -*- coding: utf8 -*-

__author__ = "Viktor Petersson, Christian Nilsson"
__copyright__ = "Copyright 2012, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"
__version__ = "0.1"
__email__ = "vpetersson@wireload.net"

import ConfigParser
from os import path, getenv
from datetime import datetime

def logg(string, out=None):
    if out is None:
        print string
    else:
        out(string)

class Config:
    """Handle all configuration parameters for screenly"""

    def __init__(self, debug_out=None):
        # Get config file
        config = ConfigParser.ConfigParser({'listen':'0.0.0.0','port':'8080','resolution':'1920x1080'})
        conf_file = path.join(getenv('HOME'), '.screenly', 'screenly.conf')
        if not path.isfile(conf_file):
            raise Exception('Config-file %s missing.' % conf_file)
        logg('Reading config-file...', debug_out)
        config.read(conf_file)

        # Get main config values
        self.database = path.join(getenv('HOME'), config.get('main', 'database'))
        self.nodetype = config.get('main', 'nodetype')

        #this listen directive needs to be handled in viewer,
        #  if it is 0.0.0.0 then the viewer shuld connect to 127.0.0.0 otherwise it shuld be configured adress,
        #  the same goes in server, but instead of 127.0.0.1 it shuld be the found adress.
        self.listen = config.get('main', 'listen')
        self.port = config.getint('main', 'port')

        # Get server config values
        self.configdir = path.join(getenv('HOME'), config.get('main', 'configdir'))

        # Get viewer config values
        self.show_splash = config.getboolean('viewer', 'show_splash')
        self.audio_output = config.get('viewer', 'audio_output')
        self.shuffle_playlist = config.getboolean('viewer', 'shuffle_playlist')
        self.resolution = config.get('viewer', 'resolution')

    def time_lookup(self):
        if self.nodetype == "standalone":
            return datetime.now()
        elif self.nodetype == "managed":
            return datetime.utcnow()
