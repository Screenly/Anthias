"""Shared GraphQL helpers for import providers.

The GraphQL-backed providers (ScreenCloud, OptiSigns) all talk the same
shape: a Bearer token, a POST of ``{query, variables}``, and a response
that returns HTTP 200 even on failure (errors surfaced in an ``errors``
array). This centralises that boilerplate so each provider owns only its
endpoint, queries, and field mapping.
"""

from __future__ import annotations

from typing import Any

import requests

from .base import ProviderImportError


def bearer_headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }


def post(
    session: requests.Session,
    endpoint: str,
    headers: dict[str, str],
    query: str,
    variables: dict[str, Any] | None,
    timeout: float,
) -> requests.Response:
    """Low-level GraphQL POST. Returns the raw ``requests`` response."""
    return session.post(
        endpoint,
        headers=headers,
        json={'query': query, 'variables': variables or {}},
        timeout=timeout,
    )


def data_or_raise(
    response: requests.Response, *, source: str
) -> dict[str, Any]:
    """Return ``data`` from a GraphQL response, raising on any error.

    GraphQL answers 200 even on failure, so the ``errors`` array is checked
    explicitly. HTTP-level and GraphQL errors both raise
    ``ProviderImportError`` with the first message; ``source`` names the
    provider for the fallback messages. Callers that need the transport
    error to propagate (e.g. ``validate_token``) don't use this.
    """
    response.raise_for_status()
    body = response.json()
    errors = body.get('errors')
    if errors:
        message = f'{source} query failed.'
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict) and first.get('message'):
                message = str(first['message'])
        raise ProviderImportError(message)
    data = body.get('data')
    if not isinstance(data, dict):
        raise ProviderImportError(f'Unexpected response from {source}.')
    return data
