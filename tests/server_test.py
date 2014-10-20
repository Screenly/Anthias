#!/usr/bin/env python
# -*- coding: utf8 -*-

import datetime
import functools
import unittest

import assets_helper
import db
import server
import utils

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

asset_w = {
    'mimetype': u'web',
    'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d',
    'name': u'いろはにほへど',
    'uri': u'http://www.wireload.net',
    'start_date': date_a,
    'end_date': date_b,
    'duration': u'5',
    'is_enabled': 1,
    'nocache': 0,
    'play_order': 1,
}

asset_w_diff = {
    'name': u'Tôi có thể ăn thủy tinh mà không hại gì.'
}

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
    'play_order': 1,
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
    'play_order': 0,
}
asset_y_diff = {
    'duration': u'324'
}
asset_z = {
    'mimetype': u'image',
    'asset_id': u'9722cd9c45e44dc9b23521be8132b38f',
    'name': u'url test',
    'start_date': date_c.isoformat(),
    'end_date': date_d.isoformat(),
    'duration': u'1',
    'is_enabled': 1,
    'nocache': 0,
}
url_fail = 'http://doesnotwork.example.com'
url_redir = 'http://example.com'
uri_ = '/home/user/file'
#url_timeout = 'http://...'


class Req():
    def __init__(self, asset):
        self.POST = asset


class URLHelperTest(unittest.TestCase):
    def test_url_1(self):
        self.assertTrue(server.url_fails(url_fail))

    def test_url_2(self):
        self.assertFalse(server.url_fails(url_redir))

    def test_url_3(self):
        self.assertFalse(server.url_fails(uri_))


class DBHelperTest(unittest.TestCase):
    def setUp(self):
        self.assertEmpty = functools.partial(self.assertEqual, [])
        self.conn = db.conn(':memory:')
        with db.commit(self.conn) as cursor:
            cursor.execute(assets_helper.create_assets_table)

    def tearDown(self):
        self.conn.close()
    # ✂--------

    def test_create_read_asset(self):
        assets_helper.create(self.conn, asset_x)
        assets_helper.create(self.conn, asset_y)
        should_be_y_x = assets_helper.read(self.conn)
        self.assertEqual([asset_y, asset_x], should_be_y_x)
    # ✂--------

    def test_create_update_read_asset(self):
        assets_helper.create(self.conn, asset_x)
        asset_x_ = asset_x.copy()
        asset_x_.update(**asset_x_diff)
        assets_helper.update(self.conn, asset_x['asset_id'], asset_x_)

        assets_helper.create(self.conn, asset_y)
        asset_y_ = asset_y.copy()
        asset_y_.update(**asset_y_diff)
        assets_helper.update(self.conn, asset_y['asset_id'], asset_y_)

        should_be_y__x_ = assets_helper.read(self.conn)
        self.assertEqual([asset_y_, asset_x_], should_be_y__x_)
    # ✂--------

    def test_create_delete_asset(self):
        assets_helper.create(self.conn, asset_x)
        assets_helper.delete(self.conn, asset_x['asset_id'])

        assets_helper.create(self.conn, asset_y)
        assets_helper.delete(self.conn, asset_y['asset_id'])

        should_be_empty = assets_helper.read(self.conn)
        self.assertEmpty(should_be_empty)
    # ✂--------

    def test_create_update_read_asset_utf8(self):
        assets_helper.create(self.conn, asset_w)
        asset_w_ = asset_w.copy()
        asset_w_.update(**asset_w_diff)
        assets_helper.update(self.conn, asset_w['asset_id'], asset_w_)

        should_be_w_ = assets_helper.read(self.conn)
        self.assertEqual([asset_w_], should_be_w_)
    # ✂--------

    def set_now(self, d):
        assets_helper.get_time = lambda: d

    def test_get_playlist(self):
        assets_helper.create(self.conn, asset_x)
        assets_helper.create(self.conn, asset_y)

        self.set_now(date_e)
        should_be_empty = assets_helper.get_playlist(self.conn)
        self.assertEmpty(should_be_empty)

        self.set_now(date_f)
        [should_be_x] = assets_helper.get_playlist(self.conn)
        self.assertEqual(asset_x['asset_id'], should_be_x['asset_id'])

        self.set_now(date_g)
        should_be_y_x = assets_helper.get_playlist(self.conn)
        self.assertEqual([should_be_y_x[0]['asset_id'],
                          should_be_y_x[1]['asset_id']],
                         [asset_y['asset_id'],
                          asset_x['asset_id']])

        self.set_now(date_h)
        [should_be_y] = assets_helper.get_playlist(self.conn)
        self.assertEqual(asset_y['asset_id'], should_be_y['asset_id'])
        # ✂--------
