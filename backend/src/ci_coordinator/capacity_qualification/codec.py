from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from ci_coordinator.capacity_qualification.model import (
    MAX_CAPACITY_MEASUREMENTS,
    CapacityEvidenceIdentity,
    CapacityMeasurement,
    CapacityReceipt,
    ReplicaTopology,
    ResourceLimits,
)
from ci_coordinator.capacity_qualification.profile import CAPACITY_QUALIFICATION_PROFILE
from ci_coordinator.kernel import (
    JsonResourceLimits,
    bounded_canonical_json,
    canonical_json,
    load_strict_json,
)

_JSON_LIMITS = JsonResourceLimits(max_depth=8, max_nodes=4_096)


@dataclass(frozen=True, slots=True)
class DecodedCapacityEnvelope:
    key_id: str
    signature: str
    receipt: CapacityReceipt

    def unsigned_mapping(self) -> dict[str, object]:
        return _unsigned_mapping(self.receipt, self.key_id)


def capacity_signature_payload(receipt: CapacityReceipt, *, key_id: str) -> bytes:
    _require_key_id(key_id)
    if type(receipt) is not CapacityReceipt:
        raise TypeError("capacity signature payload requires an exact receipt")
    unsigned = _unsigned_mapping(receipt, key_id)
    _encode_envelope_mapping({**unsigned, "signature": "A" * 86})
    return bounded_canonical_json(
        unsigned,
        max_bytes=CAPACITY_QUALIFICATION_PROFILE.limits.maximum_envelope_bytes - 1,
        resource_limits=_JSON_LIMITS,
    )


def encode_capacity_envelope(
    receipt: CapacityReceipt,
    *,
    key_id: str,
    signature: str,
) -> bytes:
    _require_key_id(key_id)
    _require_signature_text(signature)
    return _encode_envelope_mapping({**_unsigned_mapping(receipt, key_id), "signature": signature})


def decode_capacity_envelope(content: bytes) -> DecodedCapacityEnvelope:
    profile = CAPACITY_QUALIFICATION_PROFILE
    root = _exact_object(
        load_strict_json(
            content,
            max_bytes=profile.limits.maximum_envelope_bytes,
            resource_limits=_JSON_LIMITS,
        ),
        {"algorithm", "keyId", "receipt", "schemaVersion", "signature"},
    )
    if canonical_json(root, resource_limits=_JSON_LIMITS) + b"\n" != content:
        raise ValueError("capacity envelope is not canonical")
    if root["schemaVersion"] != profile.envelope_schema or root["algorithm"] != profile.algorithm:
        raise ValueError("capacity envelope identity is unsupported")
    key_id = _text(root["keyId"], "capacity key id")
    signature = _text(root["signature"], "capacity signature")
    _require_key_id(key_id)
    _require_signature_text(signature)
    return DecodedCapacityEnvelope(
        key_id=key_id,
        signature=signature,
        receipt=_receipt(root["receipt"]),
    )


def _unsigned_mapping(receipt: CapacityReceipt, key_id: str) -> dict[str, object]:
    return {
        "algorithm": CAPACITY_QUALIFICATION_PROFILE.algorithm,
        "keyId": key_id,
        "receipt": receipt.to_mapping(),
        "schemaVersion": CAPACITY_QUALIFICATION_PROFILE.envelope_schema,
    }


def _receipt(value: object) -> CapacityReceipt:
    profile = CAPACITY_QUALIFICATION_PROFILE
    record = _exact_object(
        value,
        {
            "budgetSetDigest",
            "capacityProfileDigest",
            "identity",
            "issuedAt",
            "measurements",
            "observedAt",
            "schemaVersion",
            "validFrom",
            "validUntil",
        },
    )
    if record["schemaVersion"] != profile.receipt_schema:
        raise ValueError("capacity receipt schema is unsupported")
    return CapacityReceipt(
        capacity_profile_digest=_text(record["capacityProfileDigest"], "profile digest"),
        budget_set_digest=_text(record["budgetSetDigest"], "budget-set digest"),
        identity=_identity(record["identity"]),
        observed_at=_timestamp(record["observedAt"]),
        issued_at=_timestamp(record["issuedAt"]),
        valid_from=_timestamp(record["validFrom"]),
        valid_until=_timestamp(record["validUntil"]),
        measurements=_measurements(record["measurements"]),
    )


def _identity(value: object) -> CapacityEvidenceIdentity:
    record = _exact_object(
        value,
        {
            "artifactDigest",
            "auditStorageInventoryDigest",
            "databaseIdentityDigest",
            "deploymentSubjectDigest",
            "environmentId",
            "ingressControlDigest",
            "measurementProtocolDigest",
            "networkPolicyDigest",
            "providerCapacityPolicyDigest",
            "replicaTopology",
            "resourceLimits",
            "sampleSetDigest",
            "sourceCommit",
            "retentionPolicyDigest",
            "runtimeConfigurationDigest",
            "workloadDigest",
        },
    )
    topology = _exact_object(
        record["replicaTopology"],
        {"maximumRequestConcurrencyPerWorker", "replicas", "workersPerReplica"},
    )
    limits = _exact_object(
        record["resourceLimits"],
        {"cpuMillicores", "memoryBytes", "pids", "terminationGraceMilliseconds"},
    )
    return CapacityEvidenceIdentity(
        artifact_digest=_text(record["artifactDigest"], "artifact digest"),
        source_commit=_text(record["sourceCommit"], "source commit"),
        environment_id=_text(record["environmentId"], "environment id"),
        database_identity_digest=_text(
            record["databaseIdentityDigest"], "database identity digest"
        ),
        deployment_subject_digest=_text(
            record["deploymentSubjectDigest"], "deployment subject digest"
        ),
        ingress_control_digest=_text(record["ingressControlDigest"], "ingress control digest"),
        network_policy_digest=_text(record["networkPolicyDigest"], "network policy digest"),
        provider_capacity_policy_digest=_text(
            record["providerCapacityPolicyDigest"], "provider capacity policy digest"
        ),
        retention_policy_digest=_text(record["retentionPolicyDigest"], "retention policy digest"),
        runtime_configuration_digest=_text(
            record["runtimeConfigurationDigest"], "runtime configuration digest"
        ),
        replica_topology=ReplicaTopology(
            replicas=_integer(topology["replicas"], "replicas"),
            workers_per_replica=_integer(topology["workersPerReplica"], "workers"),
            maximum_request_concurrency_per_worker=_integer(
                topology["maximumRequestConcurrencyPerWorker"], "request concurrency"
            ),
        ),
        resource_limits=ResourceLimits(
            cpu_millicores=_integer(limits["cpuMillicores"], "CPU limit"),
            memory_bytes=_integer(limits["memoryBytes"], "memory limit"),
            pids=_integer(limits["pids"], "PID limit"),
            termination_grace_milliseconds=_integer(
                limits["terminationGraceMilliseconds"], "termination grace"
            ),
        ),
        workload_digest=_text(record["workloadDigest"], "workload digest"),
        measurement_protocol_digest=_text(
            record["measurementProtocolDigest"], "measurement protocol digest"
        ),
        sample_set_digest=_text(record["sampleSetDigest"], "sample-set digest"),
        audit_storage_inventory_digest=_text(
            record["auditStorageInventoryDigest"], "audit storage inventory digest"
        ),
    )


def _measurements(value: object) -> tuple[CapacityMeasurement, ...]:
    if type(value) is not list or not 1 <= len(value) <= MAX_CAPACITY_MEASUREMENTS:
        raise ValueError("capacity measurement count is invalid")
    measurements: list[CapacityMeasurement] = []
    for item in value:
        record = _exact_object(
            item,
            {"budgetValue", "metricId", "observedValue", "sampleCount"},
        )
        measurements.append(
            CapacityMeasurement(
                metric_id=_text(record["metricId"], "metric id"),
                budget_value=_integer(record["budgetValue"], "budget"),
                observed_value=_integer(record["observedValue"], "observation"),
                sample_count=_integer(record["sampleCount"], "sample count"),
            )
        )
    return tuple(measurements)


def _timestamp(value: object) -> datetime:
    text = _text(value, "capacity timestamp")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("capacity timestamp is invalid") from error
    if parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") != text:
        raise ValueError("capacity timestamp is noncanonical")
    return parsed


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("capacity object shape is invalid")
    return cast(dict[str, object], value)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 512:
        raise ValueError(f"{name} is invalid")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} is invalid")
    return value


def _require_key_id(value: object) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in value
        )
    ):
        raise ValueError("capacity key id is invalid")


def _require_signature_text(value: object) -> None:
    if type(value) is not str or len(value) != 86:
        raise ValueError("capacity signature is invalid")


def _encode_envelope_mapping(value: dict[str, object]) -> bytes:
    maximum_payload_bytes = CAPACITY_QUALIFICATION_PROFILE.limits.maximum_envelope_bytes - 1
    return (
        bounded_canonical_json(
            value,
            max_bytes=maximum_payload_bytes,
            resource_limits=_JSON_LIMITS,
        )
        + b"\n"
    )
