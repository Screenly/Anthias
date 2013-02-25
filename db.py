import sqlite3
from contextlib import contextmanager
from settings import settings

FIELDS = [
    "asset_id", "name", "uri", "start_date",
    "end_date", "duration", "mimetype", "is_enabled", "nocache"
]

conn = lambda db: sqlite3.connect(db, detect_types=sqlite3.PARSE_DECLTYPES)

@contextmanager
def cursor(connection):
    cursor = connection.cursor()
    yield cursor
    cursor.close()

@contextmanager
def commit(connection):
    cursor = connection.cursor()
    yield cursor
    connection.commit()
    cursor.close()
