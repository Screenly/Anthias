"""Regression guard for the Sentry test-mode gate in settings.py.

The unit suite must run with no external network dependencies
(conftest.py force-mocks Redis for the same reason), and exceptions
raised on purpose by failing tests must never reach the production
Sentry project. settings.py defaults the DSN to '' whenever
ENVIRONMENT=test or pytest is detected on argv — this test pins that
behaviour: under pytest the client has no DSN, builds no transport,
and capture calls are dropped.
"""

from pathlib import Path

import sentry_sdk


def test_sentry_does_not_send_under_pytest() -> None:
    client = sentry_sdk.get_client()
    assert not client.dsn
    assert client.transport is None
    # capture_message returns the event id when an event is queued
    # for sending; None means the event was dropped client-side.
    assert sentry_sdk.capture_message('must not be sent') is None


class TestGetBoardModel:
    """Board-model detection feeding the fleet-triage Sentry tags."""

    def test_reads_and_strips_nul_terminated_model(
        self, tmp_path: Path
    ) -> None:
        from anthias_server.django_project.settings import get_board_model

        model_file = tmp_path / 'model'
        model_file.write_bytes(b'Raspberry Pi 3 Model B Rev 1.2\x00')
        assert (
            get_board_model(str(model_file))
            == 'Raspberry Pi 3 Model B Rev 1.2'
        )

    def test_returns_empty_when_no_device_tree(self, tmp_path: Path) -> None:
        # x86 hosts have no /proc/device-tree at all.
        from anthias_server.django_project.settings import get_board_model

        assert get_board_model(str(tmp_path / 'missing')) == ''
