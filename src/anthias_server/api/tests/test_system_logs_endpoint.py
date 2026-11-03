"""Tests for ``GET /api/v2/system/logs``.

The endpoint is deliberately platform-agnostic: the same request and
the same response shape whether the device is on Balena (supervisor
``/v2/journal-logs``) or bare metal (``anthias-host-agent`` over
Redis). These tests pin the contract at the HTTP layer — parameter
validation, the response envelope, and the status code each backend
failure maps to. The backends themselves are covered in
``tests/test_system_logs.py``.
"""

from typing import Any
from unittest import mock

import pytest
import requests
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from anthias_common import journal
from anthias_server.lib import system_logs

VIEW = 'anthias_server.api.views.v2.system_logs'


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def logs_url() -> str:
    return reverse('api:system_logs_v2')


def _snapshot(**overrides: Any) -> system_logs.JournalSnapshot:
    defaults: dict[str, Any] = {
        'entries': [
            {
                'timestamp': '2026-10-26T08:00:00Z',
                'priority': 6,
                'unit': 'docker.service',
                'identifier': 'dockerd',
                'hostname': 'anthias',
                'container_name': None,
                'message': 'hello',
            }
        ],
        'source': system_logs.SOURCE_HOST_AGENT,
        'truncated': False,
        'persistent': True,
    }
    defaults.update(overrides)
    return system_logs.JournalSnapshot(**defaults)


@pytest.mark.django_db
def test_returns_a_snapshot_envelope(
    api_client: APIClient, logs_url: str
) -> None:
    with mock.patch(f'{VIEW}.get_journal_snapshot', return_value=_snapshot()):
        response = api_client.get(logs_url)

    assert response.status_code == status.HTTP_200_OK
    assert response.data['source'] == 'host_agent'
    assert response.data['count'] == 1
    assert response.data['truncated'] is False
    assert response.data['persistent'] is True
    assert response.data['max_count'] == journal.MAX_COUNT
    assert response.data['entries'][0]['message'] == 'hello'


@pytest.mark.django_db
def test_defaults_to_the_shared_line_count(
    api_client: APIClient, logs_url: str
) -> None:
    with mock.patch(
        f'{VIEW}.get_journal_snapshot', return_value=_snapshot()
    ) as snapshot:
        api_client.get(logs_url)

    assert snapshot.call_args.kwargs['count'] == journal.DEFAULT_COUNT


@pytest.mark.django_db
def test_forwards_every_documented_filter(
    api_client: APIClient, logs_url: str
) -> None:
    with mock.patch(
        f'{VIEW}.get_journal_snapshot', return_value=_snapshot()
    ) as snapshot:
        api_client.get(
            logs_url,
            {
                'count': '25',
                'unit': 'docker.service',
                'container': 'anthias-anthias-viewer-1',
                'since': '-1h',
                'until': 'now',
            },
        )

    assert snapshot.call_args.kwargs == {
        'count': 25,
        'unit': 'docker.service',
        'container': 'anthias-anthias-viewer-1',
        'since': '-1h',
        'until': 'now',
    }


@pytest.mark.django_db
def test_blank_filters_are_sent_as_unset(
    api_client: APIClient, logs_url: str
) -> None:
    # A UI that renders empty inputs submits '' rather than omitting
    # the key; that must mean "no filter", not "match the empty unit".
    with mock.patch(
        f'{VIEW}.get_journal_snapshot', return_value=_snapshot()
    ) as snapshot:
        api_client.get(logs_url, {'unit': '', 'since': ''})

    assert snapshot.call_args.kwargs['unit'] is None
    assert snapshot.call_args.kwargs['since'] is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    'params',
    [
        {'count': str(journal.MAX_COUNT + 1)},
        {'count': '0'},
        {'count': 'lots'},
        {'unit': '--follow'},
        {'unit': 'docker.service; rm -rf /'},
        {'container': '-n'},
        {'since': '$(id)'},
    ],
)
def test_rejects_bad_parameters_before_touching_a_backend(
    api_client: APIClient, logs_url: str, params: dict[str, Any]
) -> None:
    with mock.patch(f'{VIEW}.get_journal_snapshot') as snapshot:
        response = api_client.get(logs_url, params)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    snapshot.assert_not_called()


@pytest.mark.django_db
def test_has_no_follow_parameter(api_client: APIClient, logs_url: str) -> None:
    # Snapshot-only on both platforms. An unknown query parameter is
    # ignored rather than rejected, so assert on what reaches the
    # backend: no follow, ever.
    with mock.patch(
        f'{VIEW}.get_journal_snapshot', return_value=_snapshot()
    ) as snapshot:
        response = api_client.get(logs_url, {'follow': 'true'})

    assert response.status_code == status.HTTP_200_OK
    assert 'follow' not in snapshot.call_args.kwargs


@pytest.mark.django_db
def test_backend_failure_is_a_502(
    api_client: APIClient, logs_url: str
) -> None:
    # 502, not 500: an unreachable supervisor or a dead host agent is
    # an upstream condition the operator can act on.
    with mock.patch(
        f'{VIEW}.get_journal_snapshot',
        side_effect=system_logs.JournalUnavailable(
            'Timed out waiting for the host agent.'
        ),
    ):
        response = api_client.get(logs_url)

    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert 'Timed out' in response.data['message']


@pytest.mark.django_db
def test_supervisor_body_timeout_is_a_502(
    api_client: APIClient, logs_url: str
) -> None:
    upstream = mock.Mock(ok=True)
    upstream.iter_content.side_effect = requests.ConnectionError(
        'private transport detail'
    )
    with (
        mock.patch.object(system_logs, 'is_balena_app', return_value=True),
        mock.patch.object(
            system_logs,
            'get_balena_supervisor_api_response',
            return_value=upstream,
        ),
    ):
        response = api_client.get(logs_url)

    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert 'private transport detail' not in response.data['message']
    upstream.close.assert_called_once()


@pytest.mark.django_db
def test_a_volatile_journal_is_reported_not_hidden(
    api_client: APIClient, logs_url: str
) -> None:
    # An install predating the Ansible persistence task still has a
    # volatile journal. The response has to say so, otherwise a UI
    # presents current-boot-only entries as the full history.
    with mock.patch(
        f'{VIEW}.get_journal_snapshot',
        return_value=_snapshot(persistent=False),
    ):
        response = api_client.get(logs_url)

    assert response.data['persistent'] is False
