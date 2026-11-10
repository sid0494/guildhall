"""Interaction signatures, checked with a keypair made for the test."""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from guildhall.api.signature import SignatureVerifier

PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_KEY_HEX = (
    PRIVATE_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
)
TIMESTAMP = "1762790400"
BODY = b'{"type":1,"id":"1"}'


def sign(timestamp: str, body: bytes) -> str:
    return PRIVATE_KEY.sign(timestamp.encode() + body).hex()


@pytest.fixture
def verifier() -> SignatureVerifier:
    return SignatureVerifier(PUBLIC_KEY_HEX)


def test_a_genuine_signature_is_valid(verifier: SignatureVerifier) -> None:
    assert verifier.is_valid(sign(TIMESTAMP, BODY), TIMESTAMP, BODY)


@pytest.mark.parametrize(
    ("timestamp", "body"),
    [
        (TIMESTAMP, BODY + b" "),  # one extra byte, as re-encoding might add
        (TIMESTAMP, b'{"id":"1","type":1}'),  # same JSON, keys reordered
        ("1762790401", BODY),  # another timestamp
    ],
    ids=["trailing-space", "reordered-json", "other-timestamp"],
)
def test_a_signature_covers_the_exact_timestamp_and_bytes(
    verifier: SignatureVerifier, timestamp: str, body: bytes
) -> None:
    assert not verifier.is_valid(sign(TIMESTAMP, BODY), timestamp, body)


def test_a_signature_from_another_key_is_invalid(verifier: SignatureVerifier) -> None:
    forged = Ed25519PrivateKey.generate().sign(TIMESTAMP.encode() + BODY).hex()
    assert not verifier.is_valid(forged, TIMESTAMP, BODY)


@pytest.mark.parametrize(
    ("signature", "timestamp"),
    [
        (None, TIMESTAMP),
        ("aa" * 64, None),
        ("not-hex", TIMESTAMP),
        ("abc", TIMESTAMP),  # odd length
        ("aa" * 10, TIMESTAMP),  # wrong length
        ("", TIMESTAMP),
    ],
    ids=["no-signature", "no-timestamp", "not-hex", "odd-hex", "short", "empty"],
)
def test_missing_or_malformed_headers_are_invalid_not_errors(
    verifier: SignatureVerifier, signature: str | None, timestamp: str | None
) -> None:
    assert not verifier.is_valid(signature, timestamp, BODY)


@pytest.mark.parametrize("key", ["not-hex", "aa" * 31, "aa" * 33, ""])
def test_a_bad_public_key_fails_at_construction(key: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - from bytes.fromhex or cryptography
        SignatureVerifier(key)
