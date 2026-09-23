"""Versioned wire projections for scoped production-admission administration."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator

from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverCommand,
    ProductionCutoverKind,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.limits import (
    MAX_PRODUCTION_DRAIN_BYTES,
    MAX_PRODUCTION_STAGE_BYTES,
)

type ScopeId = Annotated[int, Field(gt=0, le=9_007_199_254_740_991)]
type AuthorityId = Annotated[str, Field(pattern=r"^production_admission_[0-9a-f]{32}$")]
type CommandText = Annotated[str, Field(min_length=1, max_length=512)]


def _utf8(value: str) -> str:
    value.encode("utf-8")
    return value


type Utf8Text = Annotated[str, AfterValidator(_utf8)]


class ProductionCommandBody(RequestModel):
    schema_version: Literal["ci-coordinator.production-cutover-request/v1"] = Field(
        validation_alias="schemaVersion"
    )
    installation_id: ScopeId = Field(validation_alias="installationId")
    repository_id: ScopeId = Field(validation_alias="repositoryId")
    operation_id: CommandText = Field(validation_alias="operationId")
    authority_id: AuthorityId = Field(validation_alias="authorityId")
    expected_revision: Annotated[int, Field(ge=0, le=9_007_199_254_740_991)] = Field(
        validation_alias="expectedRevision"
    )
    reason: CommandText

    @field_validator("operation_id", "reason")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 512:
            raise ValueError("production command text exceeds its UTF-8 bound")
        return value

    def command(
        self, kind: ProductionCutoverKind, actor: str, input_digest: str
    ) -> ProductionCutoverCommand:
        return ProductionCutoverCommand(
            kind,
            RepositoryScope(self.installation_id, self.repository_id),
            self.operation_id,
            actor,
            self.reason,
            self.expected_revision,
            self.authority_id,
            input_digest,
        )


class ProductionStageBody(ProductionCommandBody):
    envelope: Annotated[Utf8Text, Field(min_length=1, max_length=262_144)]
    evidence: Annotated[Utf8Text, Field(min_length=1, max_length=MAX_PRODUCTION_STAGE_BYTES)]
    provider_paths: Annotated[
        list[Annotated[Utf8Text, Field(min_length=1, max_length=1024)]],
        Field(min_length=1, max_length=64),
    ] = Field(validation_alias="providerPaths")


class ProductionActivateBody(ProductionCommandBody):
    drain_envelope: Annotated[
        Utf8Text, Field(min_length=1, max_length=MAX_PRODUCTION_DRAIN_BYTES)
    ] = Field(validation_alias="drainEnvelope")


class ProductionScopeStateBody(ResponseModel):
    installation_id: int
    repository_id: int
    revision: int
    generation: int
    revoked_through_generation: int
    active_authority_id: str | None
    active_subject_digest: str | None
    staged_authority_id: str | None
    latch_override_id: str | None
    latch_applied_at: datetime | None

    @classmethod
    def from_state(cls, state: ProductionScopeState) -> ProductionScopeStateBody:
        return cls(
            installation_id=state.scope.installation_id,
            repository_id=state.scope.repository_id,
            revision=state.revision,
            generation=state.generation,
            revoked_through_generation=state.revoked_through_generation,
            active_authority_id=state.active_authority_id,
            active_subject_digest=state.active_subject_digest,
            staged_authority_id=state.staged_authority_id,
            latch_override_id=state.latch_override_id,
            latch_applied_at=state.latch_applied_at,
        )


class ProductionCutoverResultBody(ResponseModel):
    schema_version: Literal["ci-coordinator.production-cutover-result/v1"]
    ok: Literal[True]
    state: ProductionScopeStateBody
    duplicate: bool


class ProductionCutoverErrorBody(ResponseModel):
    ok: Literal[False]
    error: Literal[
        "forbidden",
        "invalid_evidence",
        "not_found",
        "unavailable",
        "unauthenticated",
        "overloaded",
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
