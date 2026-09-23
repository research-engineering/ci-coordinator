from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.plan_issuance.execution_contract import SelectedExecution
from ci_coordinator.plan_issuance.model import IssuedPlanRecord
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.reconciliation import ReconciliationSubject


class IssuanceStoreUnavailable(RuntimeError):
    """The durable idempotency comparison could not complete."""


@dataclass(frozen=True, slots=True)
class IssuanceGuardRejected:
    reason: Literal[
        "authority_expired",
        "authority_not_registered",
        "authority_generation_changed",
        "current_evidence_invalid",
        "config_epoch_changed",
        "override_active",
        "plan_expired",
        "production_binding_invalid",
    ]


type IssuanceSaveResult = IssuedPlanRecord | IssuanceGuardRejected | None


class IssuanceStore(Protocol):
    async def save(
        self,
        attempted: IssuedPlanRecord,
        guard: ProductionIssuanceGuard | None = None,
    ) -> IssuanceSaveResult: ...


class InMemoryIssuanceStore:
    def __init__(self) -> None:
        self._records: dict[str, IssuedPlanRecord] = {}

    async def save(
        self,
        attempted: IssuedPlanRecord,
        guard: ProductionIssuanceGuard | None = None,
    ) -> IssuanceSaveResult:
        selected = attempted.envelope.payload.verified_plan_id is not None
        if selected != (guard is not None) or (
            guard is not None and not production_guard_binds_record(guard, attempted)
        ):
            return IssuanceGuardRejected("production_binding_invalid")
        existing = self._records.get(attempted.idempotency_key)
        if existing is None:
            self._records[attempted.idempotency_key] = attempted
            return None
        return existing


def production_guard_binds_record(
    guard: ProductionIssuanceGuard,
    record: IssuedPlanRecord,
) -> bool:
    if type(guard) is not ProductionIssuanceGuard or type(record) is not IssuedPlanRecord:
        return False
    payload = record.envelope.payload
    execution = payload.execution
    return (
        type(execution) is SelectedExecution
        and payload.verified_plan_id == guard.execution_plan_id
        and payload.production_admission_receipt_id == guard.authority_id
        and execution.catalog_hash == guard.catalog_hash
        and execution.target_registry_hash == guard.target_registry_hash
        and payload.authenticated_run.workflow_ref == guard.workflow_ref
        and payload.authenticated_run.job_workflow_ref == guard.job_workflow_ref
        and _reconciliation_subject_id(record) == guard.reconciliation_subject_id
        and record.envelope.expires_at <= guard.not_after
        and RepositoryScope(
            payload.repository.installation_id,
            payload.repository.repository_id,
        )
        == guard.scope
    )


def _reconciliation_subject_id(record: IssuedPlanRecord) -> str:
    request = record.envelope.payload.request
    return ReconciliationSubject.create(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
        workflow_run_id=request.workflow_run_id,
        run_attempt=request.run_attempt,
    ).subject_id
