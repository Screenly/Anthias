import sqlite3
from contextlib import contextmanager

import queries

FIELDS = [
    "asset_id", "name", "uri", "start_date",
    "end_date", "duration", "mimetype", "is_enabled", "nocache"
]

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


def create_assets_table(cur):
    try:
        cur.execute(queries.create_assets_table)
    except sqlite3.OperationalError as _:
        pass
