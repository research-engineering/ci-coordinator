from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from ci_coordinator.capacity_qualification.profile import CAPACITY_QUALIFICATION_PROFILE
from ci_coordinator.kernel import hash_object, is_safe_json_integer

_ARTIFACT_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")
_ENVIRONMENT_ID: Final = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_GIT_SHA: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
MAX_CAPACITY_MEASUREMENTS: Final = 256
MAX_CAPACITY_METRIC_ID_BYTES: Final = 512


@dataclass(frozen=True, slots=True)
class ReplicaTopology:
    replicas: int
    workers_per_replica: int
    maximum_request_concurrency_per_worker: int

    def __post_init__(self) -> None:
        _bounded_positive(self.replicas, 1_024, "replica count")
        _bounded_positive(self.workers_per_replica, 1_024, "worker count")
        _bounded_positive(
            self.maximum_request_concurrency_per_worker,
            1_000_000,
            "request concurrency",
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "maximumRequestConcurrencyPerWorker": self.maximum_request_concurrency_per_worker,
            "replicas": self.replicas,
            "workersPerReplica": self.workers_per_replica,
        }


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    cpu_millicores: int
    memory_bytes: int
    pids: int
    termination_grace_milliseconds: int

    def __post_init__(self) -> None:
        _bounded_positive(self.cpu_millicores, 100_000_000, "CPU limit")
        _bounded_positive(self.memory_bytes, 9_007_199_254_740_991, "memory limit")
        _bounded_positive(self.pids, 1_000_000, "PID limit")
        _bounded_positive(
            self.termination_grace_milliseconds,
            86_400_000,
            "termination grace",
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "cpuMillicores": self.cpu_millicores,
            "memoryBytes": self.memory_bytes,
            "pids": self.pids,
            "terminationGraceMilliseconds": self.termination_grace_milliseconds,
        }


@dataclass(frozen=True, slots=True)
class CapacityEvidenceIdentity:
    artifact_digest: str
    source_commit: str
    environment_id: str
    database_identity_digest: str
    deployment_subject_digest: str
    ingress_control_digest: str
    network_policy_digest: str
    provider_capacity_policy_digest: str
    retention_policy_digest: str
    runtime_configuration_digest: str
    replica_topology: ReplicaTopology
    resource_limits: ResourceLimits
    workload_digest: str
    measurement_protocol_digest: str
    sample_set_digest: str
    audit_storage_inventory_digest: str

    def __post_init__(self) -> None:
        if _ARTIFACT_DIGEST.fullmatch(self.artifact_digest) is None:
            raise ValueError("capacity artifact digest is invalid")
        if _GIT_SHA.fullmatch(self.source_commit) is None:
            raise ValueError("capacity source commit is invalid")
        if _ENVIRONMENT_ID.fullmatch(self.environment_id) is None:
            raise ValueError("capacity environment id is invalid")
        for name, value in (
            ("database identity", self.database_identity_digest),
            ("deployment subject", self.deployment_subject_digest),
            ("ingress control", self.ingress_control_digest),
            ("network policy", self.network_policy_digest),
            ("provider capacity policy", self.provider_capacity_policy_digest),
            ("retention policy", self.retention_policy_digest),
            ("runtime configuration", self.runtime_configuration_digest),
            ("workload", self.workload_digest),
            ("measurement protocol", self.measurement_protocol_digest),
            ("sample set", self.sample_set_digest),
            ("audit storage inventory", self.audit_storage_inventory_digest),
        ):
            _require_digest(value, name)
        if type(self.replica_topology) is not ReplicaTopology:
            raise TypeError("capacity replica topology must be exact")
        if type(self.resource_limits) is not ResourceLimits:
            raise TypeError("capacity resource limits must be exact")

    def to_mapping(self) -> dict[str, object]:
        return {
            "artifactDigest": self.artifact_digest,
            "auditStorageInventoryDigest": self.audit_storage_inventory_digest,
            "databaseIdentityDigest": self.database_identity_digest,
            "deploymentSubjectDigest": self.deployment_subject_digest,
            "environmentId": self.environment_id,
            "ingressControlDigest": self.ingress_control_digest,
            "measurementProtocolDigest": self.measurement_protocol_digest,
            "networkPolicyDigest": self.network_policy_digest,
            "providerCapacityPolicyDigest": self.provider_capacity_policy_digest,
            "replicaTopology": self.replica_topology.to_mapping(),
            "resourceLimits": self.resource_limits.to_mapping(),
            "sampleSetDigest": self.sample_set_digest,
            "sourceCommit": self.source_commit,
            "retentionPolicyDigest": self.retention_policy_digest,
            "runtimeConfigurationDigest": self.runtime_configuration_digest,
            "workloadDigest": self.workload_digest,
        }


@dataclass(frozen=True, slots=True)
class CapacityMeasurement:
    metric_id: str
    budget_value: int
    observed_value: int
    sample_count: int

    def __post_init__(self) -> None:
        if (
            type(self.metric_id) is not str
            or not self.metric_id
            or len(self.metric_id.encode("utf-8")) > MAX_CAPACITY_METRIC_ID_BYTES
        ):
            raise ValueError("capacity metric id is invalid")
        for name, value in (
            ("budget", self.budget_value),
            ("observation", self.observed_value),
        ):
            if type(value) is not int or value < 0 or not is_safe_json_integer(value):
                raise ValueError(f"capacity metric {name} is invalid")
        _bounded_positive(
            self.sample_count,
            CAPACITY_QUALIFICATION_PROFILE.limits.maximum_sample_count,
            "capacity sample count",
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "budgetValue": self.budget_value,
            "metricId": self.metric_id,
            "observedValue": self.observed_value,
            "sampleCount": self.sample_count,
        }


@dataclass(frozen=True, slots=True)
class CapacityReceipt:
    capacity_profile_digest: str
    budget_set_digest: str
    identity: CapacityEvidenceIdentity
    observed_at: datetime
    issued_at: datetime
    valid_from: datetime
    valid_until: datetime
    measurements: tuple[CapacityMeasurement, ...]

    def __post_init__(self) -> None:
        _require_digest(self.capacity_profile_digest, "capacity profile")
        _require_digest(self.budget_set_digest, "capacity budget set")
        if type(self.identity) is not CapacityEvidenceIdentity:
            raise TypeError("capacity receipt identity must be exact")
        for name, value in (
            ("observation", self.observed_at),
            ("issuance", self.issued_at),
            ("validity start", self.valid_from),
            ("validity end", self.valid_until),
        ):
            _require_utc_millisecond(value, name)
        if not self.observed_at <= self.issued_at:
            raise ValueError("capacity receipt observation cannot follow issuance")
        if not self.valid_from <= self.issued_at < self.valid_until:
            raise ValueError("capacity receipt validity interval is invalid")
        if (
            type(self.measurements) is not tuple
            or not 1 <= len(self.measurements) <= MAX_CAPACITY_MEASUREMENTS
        ):
            raise ValueError("capacity receipt measurement count is invalid")
        if any(type(item) is not CapacityMeasurement for item in self.measurements):
            raise TypeError("capacity receipt measurements must be exact")
        metric_ids = tuple(item.metric_id for item in self.measurements)
        if tuple(sorted(set(metric_ids))) != metric_ids:
            raise ValueError("capacity receipt measurements must be unique and sorted")
        if self.budget_set_digest != capacity_budget_set_digest(self.measurements):
            raise ValueError("capacity receipt budget-set digest does not match")

    def to_mapping(self) -> dict[str, object]:
        return {
            "budgetSetDigest": self.budget_set_digest,
            "capacityProfileDigest": self.capacity_profile_digest,
            "identity": self.identity.to_mapping(),
            "issuedAt": _timestamp(self.issued_at),
            "measurements": [item.to_mapping() for item in self.measurements],
            "observedAt": _timestamp(self.observed_at),
            "schemaVersion": CAPACITY_QUALIFICATION_PROFILE.receipt_schema,
            "validFrom": _timestamp(self.valid_from),
            "validUntil": _timestamp(self.valid_until),
        }


@dataclass(frozen=True, slots=True)
class CapacityExpectation:
    identity: CapacityEvidenceIdentity
    budget_set_digest: str

    def __post_init__(self) -> None:
        if type(self.identity) is not CapacityEvidenceIdentity:
            raise TypeError("capacity expectation identity must be exact")
        _require_digest(self.budget_set_digest, "expected capacity budget set")


def capacity_budget_set_digest(measurements: tuple[CapacityMeasurement, ...]) -> str:
    return hash_object(
        {
            "budgets": [
                {"budgetValue": item.budget_value, "metricId": item.metric_id}
                for item in measurements
            ],
            "schemaVersion": "ci-coordinator-capacity-budget-set/v1",
        }
    )


def _require_digest(value: object, name: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"capacity {name} digest is invalid")


def _bounded_positive(value: object, maximum: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} is outside its admitted interval")


def _require_utc_millisecond(value: object, name: str) -> None:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.astimezone(UTC).utcoffset() != value.utcoffset()
        or value.microsecond % 1_000 != 0
    ):
        raise ValueError(f"capacity receipt {name} must be canonical UTC milliseconds")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
