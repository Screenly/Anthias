import sqlite3
from contextlib import contextmanager
from datetime import time

import queries

def adapt_timeobj(timeobj):
    return ((3600*timeobj.hour + 60*timeobj.minute + timeobj.second)*10**6 
            + timeobj.microsecond)

def convert_timeobj(val):
    val = int(val)
    hour, val = divmod(val, 3600*10**6)
    minute, val = divmod(val, 60*10**6)
    second, val = divmod(val, 10**6)
    microsecond = int(val)
    return time(hour, minute, second, microsecond)


# Converts datetime.time to TEXT when inserting
sqlite3.register_adapter(time, adapt_timeobj)

# Converts TEXT to datetime.time when selecting
sqlite3.register_converter("time", convert_timeobj)

conn = lambda db: sqlite3.connect(db, detect_types=sqlite3.PARSE_DECLTYPES)

@contextmanager
def cursor(connection):
    cur = connection.cursor()
    yield cur
    cur.close()


@contextmanager
def commit(connection):
    cur = connection.cursor()
    yield cur
    connection.commit()
    cur.close()
