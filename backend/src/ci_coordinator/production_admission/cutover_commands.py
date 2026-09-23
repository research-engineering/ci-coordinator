from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.production_admission.cutover_state import (
    ProductionScopeState,
    _require_authority_id,
    _require_counter,
)
from ci_coordinator.production_admission.model import _require_digest

PRODUCTION_CUTOVER_EVENT_TYPE = "production_cutover_applied"
type ProductionCutoverKind = Literal["stage", "begin", "activate"]


def production_stage_input_digest(
    envelope: bytes, evidence: bytes, provider_paths: tuple[str, ...]
) -> str:
    return hash_object(
        {
            "envelopeHash": sha256(envelope).hexdigest(),
            "evidenceHash": sha256(evidence).hexdigest(),
            "providerPaths": list(provider_paths),
        }
    )


@dataclass(frozen=True, slots=True)
class ProductionCutoverCommand:
    kind: ProductionCutoverKind
    scope: RepositoryScope
    operation_id: str
    actor: str
    reason: str
    expected_revision: int
    authority_id: str
    input_digest: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in {"stage", "begin", "activate"}:
            raise ValueError("production cutover command kind is invalid")
        if type(self.scope) is not RepositoryScope:
            raise TypeError("production cutover command scope must be exact")
        for value in (self.operation_id, self.actor, self.reason):
            if type(value) is not str or not value or len(value.encode("utf-8")) > 512:
                raise ValueError("production cutover command text must be bounded")
        _require_counter(self.expected_revision)
        _require_authority_id(self.authority_id)
        _require_digest(self.input_digest, "production cutover command input")

    @property
    def audit_key(self) -> str:
        return "production-cutover:" + hash_object(
            {
                "installationId": self.scope.installation_id,
                "repositoryId": self.scope.repository_id,
                "operationId": self.operation_id,
            }
        )

    @property
    def command_digest(self) -> str:
        return hash_object(self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-coordinator.production-cutover-command/v1",
            "kind": self.kind,
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "operationId": self.operation_id,
            "actor": self.actor,
            "reason": self.reason,
            "expectedRevision": self.expected_revision,
            "authorityId": self.authority_id,
            "inputDigest": self.input_digest,
        }


@dataclass(frozen=True, slots=True)
class ProductionCutoverApplied:
    state: ProductionScopeState
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class ProductionCutoverRejected:
    reason: Literal[
        "command_conflict",
        "revision_changed",
        "stage_changed",
        "generation_changed",
        "config_changed",
        "authority_expired",
        "capacity_exhausted",
        "drain_incomplete",
        "current_evidence_invalid",
        "override_conflict",
    ]


type ProductionCutoverResult = ProductionCutoverApplied | ProductionCutoverRejected
