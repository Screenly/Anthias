"""Shared response fakes for import-provider tests.

Used by the provider test modules (GraphQL: ScreenCloud/OptiSigns; REST:
Yodeck/piSignage/Xibo) so the canned-response boilerplate lives in one
place. Not a test module (no ``test_`` prefix), so pytest doesn't collect
it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import requests


def json_response(status: int, json_body: Any = None) -> MagicMock:
    """A fake ``requests.Response`` for a plain-JSON REST call."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.ok = 200 <= status < 400
    resp.json.return_value = json_body
    if not resp.ok:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f'HTTP {status}', response=resp
        )
    return resp


def gql_response(
    status: int, data: Any = None, errors: Any = None
) -> MagicMock:
    """A fake ``requests.Response`` for a GraphQL POST."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.ok = 200 <= status < 400
    body: dict[str, Any] = {}
    if data is not None:
        body['data'] = data
    if errors is not None:
        body['errors'] = errors
    resp.json.return_value = body
    if not resp.ok:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f'HTTP {status}', response=resp
        )
    return resp


def stream_response(status: int, chunks: list[bytes]) -> MagicMock:
    """A fake streaming response usable as a context manager."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.ok = 200 <= status < 400
    resp.iter_content.return_value = iter(chunks)
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp
