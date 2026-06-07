"""Tests for the shared SQLite connection options.

Regression coverage for the fleet-wide ``OperationalError: database
is locked`` crashes (Sentry ANTHIAS-C/E/G): uvicorn, the celery
worker, and the viewer all open the same SQLite file from separate
containers, so every consumer of the settings module must connect
with a busy timeout and WAL journaling instead of the stock
fail-immediately rollback-journal behaviour.
"""

import sqlite3

from django.conf import settings


class TestSqliteConnectionOptions:
    def test_busy_timeout_is_set(self) -> None:
        options = settings.DATABASES['default']['OPTIONS']
        assert options['timeout'] == 20

    def test_wal_and_synchronous_pragmas_in_init_command(self) -> None:
        init_command = settings.DATABASES['default']['OPTIONS']['init_command']
        assert 'PRAGMA journal_mode=WAL' in init_command
        assert 'PRAGMA synchronous=NORMAL' in init_command

    def test_transactions_start_immediate(self) -> None:
        options = settings.DATABASES['default']['OPTIONS']
        assert options['transaction_mode'] == 'IMMEDIATE'

    def test_init_command_is_valid_sqlite(self, tmp_path) -> None:
        # Execute the exact configured init_command against a scratch
        # database the way Django does on connect — a typo'd pragma
        # would otherwise only surface at service startup on-device.
        init_command = settings.DATABASES['default']['OPTIONS']['init_command']
        conn = sqlite3.connect(tmp_path / 'scratch.db')
        try:
            conn.executescript(init_command)
            journal_mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
            synchronous = conn.execute('PRAGMA synchronous').fetchone()[0]
        finally:
            conn.close()
        assert journal_mode == 'wal'
        # NORMAL reports as 1.
        assert synchronous == 1
