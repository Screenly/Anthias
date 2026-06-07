"""Regression guard for the Sentry test-mode gate in settings.py.

The unit suite must run with no external network dependencies
(conftest.py force-mocks Redis for the same reason), and exceptions
raised on purpose by failing tests must never reach the production
Sentry project. settings.py defaults the DSN to '' whenever
ENVIRONMENT=test or pytest is detected on argv — this test pins that
behaviour: under pytest the client has no DSN, builds no transport,
and capture calls are dropped.
"""

import sentry_sdk


def test_sentry_does_not_send_under_pytest() -> None:
    client = sentry_sdk.get_client()
    assert not client.dsn
    assert client.transport is None
    # capture_message returns the event id when an event is queued
    # for sending; None means the event was dropped client-side.
    assert sentry_sdk.capture_message('must not be sent') is None
