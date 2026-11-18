"""Tests for the shared SQLite connection options.

Regression coverage for the fleet-wide ``OperationalError: database
is locked`` crashes (Sentry ANTHIAS-C/E/G): uvicorn, the celery
worker, and the viewer all open the same SQLite file from separate
containers, so every consumer of the settings module must connect
with a busy timeout instead of the stock fail-immediately behaviour.

WAL journaling is the other half of that answer, but it belongs to the
database *file*, not to a connection — see ``lib.sqlite_journal``. The
tests below pin both halves, and in particular pin the property that
``init_command`` takes no lock: asserting WAL on every connect is what
turned a contended un-converted database into a 500 on every request
and, through the fds those failures leaked, into EMFILE (Sentry
ANTHIAS-38 → ANTHIAS-62/63).
"""

import sqlite3
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings

from anthias_server.app.apps import AnthiasAppConfig
from anthias_server.lib.sqlite_journal import ensure_wal_journal_mode


def _journal_mode(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return str(conn.execute('PRAGMA journal_mode').fetchone()[0]).lower()
    finally:
        conn.close()


def _make_rollback_journal_db(db_path: Path) -> None:
    """A database in the stock ``delete`` journal mode — what a legacy
    install, or a backup restored from one, actually looks like."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('CREATE TABLE t (x)')
        conn.commit()
    finally:
        conn.close()
    assert _journal_mode(db_path) == 'delete'


class TestSqliteConnectionOptions:
    def test_busy_timeout_is_set(self) -> None:
        options = settings.DATABASES['default']['OPTIONS']
        assert options['timeout'] == 20

    def test_synchronous_pragma_in_init_command(self) -> None:
        init_command = settings.DATABASES['default']['OPTIONS']['init_command']
        assert 'PRAGMA synchronous=NORMAL' in init_command

    def test_init_command_does_not_set_the_journal_mode(self) -> None:
        """Changing the journal mode takes an exclusive lock, so it can
        fail — and ``init_command`` runs inside Django's
        ``get_new_connection``, where a failure is an unusable
        connection rather than a degraded one."""
        init_command = settings.DATABASES['default']['OPTIONS']['init_command']
        assert 'journal_mode' not in init_command

    def test_transactions_start_immediate(self) -> None:
        options = settings.DATABASES['default']['OPTIONS']
        assert options['transaction_mode'] == 'IMMEDIATE'

    def test_init_command_is_valid_sqlite(self, tmp_path: Path) -> None:
        # Execute the exact configured init_command against a scratch
        # database the way Django does on connect — a typo'd pragma
        # would otherwise only surface at service startup on-device.
        init_command = settings.DATABASES['default']['OPTIONS']['init_command']
        conn = sqlite3.connect(tmp_path / 'scratch.db')
        try:
            conn.executescript(init_command)
            synchronous = conn.execute('PRAGMA synchronous').fetchone()[0]
        finally:
            conn.close()
        # NORMAL reports as 1.
        assert synchronous == 1

    def test_init_command_takes_no_lock(self, tmp_path: Path) -> None:
        """Connecting must succeed while another process holds the
        write lock on a database that is *not* in WAL.

        This is the ANTHIAS-38 shape: three processes on one file, one
        of them mid-transaction, and a connect that has to go through
        ``init_command`` before Django can use it.
        """
        db_path = tmp_path / 'contended.db'
        _make_rollback_journal_db(db_path)
        init_command = settings.DATABASES['default']['OPTIONS']['init_command']

        writer = sqlite3.connect(db_path, isolation_level=None)
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('INSERT INTO t VALUES (1)')
        try:
            conn = sqlite3.connect(db_path, timeout=0.1)
            try:
                for statement in filter(None, init_command.split(';')):
                    conn.execute(statement)
            finally:
                conn.close()
        finally:
            writer.rollback()
            writer.close()


class TestEnsureWalJournalMode:
    def test_converts_a_rollback_journal_database(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / 'legacy.db'
        _make_rollback_journal_db(db_path)

        assert ensure_wal_journal_mode(str(db_path)) == 'wal'
        assert _journal_mode(db_path) == 'wal'

    def test_leaves_an_already_wal_database_alone(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / 'already.db'
        conn = sqlite3.connect(db_path)
        try:
            conn.execute('PRAGMA journal_mode=WAL')
        finally:
            conn.close()

        assert ensure_wal_journal_mode(str(db_path)) == 'wal'

    def test_survives_a_contended_conversion(self, tmp_path: Path) -> None:
        """A database the helper cannot convert stays usable.

        The conversion is an optimisation; the busy timeout configured
        alongside it is the correctness guarantee. So a writer holding
        the lock costs a warning and the old journal mode, never an
        exception.
        """
        db_path = tmp_path / 'contended.db'
        _make_rollback_journal_db(db_path)

        writer = sqlite3.connect(db_path, isolation_level=None)
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('INSERT INTO t VALUES (1)')
        try:
            assert ensure_wal_journal_mode(str(db_path)) == 'delete'
        finally:
            writer.rollback()
            writer.close()

        assert _journal_mode(db_path) == 'delete'

    def test_survives_a_file_that_is_not_a_database(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / 'junk.db'
        db_path.write_bytes(b'not a database at all')

        assert ensure_wal_journal_mode(str(db_path)) is None

    def test_does_not_create_a_missing_database(self, tmp_path: Path) -> None:
        """Before the first migrate the file must stay absent, or
        ``start_server.sh`` takes the existing-database branch."""
        db_path = tmp_path / 'not-yet.db'

        assert ensure_wal_journal_mode(str(db_path)) is None
        assert not db_path.exists()

    @pytest.mark.parametrize('db_name', ['', ':memory:', None])
    def test_skips_databases_with_no_file(self, db_name: str | None) -> None:
        assert ensure_wal_journal_mode(db_name) is None


class TestAppReadyHook:
    """Every service that opens the database loads this app, so its
    ``ready()`` is where the one-shot conversion is anchored."""

    def test_ready_converts_the_configured_database(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / 'ready.db'
        _make_rollback_journal_db(db_path)
        config = AnthiasAppConfig.create('anthias_server.app')

        with mock.patch.dict(
            settings.DATABASES['default'],
            {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': str(db_path),
            },
        ):
            config.ready()

        assert _journal_mode(db_path) == 'wal'

    def test_ready_ignores_a_non_sqlite_backend(self) -> None:
        config = AnthiasAppConfig.create('anthias_server.app')
        target = 'anthias_server.lib.sqlite_journal.ensure_wal_journal_mode'
        with (
            mock.patch.dict(
                settings.DATABASES['default'],
                {'ENGINE': 'django.db.backends.postgresql'},
            ),
            mock.patch(target) as ensure,
        ):
            config.ready()

        ensure.assert_not_called()
