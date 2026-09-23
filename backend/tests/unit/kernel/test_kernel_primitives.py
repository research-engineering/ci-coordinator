from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ci_coordinator.kernel import (
    FixedClock,
    admit_ed25519_public_key,
    git_branch_name_is_admitted,
    verify_canonical_ed25519_signature,
)

_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
_PUBLIC_KEY = _SIGNING_KEY.public_key()
_PUBLIC_KEY_PEM = _PUBLIC_KEY.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def test_fixed_clock_returns_the_injected_instant() -> None:
    instant = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

    assert FixedClock(instant).now() == instant


@pytest.mark.parametrize(
    ("branch", "admitted"),
    [("master", True), ("release/2026.07", True), ("HEAD", False)],
)
def test_git_branch_admission_matches_checked_out_branch_domain(
    branch: str,
    admitted: bool,
) -> None:
    assert git_branch_name_is_admitted(branch) is admitted


@pytest.mark.parametrize(
    ("public_key_pem", "maximum_bytes"),
    (
        (b"", 16_384),
        (b"not a PEM key", 16_384),
        (_PUBLIC_KEY_PEM, len(_PUBLIC_KEY_PEM) - 1),
    ),
)
def test_ed25519_public_key_admission_rejects_invalid_or_oversized_input(
    public_key_pem: bytes,
    maximum_bytes: int,
) -> None:
    assert admit_ed25519_public_key(public_key_pem, maximum_bytes=maximum_bytes) is None


def test_ed25519_public_key_admission_returns_the_exact_algorithm() -> None:
    assert isinstance(admit_ed25519_public_key(_PUBLIC_KEY_PEM), Ed25519PublicKey)


@pytest.mark.parametrize(
    ("public_key", "signature", "payload"),
    (
        (cast(Ed25519PublicKey, object()), "", b"payload"),
        (_PUBLIC_KEY, cast(str, b"signature"), b"payload"),
        (_PUBLIC_KEY, "", cast(bytes, "payload")),
        (_PUBLIC_KEY, base64.urlsafe_b64encode(b"short").rstrip(b"=").decode("ascii"), b"payload"),
        (_PUBLIC_KEY, base64.urlsafe_b64encode(bytes(64)).rstrip(b"=").decode("ascii"), b"payload"),
    ),
)
def test_ed25519_signature_verification_rejects_invalid_inputs(
    public_key: Ed25519PublicKey,
    signature: str,
    payload: bytes,
) -> None:
    assert not verify_canonical_ed25519_signature(
        public_key,
        signature=signature,
        payload=payload,
    )


def test_ed25519_signature_verification_accepts_exact_canonical_signature() -> None:
    payload = b"payload"
    signature = base64.urlsafe_b64encode(_SIGNING_KEY.sign(payload)).rstrip(b"=").decode("ascii")

    assert verify_canonical_ed25519_signature(
        _PUBLIC_KEY,
        signature=signature,
        payload=payload,
    )
