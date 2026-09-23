"""Exact bounded profile for shadow evidence and reconciliation state rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import cast

PROFILE_RESOURCE_NAME = "shadow-reconciliation-state-profile.v1.json"
PROFILE_SHA256 = "3de49ceb0e7fe46db1bd61dc42cca2c4843c07ba83167acf8d1908563bcaa8ca"


@dataclass(frozen=True, slots=True)
class ShadowReconciliationStateProfile:
    profile_id_utf8_bytes: int
    shadow_repository_utf8_bytes: int
    shadow_event_utf8_bytes: int
    shadow_surface_utf8_bytes: int
    shadow_record_canonical_bytes: int
    reconciliation_subject_canonical_bytes: int
    reconciliation_contract_canonical_bytes: int
    reconciliation_claim_token_utf8_bytes: int
    reconciliation_max_attempts: int
    reconciliation_max_backoff_seconds: int
    reconciliation_max_deadline_seconds: int
    reconciliation_max_lease_seconds: int
    reconciliation_observation_id_utf8_bytes: int
    reconciliation_observation_canonical_bytes: int
    reconciliation_result_canonical_bytes: int

    def __post_init__(self) -> None:
        values = (
            self.profile_id_utf8_bytes,
            self.shadow_repository_utf8_bytes,
            self.shadow_event_utf8_bytes,
            self.shadow_surface_utf8_bytes,
            self.shadow_record_canonical_bytes,
            self.reconciliation_subject_canonical_bytes,
            self.reconciliation_contract_canonical_bytes,
            self.reconciliation_claim_token_utf8_bytes,
            self.reconciliation_max_attempts,
            self.reconciliation_max_backoff_seconds,
            self.reconciliation_max_deadline_seconds,
            self.reconciliation_max_lease_seconds,
            self.reconciliation_observation_id_utf8_bytes,
            self.reconciliation_observation_canonical_bytes,
            self.reconciliation_result_canonical_bytes,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("shadow reconciliation state profile limits must be positive integers")
        if self.reconciliation_subject_canonical_bytes < 512:
            raise ValueError("reconciliation subject canonical bound is too small")
        if self.reconciliation_observation_id_utf8_bytes < 1:
            raise ValueError("reconciliation observation id bound is too small")


def load_bundled_shadow_reconciliation_state_profile() -> ShadowReconciliationStateProfile:
    profile_bytes = (
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    if sha256(profile_bytes).hexdigest() != PROFILE_SHA256:
        raise ValueError(
            "bundled shadow reconciliation state profile digest does not match admission"
        )
    return parse_shadow_reconciliation_state_profile(profile_bytes)


def parse_shadow_reconciliation_state_profile(
    profile_bytes: bytes,
) -> ShadowReconciliationStateProfile:
    try:
        raw = json.loads(profile_bytes)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("shadow reconciliation state profile must be valid JSON") from error
    if type(raw) is not dict or any(type(key) is not str for key in raw):
        raise ValueError("shadow reconciliation state profile must be an object")
    profile = cast(dict[str, object], raw)
    if set(profile) != {"schemaVersion", "profileId", "ownerId", "limits", "nonClaims"}:
        raise ValueError("shadow reconciliation state profile has unexpected or missing fields")
    if profile["schemaVersion"] != "ci-shadow-reconciliation-state-profile/v1":
        raise ValueError("shadow reconciliation state profile schema version is unsupported")
    if profile["profileId"] != "ci-shadow-reconciliation-state/v1":
        raise ValueError("shadow reconciliation state profile id is unsupported")
    if profile["ownerId"] != "ci-coordinator.runtime":
        raise ValueError("shadow reconciliation state profile owner is unsupported")
    limits = profile["limits"]
    if type(limits) is not dict or any(type(key) is not str for key in limits):
        raise ValueError("shadow reconciliation state profile limits must be an object")
    limits = cast(dict[str, object], limits)
    expected_limits = {
        "profileIdUtf8Bytes",
        "shadowRepositoryUtf8Bytes",
        "shadowEventUtf8Bytes",
        "shadowSurfaceUtf8Bytes",
        "shadowRecordCanonicalBytes",
        "reconciliationSubjectCanonicalBytes",
        "reconciliationContractCanonicalBytes",
        "reconciliationClaimTokenUtf8Bytes",
        "reconciliationMaxAttempts",
        "reconciliationMaxBackoffSeconds",
        "reconciliationMaxDeadlineSeconds",
        "reconciliationMaxLeaseSeconds",
        "reconciliationObservationIdUtf8Bytes",
        "reconciliationObservationCanonicalBytes",
        "reconciliationResultCanonicalBytes",
    }
    if set(limits) != expected_limits:
        raise ValueError(
            "shadow reconciliation state profile limits have unexpected or missing fields"
        )
    if type(profile["nonClaims"]) is not list or not all(
        type(value) is str and value for value in cast(list[object], profile["nonClaims"])
    ):
        raise ValueError("shadow reconciliation state profile non-claims must be non-empty strings")
    return ShadowReconciliationStateProfile(
        profile_id_utf8_bytes=_positive(limits["profileIdUtf8Bytes"]),
        shadow_repository_utf8_bytes=_positive(limits["shadowRepositoryUtf8Bytes"]),
        shadow_event_utf8_bytes=_positive(limits["shadowEventUtf8Bytes"]),
        shadow_surface_utf8_bytes=_positive(limits["shadowSurfaceUtf8Bytes"]),
        shadow_record_canonical_bytes=_positive(limits["shadowRecordCanonicalBytes"]),
        reconciliation_subject_canonical_bytes=_positive(
            limits["reconciliationSubjectCanonicalBytes"]
        ),
        reconciliation_contract_canonical_bytes=_positive(
            limits["reconciliationContractCanonicalBytes"]
        ),
        reconciliation_claim_token_utf8_bytes=_positive(
            limits["reconciliationClaimTokenUtf8Bytes"]
        ),
        reconciliation_max_attempts=_positive(limits["reconciliationMaxAttempts"]),
        reconciliation_max_backoff_seconds=_positive(limits["reconciliationMaxBackoffSeconds"]),
        reconciliation_max_deadline_seconds=_positive(limits["reconciliationMaxDeadlineSeconds"]),
        reconciliation_max_lease_seconds=_positive(limits["reconciliationMaxLeaseSeconds"]),
        reconciliation_observation_id_utf8_bytes=_positive(
            limits["reconciliationObservationIdUtf8Bytes"]
        ),
        reconciliation_observation_canonical_bytes=_positive(
            limits["reconciliationObservationCanonicalBytes"]
        ),
        reconciliation_result_canonical_bytes=_positive(
            limits["reconciliationResultCanonicalBytes"]
        ),
    )


def _positive(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("shadow reconciliation state profile limits must be positive integers")
    return value
