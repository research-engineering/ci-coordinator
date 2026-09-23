from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


def admit_ed25519_public_key(
    public_key_pem: bytes,
    *,
    maximum_bytes: int = 16_384,
) -> Ed25519PublicKey | None:
    if (
        type(public_key_pem) is not bytes
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
        or not 1 <= len(public_key_pem) <= maximum_bytes
    ):
        return None
    try:
        public_key = load_pem_public_key(public_key_pem)
    except (TypeError, ValueError):
        return None
    return public_key if isinstance(public_key, Ed25519PublicKey) else None


def verify_canonical_ed25519_signature(
    public_key: Ed25519PublicKey,
    *,
    signature: str,
    payload: bytes,
) -> bool:
    if not isinstance(public_key, Ed25519PublicKey) or type(signature) is not str:
        return False
    if type(payload) is not bytes:
        return False
    try:
        decoded = base64.b64decode(signature + "==", altchars=b"-_", validate=True)
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        if len(decoded) != 64 or canonical != signature:
            return False
        public_key.verify(decoded, payload)
    except (InvalidSignature, UnicodeError, TypeError, ValueError):
        return False
    return True
