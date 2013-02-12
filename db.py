import sqlite3

from settings import settings


class Connection(object):
    """Database connection."""

    def __init__(self, database=None):
        self.database = database or settings.database
        self._conn = None

    @property
    def connection(self):
        # Not thread safe.
        if not self._conn:
            self._conn = sqlite3.connect(self.database, detect_types=sqlite3.PARSE_DECLTYPES)
        return self._conn

    def cursor(self):
        return self.connection.cursor()

    def commit(self):
        if self._conn:
            self._conn.commit()

    def rollback(self):
        if self._conn:
            self._conn.rollback()

    def close(self):
        if self._conn:
            self._conn.close()
        self._conn = None

# Default connection based on settings in settings.py.
connection = Connection(settings.database)
