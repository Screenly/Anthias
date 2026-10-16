"""Tests for the shared migrate/dbrestore startup gate.

Covers the probe and the polling loop that keep celery (Sentry
ANTHIAS-1) and the viewer (Sentry ANTHIAS-C) off a half-written
schema.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from anthias_common import migration_wait


def _patch_executor() -> tuple[mock.MagicMock, Any]:
    fake_conn = mock.MagicMock()
    connections_patch = mock.patch(
        'django.db.connections', {'default': fake_conn}
    )
    executor_patch = mock.patch(
        'django.db.migrations.executor.MigrationExecutor'
    )
    return fake_conn, (connections_patch, executor_patch)


class TestMigrationsReady:
    def test_ready_when_plan_is_empty(self) -> None:
        fake_conn, (connections_patch, executor_patch) = _patch_executor()
        with connections_patch, executor_patch as executor_cls:
            executor_cls.return_value.migration_plan.return_value = []
            assert migration_wait.migrations_ready() is True
        # The probe connection must not leak into the prefork children.
        fake_conn.close.assert_called_once()

    def test_not_ready_with_unapplied_migrations(self) -> None:
        fake_conn, (connections_patch, executor_patch) = _patch_executor()
        with connections_patch, executor_patch as executor_cls:
            executor_cls.return_value.migration_plan.return_value = [
                (mock.sentinel.migration, False)
            ]
            assert migration_wait.migrations_ready() is False
        fake_conn.close.assert_called_once()

    def test_not_ready_when_db_is_locked(self) -> None:
        # The window that produced ANTHIAS-1 and ANTHIAS-C: the
        # server's dbbackup/dbrestore holds the SQLite file (or the
        # django_migrations table is mid-recreate) and the probe
        # raises instead of returning a plan.
        from django.db.utils import OperationalError

        fake_conn, (connections_patch, executor_patch) = _patch_executor()
        with connections_patch, executor_patch as executor_cls:
            executor_cls.side_effect = OperationalError('database is locked')
            assert migration_wait.migrations_ready() is False
        fake_conn.close.assert_called_once()

    def test_non_database_errors_fail_fast(self) -> None:
        # A programming bug in the probe must not be mistaken for
        # "database not ready" — that would park the caller in an
        # infinite startup wait with no failing signal.
        fake_conn, (connections_patch, executor_patch) = _patch_executor()
        with connections_patch, executor_patch as executor_cls:
            executor_cls.side_effect = TypeError('probe bug')
            with pytest.raises(TypeError, match='probe bug'):
                migration_wait.migrations_ready()
        fake_conn.close.assert_called_once()


class TestWaitForMigrations:
    def test_returns_immediately_when_ready(self) -> None:
        with (
            mock.patch.object(
                migration_wait, 'migrations_ready', return_value=True
            ),
            mock.patch('anthias_common.migration_wait.time.sleep') as sleep,
        ):
            migration_wait.wait_for_migrations('viewer')
        sleep.assert_not_called()

    def test_blocks_until_ready(self) -> None:
        with (
            mock.patch.object(
                migration_wait,
                'migrations_ready',
                side_effect=[False, False, True],
            ),
            mock.patch('anthias_common.migration_wait.time.sleep') as sleep,
        ):
            migration_wait.wait_for_migrations('viewer')
        assert sleep.call_count == 2

    def test_names_the_waiting_service_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            mock.patch.object(
                migration_wait,
                'migrations_ready',
                side_effect=[False, True],
            ),
            mock.patch('anthias_common.migration_wait.time.sleep'),
            caplog.at_level('WARNING', logger=migration_wait.__name__),
        ):
            migration_wait.wait_for_migrations('viewer')

        assert 'delaying viewer startup' in caplog.text
