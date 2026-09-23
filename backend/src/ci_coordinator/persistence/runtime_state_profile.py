"""Exact bounded profile for durable ingress and issuance state rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import cast

PROFILE_RESOURCE_NAME = "runtime-state-profile.v1.json"
PROFILE_SHA256 = "5f1fd476df1b196e80725921a5dae2ea44c70ef1de8cc13b4b985fa22d9ba7b7"


@dataclass(frozen=True, slots=True)
class RuntimeIngressIssuanceStateProfile:
    delivery_id_utf8_bytes: int
    issuance_idempotency_key_utf8_bytes: int
    issued_plan_record_id_utf8_bytes: int
    production_admission_authority_id_utf8_bytes: int
    production_admission_envelope_canonical_bytes: int
    production_admission_key_id_utf8_bytes: int
    production_admission_public_key_der_bytes: int
    signed_envelope_canonical_bytes: int

    def __post_init__(self) -> None:
        values = (
            self.delivery_id_utf8_bytes,
            self.issuance_idempotency_key_utf8_bytes,
            self.issued_plan_record_id_utf8_bytes,
            self.production_admission_authority_id_utf8_bytes,
            self.production_admission_envelope_canonical_bytes,
            self.production_admission_key_id_utf8_bytes,
            self.production_admission_public_key_der_bytes,
            self.signed_envelope_canonical_bytes,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("runtime state profile limits must be positive integers")
        if self.issued_plan_record_id_utf8_bytes < 44:
            raise ValueError("runtime state profile record id bound is too small")
        if self.production_admission_authority_id_utf8_bytes < 53:
            raise ValueError("runtime state profile authority id bound is too small")


def load_bundled_runtime_state_profile() -> RuntimeIngressIssuanceStateProfile:
    profile_bytes = (
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    if sha256(profile_bytes).hexdigest() != PROFILE_SHA256:
        raise ValueError("bundled runtime state profile digest does not match admission")
    return parse_runtime_state_profile(profile_bytes)


def parse_runtime_state_profile(profile_bytes: bytes) -> RuntimeIngressIssuanceStateProfile:
    try:
        raw = json.loads(profile_bytes)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("runtime state profile must be valid JSON") from error
    if type(raw) is not dict or any(type(key) is not str for key in raw):
        raise ValueError("runtime state profile must be an object")
    profile = cast(dict[str, object], raw)
    if set(profile) != {"schemaVersion", "profileId", "ownerId", "limits", "nonClaims"}:
        raise ValueError("runtime state profile has unexpected or missing fields")
    if profile["schemaVersion"] != "ci-runtime-state-profile/v1":
        raise ValueError("runtime state profile schema version is unsupported")
    if profile["profileId"] != "ci-runtime-ingress-issuance-state/v1":
        raise ValueError("runtime state profile id is unsupported")
    if profile["ownerId"] != "ci-coordinator.runtime":
        raise ValueError("runtime state profile owner is unsupported")
    limits = profile["limits"]
    if type(limits) is not dict or any(type(key) is not str for key in limits):
        raise ValueError("runtime state profile limits must be an object")
    limits = cast(dict[str, object], limits)
    expected_limits = {
        "deliveryIdUtf8Bytes",
        "issuanceIdempotencyKeyUtf8Bytes",
        "issuedPlanRecordIdUtf8Bytes",
        "productionAdmissionAuthorityIdUtf8Bytes",
        "productionAdmissionEnvelopeCanonicalBytes",
        "productionAdmissionKeyIdUtf8Bytes",
        "productionAdmissionPublicKeyDerBytes",
        "signedEnvelopeCanonicalBytes",
    }
    if set(limits) != expected_limits:
        raise ValueError("runtime state profile limits have unexpected or missing fields")
    if type(profile["nonClaims"]) is not list or not all(
        type(value) is str and value for value in cast(list[object], profile["nonClaims"])
    ):
        raise ValueError("runtime state profile non-claims must be non-empty strings")
    return RuntimeIngressIssuanceStateProfile(
        delivery_id_utf8_bytes=_positive(limits["deliveryIdUtf8Bytes"]),
        issuance_idempotency_key_utf8_bytes=_positive(limits["issuanceIdempotencyKeyUtf8Bytes"]),
        issued_plan_record_id_utf8_bytes=_positive(limits["issuedPlanRecordIdUtf8Bytes"]),
        production_admission_authority_id_utf8_bytes=_positive(
            limits["productionAdmissionAuthorityIdUtf8Bytes"]
        ),
        production_admission_envelope_canonical_bytes=_positive(
            limits["productionAdmissionEnvelopeCanonicalBytes"]
        ),
        production_admission_key_id_utf8_bytes=_positive(
            limits["productionAdmissionKeyIdUtf8Bytes"]
        ),
        production_admission_public_key_der_bytes=_positive(
            limits["productionAdmissionPublicKeyDerBytes"]
        ),
        signed_envelope_canonical_bytes=_positive(limits["signedEnvelopeCanonicalBytes"]),
    )


def _positive(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("runtime state profile limits must be positive integers")
    return value
