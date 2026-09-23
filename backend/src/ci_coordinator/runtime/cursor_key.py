from typing import Literal

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def derive_runtime_cursor_key(
    private_key_pem: str, *, purpose: Literal["history", "activity"]
) -> bytes:
    if purpose not in ("history", "activity"):
        raise ValueError("cursor purpose is not admitted")
    private = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise ValueError("cursor derivation requires the admitted Ed25519 signing key")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ci-coordinator/runtime-subkeys/v1",
        info=b"ci-coordinator/" + purpose.encode("ascii") + b"-cursor/v1",
    ).derive(private.private_bytes_raw())
