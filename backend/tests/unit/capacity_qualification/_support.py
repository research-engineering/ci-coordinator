from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ci_coordinator.capacity_qualification import (
    CAPACITY_QUALIFICATION_PROFILE,
    CAPACITY_QUALIFICATION_PROFILE_DIGEST,
    CapacityEvidenceIdentity,
    CapacityExpectation,
    CapacityMeasurement,
    CapacityReceipt,
    ReplicaTopology,
    ResourceLimits,
    capacity_budget_set_digest,
    capacity_signature_payload,
    encode_capacity_envelope,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
CAPACITY_KEY_ID = "capacity-owner-2026-09"
CAPACITY_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
CAPACITY_PUBLIC_KEY_PEM = CAPACITY_PRIVATE_KEY.public_key().public_bytes(
    Encoding.PEM,
    PublicFormat.SubjectPublicKeyInfo,
)


def make_capacity_identity(
    *,
    artifact_digest: str = "sha256:" + "a" * 64,
    database_identity_digest: str = "b" * 64,
    audit_storage_inventory_digest: str = "c" * 64,
    environment_id: str = "test-capacity",
    source_commit: str = "d" * 40,
) -> CapacityEvidenceIdentity:
    return CapacityEvidenceIdentity(
        artifact_digest=artifact_digest,
        source_commit=source_commit,
        environment_id=environment_id,
        database_identity_digest=database_identity_digest,
        deployment_subject_digest="e" * 64,
        ingress_control_digest="f" * 64,
        network_policy_digest="1" * 64,
        provider_capacity_policy_digest="2" * 64,
        retention_policy_digest="3" * 64,
        runtime_configuration_digest="4" * 64,
        replica_topology=ReplicaTopology(
            replicas=2,
            workers_per_replica=1,
            maximum_request_concurrency_per_worker=128,
        ),
        resource_limits=ResourceLimits(
            cpu_millicores=2_000,
            memory_bytes=1_073_741_824,
            pids=256,
            termination_grace_milliseconds=45_000,
        ),
        workload_digest="5" * 64,
        measurement_protocol_digest="6" * 64,
        sample_set_digest="7" * 64,
        audit_storage_inventory_digest=audit_storage_inventory_digest,
    )


def make_capacity_measurements(
    *,
    overrides: dict[str, tuple[int, int, int]] | None = None,
) -> tuple[CapacityMeasurement, ...]:
    overrides = overrides or {}
    fixed_budgets = dict(CAPACITY_QUALIFICATION_PROFILE.fixed_budgets)
    measurements: list[CapacityMeasurement] = []
    for metric_id in CAPACITY_QUALIFICATION_PROFILE.metric_ids:
        default = (
            (fixed_budgets[metric_id], 0, 100) if metric_id in fixed_budgets else (1_000, 100, 100)
        )
        budget_value, observed_value, sample_count = overrides.get(metric_id, default)
        measurements.append(
            CapacityMeasurement(
                metric_id=metric_id,
                budget_value=budget_value,
                observed_value=observed_value,
                sample_count=sample_count,
            )
        )
    return tuple(measurements)


def make_capacity_receipt(
    *,
    identity: CapacityEvidenceIdentity | None = None,
    measurements: tuple[CapacityMeasurement, ...] | None = None,
    observed_at: datetime | None = None,
    issued_at: datetime = NOW,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> CapacityReceipt:
    measurements = measurements or make_capacity_measurements()
    return CapacityReceipt(
        capacity_profile_digest=CAPACITY_QUALIFICATION_PROFILE_DIGEST,
        budget_set_digest=capacity_budget_set_digest(measurements),
        identity=identity or make_capacity_identity(),
        observed_at=observed_at or issued_at - timedelta(minutes=5),
        issued_at=issued_at,
        valid_from=valid_from or issued_at - timedelta(minutes=1),
        valid_until=valid_until or issued_at + timedelta(hours=1),
        measurements=measurements,
    )


def capacity_expectation(receipt: CapacityReceipt) -> CapacityExpectation:
    return CapacityExpectation(
        identity=receipt.identity,
        budget_set_digest=receipt.budget_set_digest,
    )


def sign_capacity_receipt(
    receipt: CapacityReceipt,
    *,
    private_key: Ed25519PrivateKey = CAPACITY_PRIVATE_KEY,
    key_id: str = CAPACITY_KEY_ID,
) -> bytes:
    signature = private_key.sign(capacity_signature_payload(receipt, key_id=key_id))
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return encode_capacity_envelope(receipt, key_id=key_id, signature=encoded)
