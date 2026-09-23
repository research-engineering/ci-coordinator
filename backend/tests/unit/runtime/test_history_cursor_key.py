import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from ci_coordinator.runtime.history_cursor_key import derive_history_cursor_key


def test_history_cursor_key_is_stable_across_pem_formatting_and_separate_from_signing() -> None:
    private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    derived = derive_history_cursor_key(pem)
    assert len(derived) == 32
    assert derived != private.private_bytes_raw()
    assert derived == derive_history_cursor_key(pem.replace("\n", "\r\n"))
    rotated = ed25519.Ed25519PrivateKey.from_private_bytes(bytes(reversed(range(32))))
    other = rotated.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    assert derived != derive_history_cursor_key(other)


def test_history_cursor_key_rejects_an_unrelated_key_kind() -> None:
    key = rsa.generate_private_key(65537, 2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    with pytest.raises(ValueError, match="Ed25519"):
        derive_history_cursor_key(pem)
