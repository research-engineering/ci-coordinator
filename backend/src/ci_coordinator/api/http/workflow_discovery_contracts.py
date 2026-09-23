"""Served workflow-discovery response contract."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.api.http.model_contracts import ProjectedResponseModel, ResponseModel
from ci_coordinator.workflow_discovery.evidence import FactValue


class DiscoveryScopeResponse(ProjectedResponseModel):
    installation_id: int
    repository_id: int


class DiscoveryRepositoryResponse(ProjectedResponseModel):
    scope: DiscoveryScopeResponse
    owner: str
    name: str
    default_branch: str


class SourceResponse(ProjectedResponseModel):
    path: str
    blob_sha: str
    size: int


class PermissionResponse(ProjectedResponseModel):
    name: str
    level: str


class PermissionsResponse(ProjectedResponseModel):
    kind: Literal["absent", "all", "entries"]
    all_level: str | None
    entries: tuple[PermissionResponse, ...]


class ConcurrencyResponse(ProjectedResponseModel):
    group: str | None
    cancel_in_progress: bool | None


class MatrixDimensionResponse(ProjectedResponseModel):
    name: str
    values: tuple[str | int | float | bool | None, ...]


class StepResponse(ProjectedResponseModel):
    index: int
    name: str | None
    uses: str | None
    uses_dynamic: bool
    has_run: bool
    condition: str | None


class JobResponse(ProjectedResponseModel):
    subject_id: str
    job_id: str
    name: str | None
    needs: tuple[str, ...] | None
    uses: str | None
    uses_dynamic: bool
    runs_on: tuple[str, ...] | None
    permissions: PermissionsResponse | None
    environment: str | None
    service_ids: tuple[str, ...] | None
    timeout_minutes: int | None
    concurrency: ConcurrencyResponse | None
    matrix: tuple[MatrixDimensionResponse, ...] | None
    condition: str | None
    steps: tuple[StepResponse, ...] | None
    static_secret_names: tuple[str, ...] | None
    provider_signal_name: str | None


class WorkflowResponse(ProjectedResponseModel):
    subject_id: str
    path: str
    name: str | None
    triggers: tuple[str, ...] | None
    permissions: PermissionsResponse | None
    concurrency: ConcurrencyResponse | None
    static_secret_names: tuple[str, ...] | None
    jobs: tuple[JobResponse, ...]


class YamlLocationResponse(ProjectedResponseModel):
    path: str
    line: int
    column: int


class ProvenanceResponse(ProjectedResponseModel):
    scope: DiscoveryScopeResponse
    revision: str
    workflow_path: str
    blob_sha: str
    parser_version: str
    location: YamlLocationResponse


class FactResponse(ProjectedResponseModel):
    fact_id: str
    subject_id: str
    category: Literal["identity", "invocation", "graph", "authority", "execution", "semantics"]
    field: str
    value: FactValue
    criticality: Literal["informational", "safety"]
    provenance: ProvenanceResponse


class UnknownResponse(ProjectedResponseModel):
    unknown_id: str
    subject_id: str
    category: Literal["identity", "invocation", "graph", "authority", "execution", "semantics"]
    field: str
    reason: str
    criticality: Literal["informational", "safety"]
    provenance: ProvenanceResponse
    observed_syntax: str | None


class CallEdgeResponse(ProjectedResponseModel):
    edge_id: str
    caller_workflow_path: str
    caller_job_id: str
    uses: str
    kind: Literal["local", "remote", "dynamic", "unknown"]
    status: Literal[
        "resolved",
        "missing",
        "remote",
        "dynamic",
        "invalid",
        "cycle",
        "depth_exceeded",
    ]
    target_path: str | None
    remote_ref: str | None
    remote_ref_immutable: bool | None
    provenance: ProvenanceResponse


class ProposalDiagnosticResponse(ProjectedResponseModel):
    code: str
    phase: str
    rule_id: str
    instance_pointer: str


class ProposalResponse(ResponseModel):
    manifest_id: str = Field(pattern=r"^proposal:[0-9a-f]{32}$")
    state: Literal["reviewable", "blocked"]
    generator_version: Literal["workflow-discovery-proposal/v2"]
    inventory_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_workflow_path: str | None
    selected_job_id: str | None
    selected_job_name: str | None
    selected_events: tuple[Literal["merge_group", "pull_request", "push"], ...] = Field(
        max_length=3,
    )
    policy_source: str | None
    admitted_epoch_id: str | None = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    diagnostics: tuple[ProposalDiagnosticResponse, ...]
    blockers: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    non_claims: tuple[str, ...]


class AdoptionAssessmentResponse(ProjectedResponseModel):
    workflow_path: str
    state: Literal[
        "in_place_job_set",
        "reusable_workflow_set",
        "witness_shards",
        "full_only",
        "invalid",
    ]
    recommended_adapter: Literal[
        "in_place_job_set",
        "reusable_workflow_set",
        "full_only",
        "none",
    ]
    blockers: tuple[str, ...]
    required_owner_inputs: tuple[str, ...]
    inventory_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    non_claims: tuple[str, ...]


class AdoptionTargetProjectionResponse(ResponseModel):
    status: Literal["absent", "available", "invalid", "unavailable"]
    registry_hash: str | None = Field(
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def require_status_identity_coherence(self) -> Self:
        if (self.status == "available") != (self.registry_hash is not None):
            raise ValueError("target projection status and registry hash are incoherent")
        return self


class WorkflowDiscoveryResponse(ResponseModel):
    ok: Literal[True]
    repository: DiscoveryRepositoryResponse
    revision: str
    parser_version: str
    complete: bool
    local_graph_closed: bool
    inventory_digest: str
    sources: tuple[SourceResponse, ...] = Field(max_length=64)
    workflows: tuple[WorkflowResponse, ...] = Field(max_length=64)
    call_edges: tuple[CallEdgeResponse, ...] = Field(max_length=4_096)
    facts: tuple[FactResponse, ...] = Field(max_length=32_768)
    unknowns: tuple[UnknownResponse, ...] = Field(max_length=32_768)
    non_claims: tuple[str, ...]
    proposal: ProposalResponse
    target_projection: AdoptionTargetProjectionResponse
    adoption_assessments: tuple[AdoptionAssessmentResponse, ...] = Field(
        max_length=64,
    )


type WorkflowDiscoveryErrorCode = Literal[
    "forbidden",
    "invalid_revision",
    "malformed_provider_response",
    "not_found",
    "overloaded",
    "provider_binding_mismatch",
    "rate_limited",
    "report_limit_exceeded",
    "source_limit_exceeded",
    "source_tree_limit_exceeded",
    "unauthenticated",
    "unavailable",
]


class WorkflowDiscoveryErrorResponse(ResponseModel):
    ok: Literal[False]
    error: WorkflowDiscoveryErrorCode
