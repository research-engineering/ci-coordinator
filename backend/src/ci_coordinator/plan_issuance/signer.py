from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from ci_coordinator.kernel import Clock, canonical_json
from ci_coordinator.plan_issuance.model import (
    SIGNED_PLAN_ALGORITHM,
    SIGNED_PLAN_ENVELOPE_SCHEMA_VERSION,
    SignedPlanEnvelope,
    SignedPlanPayload,
)


class SignedPlanSigner:
    def __init__(
        self,
        *,
        key_id: str,
        private_key_pem: bytes,
        ttl_seconds: int,
        clock: Clock,
    ) -> None:
        if not key_id or type(ttl_seconds) is not int or ttl_seconds < 1:
            raise ValueError("signer requires a key id and positive TTL")
        key = load_pem_private_key(private_key_pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("signed plan key must be Ed25519")
        self._key_id = key_id
        self._key = key
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def sign(
        self,
        payload: SignedPlanPayload,
        *,
        not_after: datetime | None = None,
    ) -> SignedPlanEnvelope:
        issued_at = self._clock.now()
        expires_at = issued_at + timedelta(seconds=self._ttl_seconds)
        if not_after is not None:
            if (
                type(not_after) is not datetime
                or not_after.tzinfo is None
                or not_after.utcoffset() is None
                or issued_at >= not_after
            ):
                raise ValueError("signed plan authority has expired")
            expires_at = min(expires_at, not_after)
        unsigned = SignedPlanEnvelope(
            schema_version=SIGNED_PLAN_ENVELOPE_SCHEMA_VERSION,
            key_id=self._key_id,
            algorithm=SIGNED_PLAN_ALGORITHM,
            issued_at=issued_at,
            expires_at=expires_at,
            payload=payload,
            signature="",
        )
        signature = _encode(self._key.sign(canonical_json(unsigned.unsigned_mapping())))
        return replace(unsigned, signature=signature)

    def public_key_pem(self) -> bytes:
        return self._key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def verify_signed_plan(
    envelope: SignedPlanEnvelope,
    *,
    public_key_pem: bytes,
    clock: Clock,
) -> str | None:
    if envelope.schema_version != SIGNED_PLAN_ENVELOPE_SCHEMA_VERSION:
        return "signed_plan_schema_unsupported"
    if envelope.algorithm != SIGNED_PLAN_ALGORITHM:
        return "signed_plan_algorithm_unsupported"
    if envelope.expires_at <= clock.now():
        return "signed_plan_expired"
    try:
        key = load_pem_public_key(public_key_pem)
        if not isinstance(key, Ed25519PublicKey):
            return "signed_plan_public_key_invalid"
        key.verify(_decode(envelope.signature), canonical_json(envelope.unsigned_mapping()))
    except (InvalidSignature, ValueError, TypeError):
        return "signed_plan_signature_invalid"
    return None


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
