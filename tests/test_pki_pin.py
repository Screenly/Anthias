"""Key pins (lib.pki.spki_sha256): what both sides compare on top of
the CA."""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives import serialization

from anthias_server.lib import pki


def test_the_pin_is_the_hash_of_the_public_key_info() -> None:
    key = pki.new_key()
    der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    expected = hashlib.sha256(der).hexdigest()
    ca_key = pki.new_key()
    ca = pki.make_ca(ca_key, 'Test CA')
    cert = pki.issue_client_cert(
        ca, ca_key, key.public_key(), 'screen', pki.ROLE_SCREEN
    )

    # Same pin for the key, its private half, its PEM and any
    # certificate for it.
    assert pki.spki_sha256(key) == expected
    assert pki.spki_sha256(key.public_key()) == expected
    assert pki.spki_sha256(pki.public_key_pem(key)) == expected
    assert pki.spki_sha256(cert) == expected


def test_a_renewed_certificate_keeps_the_pin_another_key_does_not() -> None:
    ca_key = pki.new_key()
    ca = pki.make_ca(ca_key, 'Test CA')
    key = pki.new_key()
    first, renewed = (
        pki.issue_client_cert(
            ca, ca_key, key.public_key(), 'c', pki.ROLE_CONTROLLER
        )
        for _ in range(2)
    )
    forged = pki.issue_client_cert(
        ca, ca_key, pki.new_key().public_key(), 'c', pki.ROLE_CONTROLLER
    )
    pin = pki.spki_sha256(first)

    assert pki.fingerprint(first) != pki.fingerprint(renewed)
    assert pki.spki_matches(renewed, pin)
    assert pki.spki_matches(renewed, pin.upper())
    # Same CA, same role, valid: still refused, it is another key.
    assert not pki.spki_matches(forged, pin)


def test_no_pin_never_matches() -> None:
    key = pki.new_key()
    assert not pki.spki_matches(key, '')
    assert not pki.spki_matches(object(), pki.spki_sha256(key))
