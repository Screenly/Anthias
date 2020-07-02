import db
import queries
import datetime
import schedules_helper
from classes import AvailableDays
import operator
import string
from settings import settings

FIELDS = ["asset_id", "name", "uri", "duration", "mimetype", "is_enabled", "nocache", "play_order"]

create_assets_table = 'CREATE TABLE assets(asset_id text primary key, name text, uri text, md5 text, duration text, mimetype text, is_enabled integer default 0, nocache integer default 0, play_order integer default 0)'

get_time = datetime.datetime.utcnow
get_date = datetime.date.today


def is_active(asset, at_time=None):
    """Accepts an asset dictionary and determines if it
    is active at the given time. If no time is specified, 'now' is used.

    >>> asset = {'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d', 'mimetype': u'web', 'name': u'WireLoad', 'end_date': datetime.datetime(2013, 1, 19, 23, 59), 'uri': u'http://www.wireload.net', 'duration': u'5', 'is_enabled': True, 'nocache': 0, 'play_order': 1, 'start_date': datetime.datetime(2013, 1, 16, 0, 0)};
    >>> is_active(asset, datetime.datetime(2013, 1, 16, 12, 00))
    True
    >>> is_active(asset, datetime.datetime(2014, 1, 1))
    False

    >>> asset['is_enabled'] = False
    >>> is_active(asset, datetime.datetime(2013, 1, 16, 12, 00))
    False

    """

    if asset['is_enabled']:
        return True
    return False

def is_scheduled(asset, conn, at_time=None):
    """Similar to is_active, but checks to see if the current asset is scheudled
    in addition to if it's active"""

    return asset['is_enabled'] and schedules_helper.asset_has_active_schedule(asset,conn)

def get_playlist(conn):
    """Returns all currently active assets."""
    #return filter(is_active, read(conn))
    return [asset for asset in read(conn) if is_scheduled(asset, conn)]

def get_playlist_scheduled(conn):
    """Returns all currently active assets that are scheduled to play."""
    return list(filter(is_scheduled, read(conn)))


def mkdict(keys):
    """Returns a function that creates a dict from a database record."""
    return lambda row: dict([(keys[ki], v) for ki, v in enumerate(row)])


def create(conn, asset):
    """
    Create a database record for an asset.
    Returns the asset.
    Asset's is_active field is updated before returning.
    A default schedule is also created.
    """
    if 'is_active' in asset:
        asset.pop('is_active')
    print(asset)
    with db.commit(conn) as c:
        c.execute(queries.create(list(asset.keys())), list(asset.values()))
    asset.update({'is_active': is_active(asset)})
    settings.load()
    default_schedule = {
        'asset_id' : asset['asset_id'],
        'name': 'Default Schedule',
        'start_date': datetime.date.today(),
        'duration': settings['default_duration'],
        'repeat': 0,
        'priority': 0,
        'pattern_type': '',
        'pattern_days': None,
    }
    schedules_helper.create(conn, default_schedule)
    return asset


def read(conn, asset_id=None, keys=FIELDS):
    """
    Fetch one or more assets from the database.
    Returns a list of dicts or one dict.
    Assets' is_active field is updated before returning.
    """
    assets = []
    mk = mkdict(keys)
    with db.cursor(conn) as c:
        if asset_id is None:
            c.execute(queries.read_all(keys))
        else:
            c.execute(queries.read(keys), [asset_id])
        assets = [mk(asset) for asset in c.fetchall()]
    [asset.update({'is_active': is_active(asset)}) for asset in assets]
    if asset_id and len(assets):
        return assets[0]
    return assets


def update(conn, asset_id, asset):
    """
    Update an asset in the database.
    Returns the asset.
    Asset's asset_id and is_active field is updated before returning.
    """
    del asset['asset_id']
    if 'is_active' in asset:
        asset.pop('is_active')
    with db.commit(conn) as c:
        c.execute(queries.update(list(asset.keys())), list(asset.values()) + [asset_id])
    asset.update({'asset_id': asset_id})
    if 'start_date' in asset:
        asset.update({'is_active': is_active(asset)})
    return asset


def delete(conn, asset_id):
    """Remove an asset from the database."""
    with db.commit(conn) as c:
        c.execute(queries.remove, [asset_id])
