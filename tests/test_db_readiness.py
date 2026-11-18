"""The startup gate the celery worker and the viewer share.

Both containers open the server-owned SQLite database and both can
reach it before the server's ``migrate`` pass has created a single
table. Querying through that window is what filed Sentry ANTHIAS-1
(celery, one burst per device per upgrade) and ANTHIAS-C (the viewer,
which re-raises and so crash-loops the container instead).
"""

import logging
from typing import Any
from unittest import mock

import pytest

from anthias_server.lib import db_readiness


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
            assert db_readiness.migrations_ready() is True
        # The probe connection must not leak into the prefork children.
        fake_conn.close.assert_called_once()

    def test_not_ready_with_unapplied_migrations(self) -> None:
        fake_conn, (connections_patch, executor_patch) = _patch_executor()
        with connections_patch, executor_patch as executor_cls:
            executor_cls.return_value.migration_plan.return_value = [
                (mock.sentinel.migration, False)
            ]
            assert db_readiness.migrations_ready() is False
        fake_conn.close.assert_called_once()

    def test_not_ready_when_db_is_locked(self) -> None:
        # The window that produced ANTHIAS-1: the server's
        # dbbackup/dbrestore holds the SQLite file (or the
        # django_migrations table is mid-recreate) and the probe
        # raises instead of returning a plan.
        from django.db.utils import OperationalError

        fake_conn, (connections_patch, executor_patch) = _patch_executor()
        with connections_patch, executor_patch as executor_cls:
            executor_cls.side_effect = OperationalError('database is locked')
            assert db_readiness.migrations_ready() is False
        fake_conn.close.assert_called_once()

    def test_non_database_errors_fail_fast(self) -> None:
        # A programming bug in the probe must not be mistaken for
        # "database not ready" — that would park the caller in an
        # infinite startup wait with no failing signal.
        fake_conn, (connections_patch, executor_patch) = _patch_executor()
        with connections_patch, executor_patch as executor_cls:
            executor_cls.side_effect = TypeError('probe bug')
            with pytest.raises(TypeError, match='probe bug'):
                db_readiness.migrations_ready()
        fake_conn.close.assert_called_once()


class TestWaitForMigrations:
    def test_returns_immediately_when_ready(self) -> None:
        with (
            mock.patch.object(
                db_readiness, 'migrations_ready', return_value=True
            ),
            mock.patch('anthias_server.lib.db_readiness.time.sleep') as sleep,
        ):
            db_readiness.wait_for_migrations(
                description='a test', log=logging.getLogger(__name__)
            )
        sleep.assert_not_called()

    def test_blocks_until_ready(self) -> None:
        with (
            mock.patch.object(
                db_readiness,
                'migrations_ready',
                side_effect=[False, False, True],
            ),
            mock.patch('anthias_server.lib.db_readiness.time.sleep') as sleep,
        ):
            db_readiness.wait_for_migrations(
                description='a test', log=logging.getLogger(__name__)
            )
        assert sleep.call_count == 2

    def test_on_poll_runs_once_per_wait(self) -> None:
        """Callers that must stay alive while they wait get a hook.

        The viewer's watchdog file is the case this exists for: a gate
        that blocks silently trades a crash loop for a watchdog reboot
        loop.
        """
        poke = mock.Mock()
        with (
            mock.patch.object(
                db_readiness,
                'migrations_ready',
                side_effect=[False, False, True],
            ),
            mock.patch('anthias_server.lib.db_readiness.time.sleep'),
        ):
            db_readiness.wait_for_migrations(
                description='a test',
                log=logging.getLogger(__name__),
                on_poll=poke,
            )
        assert poke.call_count == 2

    def test_an_explicit_probe_overrides_the_default(self) -> None:
        probe = mock.Mock(return_value=True)
        with (
            mock.patch.object(
                db_readiness, 'migrations_ready'
            ) as default_probe,
            mock.patch('anthias_server.lib.db_readiness.time.sleep'),
        ):
            db_readiness.wait_for_migrations(
                description='a test',
                log=logging.getLogger(__name__),
                ready=probe,
            )
        probe.assert_called_once()
        default_probe.assert_not_called()

    def test_the_wait_is_logged_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        log = logging.getLogger('test_db_readiness.gate')
        with (
            mock.patch.object(
                db_readiness, 'migrations_ready', side_effect=[False, True]
            ),
            mock.patch('anthias_server.lib.db_readiness.time.sleep'),
            caplog.at_level(logging.WARNING, logger=log.name),
        ):
            db_readiness.wait_for_migrations(description='a test', log=log)
        assert any(
            'not migrated yet' in r.getMessage() and 'a test' in r.getMessage()
            for r in caplog.records
        )
