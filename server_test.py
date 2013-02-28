#!/usr/bin/env python
# -*- coding: utf8 -*-

import datetime
import os

test_db = 'test.db'

# fixtures chronology
#
#         A           B
#         +===========+            -- asset X
#         |           |
# <----+--[--+--[--+--]--+--]--+---> (time)
#      |     |  |  |     |  |  |
#      |     |  +==+=====+==+  |   -- asset Y
#      |     |  C  |     |  D  |
#      |     |     |     |     |
#      E     F     G     H     I   -- test points
#      -     X     XY    Y     -   -- expected test result
date_e = datetime.datetime(2013, 1, 15, 00, 00)
date_a = datetime.datetime(2013, 1, 16, 00, 00)
date_f = datetime.datetime(2013, 1, 16, 12, 00)
date_c = datetime.datetime(2013, 1, 16, 23, 00)
date_g = datetime.datetime(2013, 1, 17, 10, 00)
date_b = datetime.datetime(2013, 1, 19, 23, 59)
date_h = datetime.datetime(2013, 1, 20, 10, 59)
date_d = datetime.datetime(2013, 1, 21, 00, 00)

asset_x = {
    'mimetype': u'web',
    'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d',
    'name': u'WireLoad',
    'uri': u'http://www.wireload.net',
    'start_date': date_a,
    'end_date': date_b,
    'duration': u'5',
    'is_enabled': 1,
    'nocache': 0,
    }
asset_x_diff = {
    'duration': u'10'
}
asset_y = {
    'mimetype': u'image',
    'asset_id': u'7e978f8c1204a6f70770a1eb54a76e9b',
    'name': u'Google',
    'uri': u'https://www.google.com/images/srpr/logo3w.png',
    'start_date': date_c,
    'end_date': date_d,
    'duration': u'6',
    'is_enabled': 1,
    'nocache': 0,
    }
asset_y_diff = {
    'duration': u'324'
}

import db
conn = None
# setUp and tearDown helpers
def mkdb():
    global conn
    conn = db.conn(test_db)
def initdb():
    mkdb()
    with db.commit(conn) as cursor:
        cursor.execute(queries.create_assets_table)
def rmdb():
    os.remove(test_db)

# ✂--------
import queries
import assets_helper
# ✂--------
def test_init_db():
    with db.commit(conn) as cursor:
        cursor.execute(queries.create_assets_table)
test_init_db.setUp = mkdb
test_init_db.tearDown = rmdb
# ✂--------
def test_create_read_asset():
    assets_helper.create(conn, asset_x)
    assets_helper.create(conn, asset_y)
    should_be_y_x = assets_helper.read(conn)
    assert [asset_y,asset_x] == should_be_y_x
test_create_read_asset.setUp = initdb
test_create_read_asset.tearDown = rmdb
# ✂--------
def test_create_update_read_asset():
    assets_helper.create(conn, asset_x)
    asset_x_ = asset_x.copy()
    asset_x_.update(**asset_x_diff)
    assets_helper.update(conn, asset_x['asset_id'], asset_x_)

    assets_helper.create(conn, asset_y)
    asset_y_ = asset_y.copy()
    asset_y_.update(**asset_y_diff)
    assets_helper.update(conn, asset_y['asset_id'], asset_y_)

    should_be_y__x_ = assets_helper.read(conn)
    assert [asset_y_, asset_x_] == should_be_y__x_
test_create_update_read_asset.setUp = initdb
test_create_update_read_asset.tearDown = rmdb
# ✂--------
def test_create_delete_asset():
    assets_helper.create(conn, asset_x)
    assets_helper.delete(conn, asset_x['asset_id'])

    assets_helper.create(conn, asset_y)
    assets_helper.delete(conn, asset_y['asset_id'])

    should_be_empty = assets_helper.read(conn)
    assert [] == should_be_empty
test_create_delete_asset.setUp = initdb
test_create_delete_asset.tearDown = rmdb
# ✂--------
def set_now(d):
    assets_helper.get_time = lambda: d
def test_get_playlist():
    assets_helper.create(conn, asset_x)
    assets_helper.create(conn, asset_y)

    set_now(date_e)
    should_be_empty = assets_helper.get_playlist(conn)
    assert [] == should_be_empty

    set_now(date_f)
    [should_be_x] = assets_helper.get_playlist(conn)
    assert asset_x['asset_id'] == should_be_x['asset_id']

    set_now(date_g)
    should_be_y_x = assets_helper.get_playlist(conn)
    assert should_be_y_x[0]['asset_id'] == asset_y['asset_id'] and should_be_y_x[1]['asset_id'] == asset_x['asset_id']

    set_now(date_h)
    [should_be_y] = assets_helper.get_playlist(conn)
    assert asset_y['asset_id'] == should_be_y['asset_id']
test_get_playlist.setUp = initdb
test_get_playlist.tearDown = rmdb
# ✂--------
