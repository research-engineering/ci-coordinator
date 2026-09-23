from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from production_admission_support import make_production_admission_fixture

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import canonical_json
from ci_coordinator.production_admission.cutover_drain import (
    PRODUCTION_DRAIN_ENVELOPE_SCHEMA,
    AdmittedProductionDrain,
    ProductionDrainStatement,
    admit_production_drain,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.limits import MAX_PRODUCTION_DRAIN_BYTES
from ci_coordinator.production_admission.model import ProductionScopeGrant, ProductionScopeSubject

NOW = datetime(2026, 9, 6, tzinfo=UTC)
KEY_ID = "production-drain-test"


@dataclass(frozen=True)
class DrainFixture:
    key: Ed25519PrivateKey
    state: ProductionScopeState
    grant: ProductionScopeGrant
    statement: ProductionDrainStatement

    def admit(self, content: bytes) -> AdmittedProductionDrain | None:
        return admit_production_drain(
            content,
            public_key_pem=self.key.public_key().public_bytes(
                Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
            ),
            expected_key_id=KEY_ID,
        )

    def sign(self, statement: dict[str, object] | None = None, /, **headers: object) -> bytes:
        unsigned = {
            "schemaVersion": PRODUCTION_DRAIN_ENVELOPE_SCHEMA,
            "algorithm": "EdDSA",
            "keyId": KEY_ID,
            "statement": self.statement.to_mapping() if statement is None else statement,
            **headers,
        }
        signature = base64.urlsafe_b64encode(self.key.sign(canonical_json(unsigned))).rstrip(b"=")
        return canonical_json({**unsigned, "signature": signature.decode("ascii")}) + b"\n"


@pytest.fixture
def drain() -> DrainFixture:
    subject = ProductionScopeSubject(
        scope=RepositoryScope(11, 22),
        config_epoch_id="a" * 64,
        compiled_policy_hash="b" * 64,
        policy_hash="c" * 64,
        catalog_hash="d" * 64,
        target_registry_hash="e" * 64,
        workflow_refs=("owner/repo/.github/workflows/ci.yml@refs/heads/main",),
        job_workflow_refs=(),
    )
    signed = make_production_admission_fixture(subject, now=NOW)
    grant = signed.receipt.scope_grants[0]
    state = ProductionScopeState(
        scope=subject.scope,
        revision=3,
        staged_authority_id=signed.grant.authority_id,
        latch_override_id="override_" + "a" * 32,
        latch_applied_at=NOW - timedelta(seconds=1),
    )
    statement = ProductionDrainStatement(
        scope=subject.scope,
        authority_id=signed.grant.authority_id,
        admission_subject_digest=grant.admission_subject_digest,
        generation=1,
        predecessor_generation=0,
        expected_scope_revision=state.revision,
        latch_override_id="override_" + "a" * 32,
        observed_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
        old_replicas_unroutable=True,
        old_replica_requests_completed=True,
        predecessor_executions_ended=True,
    )
    return DrainFixture(Ed25519PrivateKey.generate(), state, grant, statement)


def test_signed_drain_matches_only_its_latched_scope_and_staged_receipt(
    drain: DrainFixture,
) -> None:
    admitted = drain.admit(drain.sign())
    assert admitted is not None
    assert admitted.statement == drain.statement
    assert admitted.matches(drain.state, drain.grant, database_now=NOW)
    assert not admitted.matches(
        replace(drain.state, staged_authority_id=None), drain.grant, database_now=NOW
    )
    assert not admitted.matches(
        replace(drain.state, latch_override_id=None, latch_applied_at=None),
        drain.grant,
        database_now=NOW,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"installationId": 12},
        {"repositoryId": 23},
        {"authorityId": "production_admission_" + "b" * 32},
        {"admissionSubjectDigest": "0" * 64},
        {"generation": 2, "predecessorGeneration": 1},
        {"expectedScopeRevision": 4},
        {"latchOverrideId": "override_" + "b" * 32},
        {"observedAt": "2026-09-05T23:59:58.000Z"},
        {"observedAt": "2026-09-06T00:00:00.001Z"},
    ],
    ids=[
        "installation",
        "repository",
        "receipt",
        "subject",
        "generation",
        "revision",
        "latch",
        "before-latch",
        "future",
    ],
)
def test_correctly_resigned_cross_context_drain_still_rejects(
    drain: DrainFixture, changes: dict[str, object]
) -> None:
    admitted = drain.admit(drain.sign({**drain.statement.to_mapping(), **changes}))
    assert admitted is not None
    assert not admitted.matches(drain.state, drain.grant, database_now=NOW)


@pytest.mark.parametrize(
    "field", ["oldReplicasUnroutable", "oldReplicaRequestsCompleted", "predecessorExecutionsEnded"]
)
@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_each_remote_claim_requires_an_explicit_boolean_true(
    drain: DrainFixture, field: str, value: object
) -> None:
    assert drain.admit(drain.sign({**drain.statement.to_mapping(), field: value})) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": True},
        {"predecessorGeneration": 1},
        {"schemaVersion": "ci-coordinator.production-drain-statement/v2"},
        {"unexpected": True},
        {"expectedScopeRevision": 0},
        {"expiresAt": "2026-09-06T00:00:00.000Z"},
        {"expiresAt": "2026-09-06T00:05:00.001Z"},
        {"observedAt": "2026-09-06T00:00:00.000001Z"},
        {"observedAt": "2026-09-06T00:00:00+00:00"},
    ],
)
def test_signed_drain_rejects_noncanonical_or_contradictory_values(
    drain: DrainFixture, changes: dict[str, object]
) -> None:
    assert drain.admit(drain.sign({**drain.statement.to_mapping(), **changes})) is None


@pytest.mark.parametrize(
    "headers",
    [
        {"schemaVersion": "ci-coordinator.production-admission-envelope/v2"},
        {"algorithm": "HS256"},
        {"keyId": "another-key"},
        {"extra": "value"},
    ],
)
def test_drain_signature_cannot_cross_envelope_domains(
    drain: DrainFixture, headers: dict[str, object]
) -> None:
    assert drain.admit(drain.sign(**headers)) is None


def test_drain_admission_rejects_wire_and_signature_mutations(drain: DrainFixture) -> None:
    content = drain.sign()
    for invalid in (
        b"",
        b"{}",
        content + b"\n",
        b" " * (MAX_PRODUCTION_DRAIN_BYTES + 1),
        content.replace(b'"repositoryId":22', b'"repositoryId":23'),
        content.replace(b'"repositoryId":22', b'"repositoryId":22,"repositoryId":22'),
    ):
        assert invalid != content
        assert drain.admit(invalid) is None


@pytest.mark.parametrize("offset,allowed", [(0, True), (59_999_999, True), (60_000_000, False)])
def test_drain_expiry_is_strict_at_the_locked_database_instant(
    drain: DrainFixture, offset: int, allowed: bool
) -> None:
    admitted = drain.admit(drain.sign())
    assert admitted is not None
    assert (
        admitted.matches(
            drain.state, drain.grant, database_now=NOW + timedelta(microseconds=offset)
        )
        is allowed
    )


def test_drain_cannot_be_constructed_or_copied_without_signature_admission(
    drain: DrainFixture,
) -> None:
    with pytest.raises(TypeError, match="owner signature admission"):
        AdmittedProductionDrain(drain.statement, "0" * 64, object())
    admitted = drain.admit(drain.sign())
    assert admitted is not None
    with pytest.raises(TypeError, match="token"):
        replace(admitted, envelope_digest="0" * 64)  # type: ignore[call-arg]
