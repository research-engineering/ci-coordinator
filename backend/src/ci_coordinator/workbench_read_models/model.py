"""Redacted repository-scoped projections for the operator workbench."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.config_control import RepositoryScope

type WorkbenchReplayStatus = Literal["valid", "in_progress", "invalid", "unavailable"]
type WorkbenchRunState = Literal["pending", "success", "failure", "conflict"]
MAX_WORKBENCH_SECTION_ITEMS = 20


@dataclass(frozen=True, slots=True)
class ShardedProfileCapacityView:
    profile_id: str
    execution_kind: Literal["witness-shards"]
    shard_ids: tuple[str, ...]
    witness_count: int
    test_count: int
    max_parallel: int
    capacity_mode: Literal["optimized", "conservative"]
    capacity_reason: str | None


@dataclass(frozen=True, slots=True)
class NativeProfileExecutionView:
    profile_id: str
    execution_kind: Literal["native-job-set"]
    job_id: str
    witness_count: int


type ProfileCapacityView = ShardedProfileCapacityView | NativeProfileExecutionView


@dataclass(frozen=True, slots=True)
class PlanView:
    record_id: str
    plan_id: str
    issued_at: datetime
    expires_at: datetime
    request_id: str
    event_name: str
    ref: str
    base_sha: str
    head_sha: str
    workflow_run_id: int
    run_attempt: int
    execution_mode: Literal["selected", "full-ci"]
    verified_plan_id: str | None
    production_admission_receipt_id: str | None
    selected_obligation_ids: tuple[str, ...]
    omitted_obligation_ids: tuple[str, ...]
    selected_witness_ids: tuple[str, ...]
    test_manifest_id: str | None
    catalog_hash: str | None
    target_registry_hash: str | None
    profiles: tuple[ProfileCapacityView, ...]
    fallback_reason: str | None


@dataclass(frozen=True, slots=True)
class RunFindingView:
    kind: str
    signal_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class RunView:
    subject_id: str
    event_name: str
    ref: str
    base_sha: str
    head_sha: str
    workflow_run_id: int
    run_attempt: int
    revision: int
    state: WorkbenchRunState
    created_at: datetime
    deadline_at: datetime
    next_attempt_at: datetime
    attempt_count: int
    max_attempts: int
    claim_generation: int
    lease_active: bool
    lease_expires_at: datetime | None
    contract_hash: str
    findings: tuple[RunFindingView, ...]


@dataclass(frozen=True, slots=True)
class OverrideView:
    override_id: str
    kind: Literal["force_full_ci", "disable_omission", "enable_omission"]
    subject_id: str | None
    operation_id: str
    actor: str
    reason: str
    applied_at: datetime
    expires_at: datetime | None
    active: bool
    audit_event_id: str


@dataclass(frozen=True, slots=True)
class ConfigEpochView:
    epoch_id: str
    source_format: Literal["json", "yaml-1.2"]
    source_hash: str
    document_hash: str
    epoch_hash: str
    document_schema_id: str
    document_profile_id: str
    semantic_profile_id: str
    compiled_schema_id: str
    active: bool
    active_revision: int | None


@dataclass(frozen=True, slots=True)
class AuditEventView:
    sequence: int
    audit_event_id: str
    subject_type: str
    subject_id: str
    event_type: str
    created_at: str
    actor: str
    payload: JsonValue
    payload_hash: str
    previous_event_hash: str | None
    event_hash: str


@dataclass(frozen=True, slots=True)
class ReplayView:
    status: WorkbenchReplayStatus
    snapshot_revision: int
    verified_revision: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class TruncationView:
    plans: bool
    runs: bool
    overrides: bool
    config_epochs: bool
    audit_events: bool


@dataclass(frozen=True, slots=True)
class RepositoryDataSnapshot:
    scope: RepositoryScope
    observed_at: datetime
    ledger_revision: int
    plans: tuple[PlanView, ...]
    runs: tuple[RunView, ...]
    overrides: tuple[OverrideView, ...]
    config_epochs: tuple[ConfigEpochView, ...]
    audit_events: tuple[AuditEventView, ...]
    truncated: TruncationView


@dataclass(frozen=True, slots=True)
class RepositoryWorkbenchSnapshot:
    data: RepositoryDataSnapshot
    replay: ReplayView


@dataclass(frozen=True, slots=True)
class WorkbenchForbidden:
    pass


@dataclass(frozen=True, slots=True)
class WorkbenchUnavailable:
    pass


type WorkbenchResult = RepositoryWorkbenchSnapshot | WorkbenchForbidden | WorkbenchUnavailable
