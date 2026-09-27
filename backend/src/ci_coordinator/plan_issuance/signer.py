from __future__ import annotations

import base64
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from ci_coordinator.kernel import Clock, canonical_json, verify_canonical_ed25519_signature
from ci_coordinator.plan_issuance.model import (
    SIGNED_PLAN_ALGORITHM,
    SIGNED_PLAN_ENVELOPE_SCHEMA_VERSION,
    SignedPlanEnvelope,
    SignedPlanPayload,
)

MAX_SIGNED_PLAN_TTL_SECONDS = 300
MAX_SIGNED_PLAN_CLOCK_SKEW_SECONDS = 300


class SignedPlanSigner:
    def __init__(
        self,
        *,
        key_id: str,
        private_key_pem: bytes,
        ttl_seconds: int,
        clock: Clock,
    ) -> None:
        if (
            not key_id
            or type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= MAX_SIGNED_PLAN_TTL_SECONDS
        ):
            raise ValueError("signer requires a key id and target-compatible TTL")
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
    expected_key_id: str,
    clock: Clock,
) -> str | None:
    """Admit a typed envelope against an independent key identity and clock.

    Repository/run/execution binding remains a separate target gate. The original
    Python lifetime predicate remains stricter than target time projection.
    """
    if envelope.schema_version != SIGNED_PLAN_ENVELOPE_SCHEMA_VERSION:
        return "signed_plan_schema_unsupported"
    if envelope.algorithm != SIGNED_PLAN_ALGORITHM:
        return "signed_plan_algorithm_unsupported"
    if (
        type(expected_key_id) is not str
        or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", expected_key_id) is None
        or envelope.key_id != expected_key_id
    ):
        return "signed_plan_key_id_mismatch"
    if type(envelope.signature) is not str or len(envelope.signature) != 86:
        return "signed_plan_signature_invalid"
    now = clock.now()
    if any(
        not isinstance(value, datetime) for value in (envelope.issued_at, envelope.expires_at, now)
    ):
        return "signed_plan_time_invalid"
    try:
        unsigned = envelope.unsigned_mapping()
        issued_ms = _target_milliseconds(unsigned["issuedAt"])
        expires_ms = _target_milliseconds(unsigned["expiresAt"])
        now_ms = _target_milliseconds(now.isoformat())
    except (OverflowError, TypeError, ValueError):
        return "signed_plan_time_invalid"
    if issued_ms > now_ms + MAX_SIGNED_PLAN_CLOCK_SKEW_SECONDS * 1_000:
        return "signed_plan_issued_in_future"
    if expires_ms <= now_ms or envelope.expires_at <= now:
        return "signed_plan_expired"
    if (
        expires_ms <= issued_ms
        or expires_ms - issued_ms > MAX_SIGNED_PLAN_TTL_SECONDS * 1_000
        or envelope.expires_at <= envelope.issued_at
        or envelope.expires_at - envelope.issued_at > timedelta(seconds=MAX_SIGNED_PLAN_TTL_SECONDS)
    ):
        return "signed_plan_ttl_invalid"
    try:
        key = load_pem_public_key(public_key_pem)
        if not isinstance(key, Ed25519PublicKey):
            return "signed_plan_public_key_invalid"
        valid = verify_canonical_ed25519_signature(
            key, signature=envelope.signature, payload=canonical_json(unsigned)
        )
    except (ValueError, TypeError):
        return "signed_plan_signature_invalid"
    return None if valid else "signed_plan_signature_invalid"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _target_milliseconds(value: object) -> int:
    pattern = (
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{6})?[+-][0-9]{2}:[0-9]{2}"
    )
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise ValueError("signed plan time must retain a target-compatible numeric offset")
    instant = datetime.fromisoformat(value).astimezone(UTC)
    return (instant - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)
