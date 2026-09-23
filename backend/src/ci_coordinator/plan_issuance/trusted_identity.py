from __future__ import annotations

from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.plan_issuance.model import PlanRequest


def bind_trusted_identity(request: PlanRequest, identity: TrustedActionsRun) -> str | None:
    if identity.repository != request.owner + "/" + request.repository:
        return "plan_identity_repository_mismatch"
    if identity.repository_id != request.repository_id:
        return "plan_identity_repository_id_mismatch"
    if identity.ref != request.ref:
        return "plan_identity_ref_mismatch"
    if identity.run_id != request.workflow_run_id or identity.run_attempt != request.run_attempt:
        return "plan_identity_run_mismatch"
    if identity.event_name != request.event_name:
        return "plan_identity_event_mismatch"
    if identity.execution_sha != request.execution_sha:
        return "plan_identity_execution_sha_mismatch"
    return None
