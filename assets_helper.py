import db
import queries
import datetime

def is_active(asset, at_time=None):
    """Accepts an asset dictionary and determines if it
    is active at the given time. If no time is specified, 'now' is used.

    >>> asset = {'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d', 'mimetype': u'web', 'name': u'WireLoad', 'end_date': datetime(2013, 1, 19, 23, 59), 'uri': u'http://www.wireload.net', 'duration': u'5', 'start_date': datetime(2013, 1, 16, 0, 0)};

    >>> is_active(asset, datetime(2013, 1, 16, 12, 00))
    True
    >>> is_active(asset, datetime(2014, 1, 1))
    False

    """

    if asset['start_date'] and asset['end_date']:
        at = at_time or datetime.datetime.utcnow()
        return asset['start_date'] < at and asset['end_date'] > at
    return False

def get_playlist(conn):
    """Returns all currently active assets."""
    predicate = lambda ass: ass['is_enabled'] == 1 and is_active(ass)
    return filter(predicate, read(conn))

def mkdict(keys):
    """Returns a function that creates a dict from a database record."""
    return lambda row: dict([(keys[ki],v) for ki,v in enumerate(row)])

def create(conn, asset):
    """
    Create a database record for an asset.
    Returns the asset.
    Asset's is_active field is updated before returning.
    """
    with db.commit(conn) as c:
        c.execute(queries.create(asset.keys()),asset.values())
    asset.update({'is_active': is_active(asset)})
    return asset

def read(conn, asset_id=None, keys=db.FIELDS):
    """
    Fetch one or more assets from the database.
    Returns a list of dicts or one dict.
    Assets' is_active field is updated before returning.
    """
    assets = []
    mk = mkdict(keys)
    with db.cursor(conn) as c:
        if asset_id == None:
            c.execute(queries.read_all(keys))
        else:
            c.execute(queries.read(keys),[asset_id])
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
    with db.commit(conn) as c:
        c.execute(queries.update(asset.keys()), asset.values() + [asset_id])
    asset.update({'asset_id': asset_id})
    asset.update({'is_active': is_active(asset)})
    return asset

def delete(conn, asset_id):
    """Remove an asset from the database."""
    with db.commit(conn) as c:
        c.execute(queries.remove, [asset_id])
