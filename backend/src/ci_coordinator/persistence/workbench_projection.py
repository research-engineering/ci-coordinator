"""Integrity-checked redaction from persistence rows to workbench views."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal

from ci_coordinator.audit_replay import verify_audit_event_integrity
from ci_coordinator.persistence.audit_codec import row_to_record
from ci_coordinator.persistence.issued_plan_codec import decode_record
from ci_coordinator.persistence.operator_override_codec import decode_operator_override_row
from ci_coordinator.persistence.reconciliation_state_codec import (
    decode_result_row,
    decode_subject_row,
)
from ci_coordinator.persistence.runtime_state_profile import RuntimeIngressIssuanceStateProfile
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.plan_issuance import (
    FullCiExecution,
    SelectedExecution,
    SignedNativeProfileExecution,
    SignedProfileExecution,
)
from ci_coordinator.workbench_read_models import (
    AuditEventView,
    ConfigEpochView,
    NativeProfileExecutionView,
    OverrideView,
    PlanView,
    ProfileCapacityView,
    RunFindingView,
    RunView,
    ShardedProfileCapacityView,
)


def project_plans(
    rows: Sequence[Mapping[str, object]],
    profile: RuntimeIngressIssuanceStateProfile,
) -> tuple[PlanView, ...]:
    return tuple(_plan_view(row, profile) for row in rows)


def project_runs(
    rows: Sequence[Mapping[str, object]],
    profile: ShadowReconciliationStateProfile,
    observed_at: datetime,
) -> tuple[RunView, ...]:
    return tuple(_run_view(row, profile, observed_at) for row in rows)


def project_overrides(
    rows: Sequence[Mapping[str, object]],
    observed_at: datetime,
) -> tuple[OverrideView, ...]:
    result: list[OverrideView] = []
    for row in rows:
        stored = decode_operator_override_row(row)
        override = stored.override
        command = override.command
        result.append(
            OverrideView(
                override_id=override.override_id,
                kind=command.kind,
                subject_id=command.subject_id,
                operation_id=command.operation_id,
                actor=command.actor,
                reason=command.reason,
                applied_at=override.applied_at,
                expires_at=command.expires_at,
                active=override.applies_at(observed_at),
                audit_event_id=stored.audit_event_id,
            )
        )
    return tuple(result)


def project_config_epochs(
    rows: Sequence[Mapping[str, object]],
) -> tuple[ConfigEpochView, ...]:
    return tuple(
        ConfigEpochView(
            epoch_id=_digest(row.get("epoch_id"), "config epoch id"),
            source_format=_source_format(row.get("source_format")),
            source_hash=_digest(row.get("source_hash"), "config source hash"),
            document_hash=_digest(row.get("document_hash"), "config document hash"),
            epoch_hash=_digest(row.get("epoch_hash"), "config epoch hash"),
            document_schema_id=_text(row.get("document_schema_id"), "document schema id"),
            document_profile_id=_text(row.get("document_profile_id"), "document profile id"),
            semantic_profile_id=_text(row.get("semantic_profile_id"), "semantic profile id"),
            compiled_schema_id=_text(row.get("compiled_schema_id"), "compiled schema id"),
            active=row.get("active_revision") is not None,
            active_revision=_optional_positive(row.get("active_revision"), "active revision"),
        )
        for row in rows
    )


def project_audit_events(
    rows: Sequence[Mapping[str, object]],
) -> tuple[AuditEventView, ...]:
    result: list[AuditEventView] = []
    for row in rows:
        record = row_to_record(row)
        invalid = verify_audit_event_integrity(record)
        if invalid is not None:
            raise ValueError("stored workbench audit event fails integrity verification")
        result.append(
            AuditEventView(
                sequence=record.sequence,
                audit_event_id=record.audit_event_id,
                subject_type=record.subject_type,
                subject_id=record.subject_id,
                event_type=record.event_type,
                created_at=record.created_at,
                actor=record.actor,
                payload=record.payload,
                payload_hash=record.payload_hash,
                previous_event_hash=record.previous_event_hash,
                event_hash=record.event_hash,
            )
        )
    return tuple(result)


def _plan_view(
    row: Mapping[str, object],
    profile: RuntimeIngressIssuanceStateProfile,
) -> PlanView:
    record = decode_record(row, profile)
    envelope = record.envelope
    payload = envelope.payload
    request = payload.request
    execution = payload.execution
    if isinstance(execution, SelectedExecution):
        profiles = tuple(_profile_view(item) for item in execution.profiles)
        selected_ids = execution.selected_obligation_ids
        omitted_ids = execution.omitted_obligation_ids
        witness_ids = execution.selected_witness_ids
        manifest_id = execution.test_manifest_id
        catalog_hash = execution.catalog_hash
        target_registry_hash = execution.target_registry_hash
    elif isinstance(execution, FullCiExecution):
        profiles = ()
        selected_ids = ()
        omitted_ids = ()
        witness_ids = ()
        manifest_id = None
        catalog_hash = None
        target_registry_hash = None
    else:
        raise ValueError("stored issued-plan execution is unsupported")
    return PlanView(
        record_id=record.record_id,
        plan_id=payload.plan_id,
        issued_at=envelope.issued_at,
        expires_at=envelope.expires_at,
        request_id=request.request_id,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
        workflow_run_id=request.workflow_run_id,
        run_attempt=request.run_attempt,
        execution_mode=execution.mode,
        verified_plan_id=payload.verified_plan_id,
        production_admission_receipt_id=payload.production_admission_receipt_id,
        selected_obligation_ids=selected_ids,
        omitted_obligation_ids=omitted_ids,
        selected_witness_ids=witness_ids,
        test_manifest_id=manifest_id,
        catalog_hash=catalog_hash,
        target_registry_hash=target_registry_hash,
        profiles=profiles,
        fallback_reason=payload.fallback_reason,
    )


def _profile_view(
    value: SignedProfileExecution | SignedNativeProfileExecution,
) -> ProfileCapacityView:
    if type(value) is SignedNativeProfileExecution:
        return NativeProfileExecutionView(
            profile_id=value.profile.profile_id,
            execution_kind="native-job-set",
            job_id=value.job_id,
            witness_count=len(value.witness_ids),
        )
    if type(value) is not SignedProfileExecution:
        raise ValueError("stored selected execution profile is unsupported")
    return ShardedProfileCapacityView(
        profile_id=value.profile.profile_id,
        execution_kind="witness-shards",
        shard_ids=tuple(shard.shard_id for shard in value.shards),
        witness_count=len(
            {witness_id for shard in value.shards for witness_id in shard.witness_ids}
        ),
        test_count=sum(len(shard.test_ids) for shard in value.shards),
        max_parallel=value.max_parallel,
        capacity_mode=value.capacity_mode,
        capacity_reason=value.capacity_reason,
    )


def _run_view(
    row: Mapping[str, object],
    profile: ShadowReconciliationStateProfile,
    observed_at: datetime,
) -> RunView:
    subject, contract, revision, convergence = decode_subject_row(dict(row), profile)
    result_revision = row.get("result_revision")
    if result_revision is None:
        state: Literal["pending", "success", "failure", "conflict"] = "pending"
        findings: tuple[RunFindingView, ...] = ()
    else:
        result_row = {
            "subject_id": subject.subject_id,
            "revision": result_revision,
            "result_canonical_json": row.get("result_canonical_json"),
            "semantic_hash": row.get("result_semantic_hash"),
        }
        stored_revision, result = decode_result_row(result_row, subject, profile)
        if stored_revision != revision:
            raise ValueError("stored workbench run result revision is inconsistent")
        state = result.state
        findings = tuple(
            RunFindingView(item.kind, item.signal_id, item.message) for item in result.findings
        )
    lease_expires_at = convergence.lease_expires_at
    return RunView(
        subject_id=subject.subject_id,
        event_name=subject.event_name,
        ref=subject.ref,
        base_sha=subject.base_sha,
        head_sha=subject.head_sha,
        workflow_run_id=subject.workflow_run_id,
        run_attempt=subject.run_attempt,
        revision=revision,
        state=state,
        created_at=convergence.created_at,
        deadline_at=convergence.deadline_at,
        next_attempt_at=convergence.next_attempt_at,
        attempt_count=convergence.attempt_count,
        max_attempts=convergence.max_attempts,
        claim_generation=convergence.claim_generation,
        lease_active=lease_expires_at is not None and lease_expires_at > observed_at,
        lease_expires_at=lease_expires_at,
        contract_hash=contract.contract_hash,
        findings=findings,
    )


def _text(value: object, context: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"stored {context} is invalid")
    return value


def _digest(value: object, context: str) -> str:
    text = _text(value, context)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"stored {context} is invalid")
    return text


def _source_format(value: object) -> Literal["json", "yaml-1.2"]:
    if value not in {"json", "yaml-1.2"}:
        raise ValueError("stored config source format is invalid")
    return value


def _optional_positive(value: object, context: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError(f"stored {context} is invalid")
    return value
