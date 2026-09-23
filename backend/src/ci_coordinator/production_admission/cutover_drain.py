from __future__ import annotations

from dataclasses import InitVar, dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import StrictJsonError, canonical_json, load_strict_json
from ci_coordinator.kernel.ed25519 import (
    admit_ed25519_public_key,
    verify_canonical_ed25519_signature,
)
from ci_coordinator.production_admission._fields import (
    _exact_object,
    _nonnegative_integer,
    _positive_integer,
    _text,
    _timestamp,
)
from ci_coordinator.production_admission.cutover_state import (
    ProductionScopeState,
    _require_authority_id,
    _require_counter,
    _require_latch_id,
)
from ci_coordinator.production_admission.limits import (
    MAX_PRODUCTION_DRAIN_BYTES,
    MAX_PRODUCTION_DRAIN_SECONDS,
)
from ci_coordinator.production_admission.model import (
    ProductionScopeGrant,
    _require_aware_utc,
    _require_digest,
)
from ci_coordinator.production_admission.model import _timestamp as render_timestamp

PRODUCTION_DRAIN_ENVELOPE_SCHEMA = "ci-coordinator.production-drain-envelope/v1"
PRODUCTION_DRAIN_STATEMENT_SCHEMA = "ci-coordinator.production-drain-statement/v1"
_DRAIN_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ProductionDrainStatement:
    scope: RepositoryScope
    authority_id: str
    admission_subject_digest: str
    generation: int
    predecessor_generation: int
    expected_scope_revision: int
    latch_override_id: str
    observed_at: datetime
    expires_at: datetime
    old_replicas_unroutable: bool
    old_replica_requests_completed: bool
    predecessor_executions_ended: bool

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("production drain requires an exact scope")
        _require_authority_id(self.authority_id)
        _require_digest(self.admission_subject_digest, "production drain subject")
        _require_latch_id(self.latch_override_id)
        _require_counter(self.generation, minimum=1)
        _require_counter(self.predecessor_generation)
        _require_counter(self.expected_scope_revision, minimum=1)
        if self.generation != self.predecessor_generation + 1:
            raise ValueError("production drain requires the exact successor generation")
        for instant in (self.observed_at, self.expires_at):
            _require_aware_utc(instant, "production drain time")
            if instant.microsecond % 1000:
                raise ValueError("production drain times require exact milliseconds")
        if (
            not timedelta(0)
            < self.expires_at - self.observed_at
            <= timedelta(seconds=MAX_PRODUCTION_DRAIN_SECONDS)
        ):
            raise ValueError("production drain validity interval is outside its bound")
        if any(
            value is not True
            for value in (
                self.old_replicas_unroutable,
                self.old_replica_requests_completed,
                self.predecessor_executions_ended,
            )
        ):
            raise ValueError("production drain requires all three explicit true attestations")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": PRODUCTION_DRAIN_STATEMENT_SCHEMA,
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "authorityId": self.authority_id,
            "admissionSubjectDigest": self.admission_subject_digest,
            "generation": self.generation,
            "predecessorGeneration": self.predecessor_generation,
            "expectedScopeRevision": self.expected_scope_revision,
            "latchOverrideId": self.latch_override_id,
            "observedAt": render_timestamp(self.observed_at),
            "expiresAt": render_timestamp(self.expires_at),
            "oldReplicasUnroutable": self.old_replicas_unroutable,
            "oldReplicaRequestsCompleted": self.old_replica_requests_completed,
            "predecessorExecutionsEnded": self.predecessor_executions_ended,
        }


@dataclass(frozen=True, slots=True)
class AdmittedProductionDrain:
    statement: ProductionDrainStatement
    envelope_digest: str
    token: InitVar[object]

    def __post_init__(self, token: object) -> None:
        if token is not _DRAIN_TOKEN:
            raise TypeError("production drain requires owner signature admission")

    def matches(
        self,
        state: ProductionScopeState,
        scope_grant: ProductionScopeGrant,
        *,
        database_now: datetime,
    ) -> bool:
        _require_aware_utc(database_now, "production drain database time")
        statement = self.statement
        return (
            type(state) is ProductionScopeState
            and type(scope_grant) is ProductionScopeGrant
            and statement.scope == state.scope == scope_grant.subject.scope
            and statement.authority_id == state.staged_authority_id
            and statement.admission_subject_digest == scope_grant.admission_subject_digest
            and statement.generation == scope_grant.relation.generation
            and statement.predecessor_generation == state.generation
            and state.revoked_through_generation >= statement.predecessor_generation
            and statement.expected_scope_revision == state.revision
            and statement.latch_override_id == state.latch_override_id
            and state.latch_applied_at is not None
            and state.latch_applied_at <= statement.observed_at <= database_now
            and database_now < statement.expires_at
        )


def admit_production_drain(
    content: bytes, *, public_key_pem: bytes, expected_key_id: str
) -> AdmittedProductionDrain | None:
    try:
        root = _exact_object(
            load_strict_json(content, max_bytes=MAX_PRODUCTION_DRAIN_BYTES),
            {"schemaVersion", "algorithm", "keyId", "statement", "signature"},
        )
        if (
            canonical_json(root) + b"\n" != content
            or root["schemaVersion"] != PRODUCTION_DRAIN_ENVELOPE_SCHEMA
            or root["algorithm"] != "EdDSA"
            or _text(root["keyId"]) != expected_key_id
        ):
            return None
        public_key = admit_ed25519_public_key(public_key_pem)
        unsigned = {key: value for key, value in root.items() if key != "signature"}
        if public_key is None or not verify_canonical_ed25519_signature(
            public_key, signature=_text(root["signature"]), payload=canonical_json(unsigned)
        ):
            return None
        statement = _statement(root["statement"])
        return AdmittedProductionDrain(statement, sha256(content).hexdigest(), _DRAIN_TOKEN)
    except (StrictJsonError, TypeError, ValueError):
        return None


def _statement(value: object) -> ProductionDrainStatement:
    mapping = _exact_object(
        value,
        {
            "schemaVersion",
            "installationId",
            "repositoryId",
            "authorityId",
            "admissionSubjectDigest",
            "generation",
            "predecessorGeneration",
            "expectedScopeRevision",
            "latchOverrideId",
            "observedAt",
            "expiresAt",
            "oldReplicasUnroutable",
            "oldReplicaRequestsCompleted",
            "predecessorExecutionsEnded",
        },
    )
    if mapping["schemaVersion"] != PRODUCTION_DRAIN_STATEMENT_SCHEMA or any(
        mapping[name] is not True
        for name in (
            "oldReplicasUnroutable",
            "oldReplicaRequestsCompleted",
            "predecessorExecutionsEnded",
        )
    ):
        raise ValueError("production drain statement is incomplete")
    return ProductionDrainStatement(
        scope=RepositoryScope(
            _positive_integer(mapping["installationId"]),
            _positive_integer(mapping["repositoryId"]),
        ),
        authority_id=_text(mapping["authorityId"]),
        admission_subject_digest=_text(mapping["admissionSubjectDigest"]),
        generation=_positive_integer(mapping["generation"]),
        predecessor_generation=_nonnegative_integer(mapping["predecessorGeneration"]),
        expected_scope_revision=_positive_integer(mapping["expectedScopeRevision"]),
        latch_override_id=_text(mapping["latchOverrideId"]),
        observed_at=_timestamp(mapping["observedAt"]),
        expires_at=_timestamp(mapping["expiresAt"]),
        old_replicas_unroutable=True,
        old_replica_requests_completed=True,
        predecessor_executions_ended=True,
    )
