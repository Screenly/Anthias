import sqlite3
from contextlib import contextmanager
import queries

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
