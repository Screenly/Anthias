import db
import queries
import datetime
from classes import AvailableDays
import logging

FIELDS = ["id", "asset_id", "name", "start_date", "start_time", "end_date", "end_time", "duration", "repeat", "priority", "pattern_type", "pattern_days"] 

#create_schedules_table = 'CREATE TABLE schedules(id integer primary key autoincrement, asset_id text, name text, start_date timestamp, end_date timestamp, duration integer, repeat integer default 0, priority integer default 0, pattern_days text, pattern_type text)'
create_schedules_table = 'CREATE TABLE schedules(id integer primary key autoincrement, asset_id text, name text, start_date date, start_time timestamp, end_date date, end_time timestamp, duration integer, repeat integer default 0, priority integer default 0, pattern_days integer default 0, pattern_type text);'

get_time = datetime.datetime.now
get_date = datetime.date.today


def asset_has_active_schedule(asset, conn, at_date=None, at_time=None):
    schedules = read(conn, asset['asset_id'])

    at_t = at_time or get_time().time()
    at_d = at_date or get_date()

    for schedule in schedules:
        asset['duration'] = schedule['duration']
        if (schedule['start_date']):
            if schedule['start_date'] <= at_d and (schedule['end_date'] and at_d <= schedule['end_date']):
                if schedule['repeat']:
                    if schedule['pattern_type'] == 'weekly':
                        for name, member in AvailableDays.__members__.items():
                            if ((int(schedule['pattern_days']) & member.value) > 0) and (member.name == datetime.datetime.now().strftime('%A')):
                                return scheduled_withinTimePeriod(at_t, schedule)
                        return False
                return scheduled_withinTimePeriod(at_t, schedule);
            pass
        return scheduled_withinTimePeriod(at_t, schedule)
    asset['duration'] = None
    return False

def scheduled_withinTimePeriod(at_t, schedule):
    if schedule['start_time'] and schedule['end_time']:
        return schedule['start_time'].time() < at_t and at_t < schedule['end_time'].time()
    return True

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