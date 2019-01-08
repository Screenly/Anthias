#!/usr/bin/env python
# -*- coding: utf-8 -*-

import binascii
import datetime
import glob
import mimetypes
import os
import sys

from settings import settings
from lib import db
from lib import assets_helper


__author__ = "Screenly, Inc"
__copyright__ = "Copyright 2012-2017, Screenly, Inc"
__license__ = "Dual License: GPLv2 and Commercial License"


db_conn = None
settings.load()
db_conn = db.conn(settings['database'])


def add(mountpoint):
    files = [y for x in os.walk(mountpoint) for y in glob.glob(os.path.join(mountpoint, '*.*'))]

    for entry in files:
        asset = {
            'asset_id': binascii.b2a_hex(os.urandom(16)),
            'name': os.path.basename(entry),
            'uri': entry,
            'start_date': datetime.now(),
            'end_date': datetime.datetime(datetime.MAXYEAR, 12, 31),
            'mimetype': mimetypes.guess_type(entry)[0],
            'is_enabled': 1
        }

        if asset['mimetype'] and asset['mimetype'].startswith('image/'):
            assets_helper.create(db_conn, asset)

def remove(mountpoint):
    for asset in assets_helper.read(db_conn):
        if asset['uri'].startswith(mountpoint):
            assets_helper.delete(db_conn, asset['id'])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == 'add':
        add(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == 'remove':
        add(sys.argv[2])

