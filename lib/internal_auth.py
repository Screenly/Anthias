#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import hmac
from os import getenv
from typing import Any, Mapping

INTERNAL_AUTH_HEADER = 'X-Anthias-Internal-Token'
INTERNAL_AUTH_ENV = 'ANTHIAS_INTERNAL_TOKEN'
_INTERNAL_AUTH_CONTEXT = b'anthias-internal-api-v1'


def _internal_auth_secret(settings: Mapping[str, Any]) -> str:
    return str(
        getenv(INTERNAL_AUTH_ENV) or settings.get('django_secret_key') or ''
    )


def internal_auth_token(settings: Mapping[str, Any]) -> str:
    """Derive the internal API token shared by server and viewer."""
    secret = _internal_auth_secret(settings)
    if not secret:
        return ''
    return hmac.new(
        secret.encode('utf-8'),
        _INTERNAL_AUTH_CONTEXT,
        hashlib.sha256,
    ).hexdigest()


def is_internal_request(request: Any, settings: Mapping[str, Any]) -> bool:
    expected = internal_auth_token(settings)
    supplied = request.headers.get(INTERNAL_AUTH_HEADER, '')
    return bool(expected) and hmac.compare_digest(supplied, expected)
