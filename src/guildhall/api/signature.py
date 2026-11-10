"""Checks that an interaction really comes from Discord (docs/adr/0059).

Discord signs the request's timestamp followed by its raw body with the
application's private key, and sends the signature and timestamp in headers.
Verification uses the application's public key, which is not a secret.
"""

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class SignatureVerifier:
    """Verifies interaction signatures against one application's public key."""

    def __init__(self, public_key_hex: str) -> None:
        """Raises ValueError if the key is not 32 bytes of hex, so a wrong
        setting fails at startup rather than on every request."""
        self._key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))

    def is_valid(
        self, signature_hex: str | None, timestamp: str | None, body: bytes
    ) -> bool:
        """Whether `signature_hex` signs `timestamp` + `body`.

        `body` must be the exact bytes received: parsing and re-encoding the
        JSON can change them. A missing or malformed header is simply invalid.
        """
        if signature_hex is None or timestamp is None:
            return False
        try:
            signature = bytes.fromhex(signature_hex)
            self._key.verify(signature, timestamp.encode() + body)
        except ValueError, InvalidSignature:
            return False
        return True
