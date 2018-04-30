#!/usr/bin/env python
# -*- coding: utf8 -*-

import md5
import sys
from mimetypes import guess_type
from os import link, path

from lib.utils   import get_video_duration

from lib import assets_helper
from lib import db
from lib import queries

from settings import settings



def main():
    make_database()

    # is_processing, is_enabled, nocache, play_order are default 0 in the SQL table
    # so we won't specify them here

    # Screenly generates a UUID the same size and function as an MD5 checksum, and
    # currently fails to set the MD5 value in the Assets database. So I decided to
    # use the MD5 checksum for the ID.
    #
    # (for reference, the Screenly code does this:
    #    import uuid
    #    asset['asset_id'] = uuid.uuid4().hex
    # )
    #
    # This has the advantage of setting a deterministic value for the filename of
    # a particular asset, so if I run this import code over and over, I don't
    # generate a huge number of duplicate links to the imported asset.


    asset = {
        'start_date':   '2018-01-01 00:00:00',
        'end_date':     '2038-01-01 00:00:00',
        'is_enabled':   0,
    }


    for arg in sys.argv[1:]:
        filename          = path.abspath(arg)

        asset['md5']      = md5sum(filename)
        asset['asset_id'] = asset['md5']

        uri               = path.join(settings['assetdir'], asset['asset_id'])
        asset['uri']      = uri
        asset['name']     = filename


        if not path.exists(uri):
            link(filename, uri)

            asset['mimetype'] = guess_type(filename)[0]

            if "video" in asset['mimetype']:
                asset['duration'] = int(get_video_duration(uri).total_seconds())
            else:
                asset['duration'] = settings['default_duration']

            with db.conn(settings['database']) as conn:
                assets_helper.create(conn, asset)
                print asset['asset_id'], "\t", asset['name']
#
############



def md5sum(filename, blocksize=65536):
    h = md5.new()
    with open(filename, "rb") as f:
        for block in iter(lambda: f.read(blocksize), b""):
            h.update(block)
    return h.hexdigest()
####


def make_database():
    # Make sure the asset folder exist. If not, create it
    if not path.isdir(settings['assetdir']):
        mkdir(settings['assetdir'])
    # Create config dir if it doesn't exist
    if not path.isdir(settings.get_configdir()):
        makedirs(settings.get_configdir())

    with db.conn(settings['database']) as conn:
        with db.cursor(conn) as cursor:
            cursor.execute(queries.exists_table)
            if cursor.fetchone() is None:
                cursor.execute(assets_helper.create_assets_table)
################


### NOTE: I know this is stupid, but here I want to simply generate a list
###       of SQLite commands as text, rather than have this tool execute
###       any queries of its own.
###
def insert_string( d ):
    from string import Template
    INSERT = Template("insert into assets (asset_id,mimetype,name,end_date,uri,duration,start_date,md5) \
        values ('$asset_id','$mimetype','$name','$end_date','$uri','$duration','$start_date','$md5')")
    return INSERT.substitute(d)


##########################
##########################

if __name__ == '__main__':
  main()
