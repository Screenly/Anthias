import unittest
import datetime

from mock import patch, MagicMock

import db
import server


class ServerTest(unittest.TestCase):
    def test_get_playlist(self):
        """Test that the playlists retrieved are accurate."""

        # Set up a mock database.
        with patch('db.Connection') as mock:
            instance = mock.return_value
            instance.method.return_value = 'the result'
            server.connection = mock

        fake_cursor = server.connection.cursor.return_value
        fake_cursor.execute.return_value = None
        fake_cursor.fetchall.return_value = (
                (u'7e978f8c1204a6f70770a1eb54a76e9b', u'Google', u'https://www.google.com/images/srpr/logo3w.png', None, datetime.datetime(2013, 1, 17, 00, 00), datetime.datetime(2013, 1, 21, 00, 00), u'6', u'image'),
                (u'4c8dbce552edb5812d3a866cfe5f159d', u'WireLoad', u'http://www.wireload.net', None, datetime.datetime(2013, 1, 16, 00, 00), datetime.datetime(2013, 1, 19, 23, 59), u'5', u'web')
            )

        # Fake the current time.
        server.get_current_time = MagicMock()
        # During the WL ad but before the Google ad starts.
        server.get_current_time.return_value = datetime.datetime(2013, 1, 16, 12, 00)

        pl = server.get_playlist()
        fake_cursor.execute.assert_called_once_with("SELECT * FROM assets ORDER BY name")
        self.assertEquals(pl, [
                {'mimetype': u'web', 'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d', 'name': u'WireLoad', 'end_date': '2013-01-19 @ 23:59', 'uri': u'http://www.wireload.net', 'duration': u'5', 'start_date': '2013-01-16 @ 00:00'}
            ])

        # During the both WL and Google ad.
        server.get_current_time.return_value = datetime.datetime(2013, 1, 17, 12, 00)
        pl = server.get_playlist()
        self.assertEquals(pl, [
                {'mimetype': u'image', 'asset_id': u'7e978f8c1204a6f70770a1eb54a76e9b', 'name': u'Google', 'end_date': '2013-01-21 @ 00:00', 'uri': u'https://www.google.com/images/srpr/logo3w.png', 'duration': u'6', 'start_date': '2013-01-17 @ 00:00'},
                {'mimetype': u'web', 'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d', 'name': u'WireLoad', 'end_date': '2013-01-19 @ 23:59', 'uri': u'http://www.wireload.net', 'duration': u'5', 'start_date': '2013-01-16 @ 00:00'},
            ])

        server.get_current_time.return_value = datetime.datetime(2013, 1, 20, 12, 00)
        pl = server.get_playlist()
        self.assertEquals(pl, [
                {'mimetype': u'image', 'asset_id': u'7e978f8c1204a6f70770a1eb54a76e9b', 'name': u'Google', 'end_date': '2013-01-21 @ 00:00', 'uri': u'https://www.google.com/images/srpr/logo3w.png', 'duration': u'6', 'start_date': '2013-01-17 @ 00:00'},
            ])

        server.get_current_time.return_value = datetime.datetime(2013, 1, 21, 00, 01)
        pl = server.get_playlist()
        self.assertEquals(pl, [])
