"""Strict loader for the bundled CI economics profile."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import Final, cast

from ci_coordinator.ci_economics.collection import CollectionPolicy
from ci_coordinator.ci_economics.model import (
    MAX_ECONOMICS_PAGE_SIZE,
    MAX_JOB_LABEL_CODE_POINTS,
    MAX_JOB_LABELS,
    MAX_JOB_NAME_CODE_POINTS,
    MAX_JOBS_PER_ATTEMPT,
    MAX_RUNNER_TEXT_CODE_POINTS,
    MEASUREMENT_DEFINITION_VERSION,
)

CI_ECONOMICS_PROFILE_RESOURCE: Final = "ci-economics-profile.v1.json"
CI_ECONOMICS_PROFILE_SHA256: Final = (
    "ba46639981fc7ade4c015d4db53c6065977f4d49435d80526c5c69212226a287"
)
_EXPECTED_COLLECTION: Final = {
    "windowSeconds": 604_800,
    "initialBackoffSeconds": 30,
    "maximumBackoffSeconds": 3_600,
    "maximumAttempts": 20,
    "leaseSeconds": 300,
    "maximumClaimsPerRound": 10,
    "maximumConcurrentClaims": 4,
    "maximumTerminalizationsPerClaim": 10,
}
_EXPECTED_RETENTION: Final = {
    "evidenceDays": 90,
    "tombstoneGraceSeconds": 86_400,
    "cleanupBatchSize": 100,
}
_EXPECTED_MAINTENANCE: Final = {
    "collectionDeadlineSeconds": 240,
    "expiryDeadlineSeconds": 30,
    "purgeDeadlineSeconds": 30,
    "observationCleanupDeadlineSeconds": 30,
}
_EXPECTED_HTTP: Final = {
    "attempts": {
        "method": "GET",
        "path": "/api/v1/economics/repositories/{installation_id}/{repository_id}/attempts",
        "operationId": "list_repository_ci_economics_attempts",
        "requiredRole": "audit",
        "cursorParameter": "afterCursor",
        "defaultPageSize": 50,
        "maximumPageSize": 100,
    },
    "attemptJobs": {
        "method": "GET",
        "path": (
            "/api/v1/economics/repositories/{installation_id}/{repository_id}/attempts/"
            "{workflow_run_id}/{run_attempt}/jobs"
        ),
        "operationId": "get_ci_economics_attempt_jobs",
        "requiredRole": "audit",
        "identityParameter": "headSha",
        "cursorParameter": "afterJobId",
        "defaultPageSize": 50,
        "maximumPageSize": 100,
    },
}
_EXPECTED_FORBIDDEN_CLAIMS: Final = [
    "actual_cpu_utilization",
    "allocated_cores_without_owner_profile",
    "cache_efficiency_without_cache_evidence",
    "plan_consumption_without_receipt",
    "saved_compute_without_comparable_consumed_runs",
]


@dataclass(frozen=True, slots=True)
class CiEconomicsHttpOperation:
    operation_key: str
    method: str
    path: str
    operation_id: str
    required_role: str
    identity_parameter: str | None
    cursor_parameter: str
    default_page_size: int
    maximum_page_size: int

    def __post_init__(self) -> None:
        if self.operation_key not in {"attempts", "attemptJobs"}:
            raise ValueError("CI economics HTTP operation key is invalid")
        if self.method != "GET" or not self.path.startswith("/api/v1/economics/"):
            raise ValueError("CI economics HTTP route identity is invalid")
        if not self.operation_id or self.required_role != "audit":
            raise ValueError("CI economics HTTP operation authority is invalid")
        if self.identity_parameter not in {None, "headSha"}:
            raise ValueError("CI economics HTTP identity parameter is invalid")
        if self.cursor_parameter not in {"afterCursor", "afterJobId"}:
            raise ValueError("CI economics HTTP cursor parameter is invalid")
        if (
            type(self.default_page_size) is not int
            or type(self.maximum_page_size) is not int
            or not 1 <= self.default_page_size <= self.maximum_page_size
            or self.maximum_page_size != MAX_ECONOMICS_PAGE_SIZE
        ):
            raise ValueError("CI economics HTTP page bounds are invalid")


@dataclass(frozen=True, slots=True)
class CiEconomicsProfile:
    source_digest: str
    profile_id: str
    http_operations: tuple[CiEconomicsHttpOperation, ...]
    collection_policy: CollectionPolicy
    maximum_claims_per_round: int
    maximum_concurrent_claims: int
    maximum_terminalizations_per_claim: int
    cleanup_batch_size: int
    collection_deadline_seconds: int
    expiry_deadline_seconds: int
    purge_deadline_seconds: int
    observation_cleanup_deadline_seconds: int
    maximum_jobs_per_attempt: int
    maximum_observation_canonical_bytes: int

    def __post_init__(self) -> None:
        if len(self.source_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_digest
        ):
            raise ValueError("CI economics profile digest is invalid")
        for name, value in (
            ("maximum_claims_per_round", self.maximum_claims_per_round),
            ("maximum_concurrent_claims", self.maximum_concurrent_claims),
            (
                "maximum_terminalizations_per_claim",
                self.maximum_terminalizations_per_claim,
            ),
            ("cleanup_batch_size", self.cleanup_batch_size),
            ("collection_deadline_seconds", self.collection_deadline_seconds),
            ("expiry_deadline_seconds", self.expiry_deadline_seconds),
            ("purge_deadline_seconds", self.purge_deadline_seconds),
            (
                "observation_cleanup_deadline_seconds",
                self.observation_cleanup_deadline_seconds,
            ),
            ("maximum_jobs_per_attempt", self.maximum_jobs_per_attempt),
            ("maximum_observation_canonical_bytes", self.maximum_observation_canonical_bytes),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.collection_policy) is not CollectionPolicy:
            raise TypeError("CI economics profile requires an exact collection policy")
        if (
            type(self.http_operations) is not tuple
            or any(
                type(operation) is not CiEconomicsHttpOperation
                for operation in self.http_operations
            )
            or tuple(operation.operation_key for operation in self.http_operations)
            != ("attempts", "attemptJobs")
        ):
            raise ValueError("CI economics HTTP operations are incomplete or unordered")
        if self.maximum_concurrent_claims > self.maximum_claims_per_round:
            raise ValueError("collection concurrency cannot exceed the per-round claim bound")


def load_bundled_ci_economics_profile() -> CiEconomicsProfile:
    raw = (
        files("ci_coordinator.ci_economics.resources")
        .joinpath(CI_ECONOMICS_PROFILE_RESOURCE)
        .read_bytes()
    )
    if sha256(raw).hexdigest() != CI_ECONOMICS_PROFILE_SHA256:
        raise ValueError("bundled CI economics profile digest does not match admission")
    return parse_ci_economics_profile(raw)


def parse_ci_economics_profile(raw: bytes) -> CiEconomicsProfile:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("CI economics profile must be duplicate-free UTF-8 JSON") from error
    if type(value) is not dict:
        raise ValueError("CI economics profile must be an object")
    document = cast(dict[str, object], value)
    _require_exact_keys(
        document,
        {
            "schemaVersion",
            "profileId",
            "measurementDefinitionVersion",
            "provider",
            "collection",
            "retention",
            "maintenance",
            "http",
            "bounds",
            "persistedWebhookEvents",
            "accuracyClasses",
            "forbiddenClaims",
        },
        "profile",
    )
    if (
        document["schemaVersion"] != "ci-economics-profile/v1"
        or document["profileId"] != "ci-coordinator.ci-economics/v1"
        or document["measurementDefinitionVersion"] != MEASUREMENT_DEFINITION_VERSION
        or document["provider"] != "github"
    ):
        raise ValueError("CI economics profile identity is invalid")
    collection = _mapping(document["collection"], "collection")
    retention = _mapping(document["retention"], "retention")
    maintenance = _mapping(document["maintenance"], "maintenance")
    http = _mapping(document["http"], "http")
    bounds = _mapping(document["bounds"], "bounds")
    _require_exact_keys(
        collection,
        {
            "windowSeconds",
            "initialBackoffSeconds",
            "maximumBackoffSeconds",
            "maximumAttempts",
            "leaseSeconds",
            "maximumClaimsPerRound",
            "maximumConcurrentClaims",
            "maximumTerminalizationsPerClaim",
        },
        "collection",
    )
    _require_exact_keys(
        retention,
        {
            "evidenceDays",
            "tombstoneGraceSeconds",
            "cleanupBatchSize",
        },
        "retention",
    )
    _require_exact_keys(
        maintenance,
        {
            "collectionDeadlineSeconds",
            "expiryDeadlineSeconds",
            "observationCleanupDeadlineSeconds",
            "purgeDeadlineSeconds",
        },
        "maintenance",
    )
    _require_exact_keys(
        bounds,
        {
            "maximumAttemptPageSize",
            "maximumJobPageSize",
            "maximumJobsPerAttempt",
            "maximumJobNameCodePoints",
            "maximumRunnerTextCodePoints",
            "maximumJobLabels",
            "maximumJobLabelCodePoints",
            "maximumObservationCanonicalBytes",
        },
        "bounds",
    )
    _require_exact_keys(http, {"attempts", "attemptJobs"}, "http")
    if collection != _EXPECTED_COLLECTION:
        raise ValueError("CI economics collection policy does not match v1")
    if retention != _EXPECTED_RETENTION:
        raise ValueError("CI economics retention policy does not match v1")
    if maintenance != _EXPECTED_MAINTENANCE:
        raise ValueError("CI economics maintenance policy does not match v1")
    if http != _EXPECTED_HTTP:
        raise ValueError("CI economics HTTP contract does not match v1")
    http_operations = tuple(
        _http_operation(operation_key, http[operation_key])
        for operation_key in ("attempts", "attemptJobs")
    )
    expected_bounds = {
        "maximumAttemptPageSize": MAX_ECONOMICS_PAGE_SIZE,
        "maximumJobPageSize": MAX_ECONOMICS_PAGE_SIZE,
        "maximumJobsPerAttempt": MAX_JOBS_PER_ATTEMPT,
        "maximumJobNameCodePoints": MAX_JOB_NAME_CODE_POINTS,
        "maximumRunnerTextCodePoints": MAX_RUNNER_TEXT_CODE_POINTS,
        "maximumJobLabels": MAX_JOB_LABELS,
        "maximumJobLabelCodePoints": MAX_JOB_LABEL_CODE_POINTS,
        "maximumObservationCanonicalBytes": 16_384,
    }
    if bounds != expected_bounds:
        raise ValueError("CI economics profile bounds do not match the implementation")
    if document["persistedWebhookEvents"] != [
        {"event": "workflow_job", "actions": ["completed"]},
        {"event": "workflow_run", "actions": ["completed"]},
    ]:
        raise ValueError("CI economics persisted webhook events are invalid")
    if document["accuracyClasses"] != ["exact", "partial", "unknown", "conflict"]:
        raise ValueError("CI economics accuracy classes are invalid")
    if document["forbiddenClaims"] != _EXPECTED_FORBIDDEN_CLAIMS:
        raise ValueError("CI economics forbidden claims are invalid")
    return CiEconomicsProfile(
        source_digest=sha256(raw).hexdigest(),
        profile_id="ci-coordinator.ci-economics/v1",
        http_operations=http_operations,
        collection_policy=CollectionPolicy(
            collection_window_seconds=_positive_int(collection["windowSeconds"], "windowSeconds"),
            initial_backoff_seconds=_positive_int(
                collection["initialBackoffSeconds"], "initialBackoffSeconds"
            ),
            maximum_backoff_seconds=_positive_int(
                collection["maximumBackoffSeconds"], "maximumBackoffSeconds"
            ),
            maximum_attempts=_positive_int(collection["maximumAttempts"], "maximumAttempts"),
            lease_seconds=_positive_int(collection["leaseSeconds"], "leaseSeconds"),
            evidence_days=_positive_int(retention["evidenceDays"], "evidenceDays"),
            tombstone_grace_seconds=_positive_int(
                retention["tombstoneGraceSeconds"], "tombstoneGraceSeconds"
            ),
        ),
        maximum_claims_per_round=_positive_int(
            collection["maximumClaimsPerRound"], "maximumClaimsPerRound"
        ),
        maximum_concurrent_claims=_positive_int(
            collection["maximumConcurrentClaims"], "maximumConcurrentClaims"
        ),
        maximum_terminalizations_per_claim=_positive_int(
            collection["maximumTerminalizationsPerClaim"],
            "maximumTerminalizationsPerClaim",
        ),
        cleanup_batch_size=_positive_int(retention["cleanupBatchSize"], "cleanupBatchSize"),
        collection_deadline_seconds=_positive_int(
            maintenance["collectionDeadlineSeconds"], "collectionDeadlineSeconds"
        ),
        expiry_deadline_seconds=_positive_int(
            maintenance["expiryDeadlineSeconds"], "expiryDeadlineSeconds"
        ),
        purge_deadline_seconds=_positive_int(
            maintenance["purgeDeadlineSeconds"], "purgeDeadlineSeconds"
        ),
        observation_cleanup_deadline_seconds=_positive_int(
            maintenance["observationCleanupDeadlineSeconds"],
            "observationCleanupDeadlineSeconds",
        ),
        maximum_jobs_per_attempt=MAX_JOBS_PER_ATTEMPT,
        maximum_observation_canonical_bytes=16_384,
    )


def _mapping(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _http_operation(
    operation_key: str,
    value: object,
) -> CiEconomicsHttpOperation:
    operation = _mapping(value, f"http.{operation_key}")
    required_keys = {
        "method",
        "path",
        "operationId",
        "requiredRole",
        "cursorParameter",
        "defaultPageSize",
        "maximumPageSize",
    }
    if operation_key == "attemptJobs":
        required_keys.add("identityParameter")
    _require_exact_keys(operation, required_keys, f"http.{operation_key}")
    return CiEconomicsHttpOperation(
        operation_key=operation_key,
        method=_text(operation["method"], "method"),
        path=_text(operation["path"], "path"),
        operation_id=_text(operation["operationId"], "operationId"),
        required_role=_text(operation["requiredRole"], "requiredRole"),
        identity_parameter=(
            None
            if operation_key == "attempts"
            else _text(operation["identityParameter"], "identityParameter")
        ),
        cursor_parameter=_text(operation["cursorParameter"], "cursorParameter"),
        default_page_size=_positive_int(operation["defaultPageSize"], "defaultPageSize"),
        maximum_page_size=_positive_int(operation["maximumPageSize"], "maximumPageSize"),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("CI economics profile contains a duplicate key")
        result[key] = value
    return result


def _require_exact_keys(value: dict[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} keys are not exact")


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value
