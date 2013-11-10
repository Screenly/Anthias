import db
import queries
import datetime

FIELDS = ["id", "asset_id", "name", "start_date", "end_date", "duration", "repeat", "priority", "pattern_days"]

create_schedules_table = 'CREATE TABLE schedules(id integer primary key autoincrement, asset_id text, name text, start_date timestamp, end_date timestamp, duration integer, repeat integer default 0, priority integer default 0, pattern_days text)'

get_time = datetime.datetime.utcnow


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

    if asset['is_enabled'] and asset['start_date'] and asset['end_date']:
        at = at_time or get_time()
        return asset['start_date'] < at and asset['end_date'] > at
    return False


def get_playlist(conn):
    """Returns all currently active assets."""
    return filter(is_active, read(conn))


def mkdict(keys):
    """Returns a function that creates a dict from a database record."""
    return lambda row: dict([(keys[ki], v) for ki, v in enumerate(row)])


def create(conn, schedule):
    """
    Create a database record for an asset.
    Returns the asset.
    Asset's is_active field is updated before returning.
    """
    with db.commit(conn) as c:
        c.execute(queries.create_schedule(schedule.keys()), schedule.values())
    return schedule


def read(conn, asset_id, keys=FIELDS):
    """
    Fetch one or more schedules from the database.
    Returns a list of dicts or one dict.
    Assets' is_active field is updated before returning.
    """
    schedules = []
    mk = mkdict(keys)
    with db.cursor(conn) as c:
        c.execute(queries.read_schedule(keys), [asset_id])
        schedules = [mk(schedule) for schedule in c.fetchall()]
    return schedules


def update(conn, id, schedule):
    """
    Update an schedule in the database.
    Returns the schedule.
    schedule's id field is updated before returning.
    """
    with db.commit(conn) as c:
        c.execute(queries.update_schedule(schedule.keys()), schedule.values() + [id])
    return schedule


def delete(conn, schedule_id):
    """Remove an asset from the database."""
    with db.commit(conn) as c:
        c.execute(queries.remove_schedule, [schedule_id])
