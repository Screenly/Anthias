"""The supervisor filters journal identifiers, not CONTAINER_ID_FULL."""

import json
from typing import Any
from unittest import mock

import pytest

from anthias_server.lib import system_logs


@pytest.mark.parametrize('selector', ['anthias-viewer', 'f' * 64, 'f' * 12])
def test_balena_filter_reaches_records_with_the_default_journal_tag(
    selector: str,
) -> None:
    full_id = 'f' * 64
    record = {
        'MESSAGE': 'viewer log',
        'CONTAINER_ID_FULL': full_id,
        'SYSLOG_IDENTIFIER': full_id[:12],
    }

    def supervisor(**kwargs: Any) -> mock.Mock:
        response = mock.Mock(ok=True)
        if kwargs['action'] == 'containerId':
            response.json.return_value = {'containerId': full_id}
        else:
            # The supervisor passes containerId to journalctl -t, which
            # selects SYSLOG_IDENTIFIER, not the full-ID metadata field.
            matches = (
                kwargs['json']['containerId'] == record['SYSLOG_IDENTIFIER']
            )
            payload = json.dumps(record) if matches else ''
            response.iter_content.return_value = [payload.encode()]
        return response

    with (
        mock.patch.object(system_logs, 'is_balena_app', return_value=True),
        mock.patch.object(
            system_logs,
            'get_balena_supervisor_api_response',
            side_effect=supervisor,
        ),
    ):
        snapshot = system_logs.get_journal_snapshot(container=selector)

    assert [entry['message'] for entry in snapshot.entries] == ['viewer log']
