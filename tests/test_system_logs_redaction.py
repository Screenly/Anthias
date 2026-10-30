"""Structured log messages must not bypass secret redaction."""

import ast
import json

import pytest

from anthias_common import journal


@pytest.mark.parametrize(
    'key', ['token', 'Authorization', 'psk', 'wpa_passphrase', 'api_key']
)
@pytest.mark.parametrize(
    'secret',
    [
        'example-secret',
        'Bearer example-secret',
        'Basic example-secret',
        'prefix "example-secret" tail',
        'prefix \\ "example-secret" tail',
    ],
)
def test_json_keys_and_escaped_values_are_redacted(
    key: str, secret: str
) -> None:
    message = json.dumps({key: secret, 'safe': 'ordinary message'})
    payload = json.dumps({'MESSAGE': message})

    entries = journal.normalize_payload(payload)
    redacted = entries[0]['message']

    assert 'example-secret' not in redacted
    assert json.loads(redacted) == {
        key: journal.REDACTED,
        'safe': 'ordinary message',
    }


@pytest.mark.parametrize('key', ['psk', 'Authorization'])
def test_single_quoted_keys_and_escaped_values_are_redacted(key: str) -> None:
    message = repr({key: "prefix 'example-secret' tail", 'safe': 'ordinary'})

    redacted = journal.redact(message)

    assert 'example-secret' not in redacted
    assert ast.literal_eval(redacted) == {
        key: journal.REDACTED,
        'safe': 'ordinary',
    }
