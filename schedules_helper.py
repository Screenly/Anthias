import db
import queries
import datetime
import classes

FIELDS = ["id", "asset_id", "name", "start_date", "start_time", "end_date", "end_time", "duration", "repeat", "priority", "pattern_type", "pattern_days"] 

#create_schedules_table = 'CREATE TABLE schedules(id integer primary key autoincrement, asset_id text, name text, start_date timestamp, end_date timestamp, duration integer, repeat integer default 0, priority integer default 0, pattern_days text, pattern_type text)'
create_schedules_table = 'CREATE TABLE schedules(id integer primary key autoincrement, asset_id text, name text, start_date text, start_time text, end_date text, end_time text, duration integer, repeat integer default 0, priority integer default 0, pattern_days integer default 0, pattern_type text);'

get_time = datetime.datetime.utcnow
get_date = datetime.date.today


def is_active(schedule, at_date=None, at_time=None):
    """Accepts a schedule dictionary and determines if it
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

    at = at_time or get_time()
    at_d = at_date or get_date()
    if (schedule['start_date'] and schedule['start_date'] <= at_d) and (schedule['repeat'] or schedule['end_date']) and (schedule['start_time'].time() <= at and schedule['end_time'].time() >= at):
        if schedule['repeat'] and (schedule['end_date'] == None or schedule['end_date'] >= at_d):
            if schedule['pattern_type'] == 'weekly':
                print 'Weekly Pattern Type'
            return schedule['pattern_type'] == 'daily'
        else:
            return schedule['end_date'] >= at
    return False


def get_schedules(asset_id, conn):
    """Returns all currently active schedules for an asset."""
    return filter(is_active, read(conn, asset_id))


def mkdict(keys):
    """Returns a function that creates a dict from a database record."""
    return lambda row: dict([(keys[ki], v) for ki, v in enumerate(row)])


def create(conn, schedule):
    """
    Create a database record for an schedule.
    Returns the schedule.
    Asset's is_active field is updated before returning.
    """
    with db.commit(conn) as c:
        c.execute(queries.create_schedule(schedule.keys()), schedule.values())
    return schedule


def read(conn, asset_id, keys=FIELDS):
    """
    Fetch one or more schedules from the database.
    Returns a list of dicts or one dict.
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
    """Remove a schedule from the database."""
    with db.commit(conn) as c:
        c.execute(queries.remove_schedule, [schedule_id])