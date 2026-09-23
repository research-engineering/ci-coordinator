from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import hash_object
from ci_coordinator.plan_issuance.execution_contract import (
    FullCiExecution,
    SelectedExecution,
)
from ci_coordinator.plan_issuance.model import (
    SIGNED_PLAN_PAYLOAD_SCHEMA_VERSION,
    AuthenticatedRunBinding,
    IssuanceConflict,
    IssuanceRejected,
    IssuanceResult,
    Issued,
    IssuedPlanRecord,
    PlanRequest,
    RepositoryBinding,
    SignedPlanPayload,
)
from ci_coordinator.plan_issuance.signer import SignedPlanSigner
from ci_coordinator.plan_issuance.store import (
    IssuanceGuardRejected,
    IssuanceStore,
    IssuanceStoreUnavailable,
)
from ci_coordinator.plan_issuance.trusted_identity import bind_trusted_identity
from ci_coordinator.production_admission import (
    AuthorizedProductionAdmission,
    project_plan_subject,
)
from ci_coordinator.reconciliation import ReconciliationSubject
from ci_coordinator.repo_context import RepositoryEpoch
from ci_coordinator.runner_capacity import TrustedExecutionProjection
from ci_coordinator.verification_core import VerifiedPlan


@dataclass(frozen=True, slots=True)
class PlanIssuanceContext:
    repository: RepositoryBinding
    verified_plan: VerifiedPlan | None
    production_admission: AuthorizedProductionAdmission | None
    forced_fallback_reason: str | None = None
    execution_projection: TrustedExecutionProjection | None = None

    def __post_init__(self) -> None:
        if type(self.repository) is not RepositoryBinding:
            raise TypeError("issuance context requires an exact repository binding")
        if self.verified_plan is not None and type(self.verified_plan) is not VerifiedPlan:
            raise TypeError("issuance context requires an exact verified plan or none")
        if self.production_admission is not None and (
            type(self.production_admission) is not AuthorizedProductionAdmission
        ):
            raise TypeError("issuance context production admission must be verifier-owned")
        if self.forced_fallback_reason is not None and (
            type(self.forced_fallback_reason) is not str
            or not self.forced_fallback_reason
            or len(self.forced_fallback_reason) > 128
        ):
            raise ValueError("forced fallback reason must be bounded non-empty text")
        if self.execution_projection is not None:
            if type(self.execution_projection) is not TrustedExecutionProjection:
                raise TypeError("issuance context requires an exact execution projection")
            if (
                self.verified_plan is None
                or self.execution_projection.verified_plan_id
                != self.verified_plan.execution_plan_id
            ):
                raise ValueError("execution projection must bind the issuance verified plan")


class SignedPlanIssuer:
    def __init__(self, *, store: IssuanceStore, signer: SignedPlanSigner) -> None:
        self._store = store
        self._signer = signer

    async def issue(
        self,
        request: PlanRequest,
        identity: TrustedActionsRun,
        context: PlanIssuanceContext,
    ) -> IssuanceResult:
        binding_error = bind_trusted_identity(request, identity)
        if binding_error is not None:
            return IssuanceRejected(binding_error)
        if context.repository != _repository(request):
            return IssuanceRejected("plan_repository_not_configured")
        payload = _payload(request, identity, context)
        not_after = (
            context.production_admission.not_after
            if payload.verified_plan_id is not None and context.production_admission is not None
            else None
        )
        try:
            envelope = self._signer.sign(payload, not_after=not_after)
        except ValueError:
            fallback = _fallback_payload(
                request,
                identity,
                context.repository,
                "production_admission_expired",
            )
            envelope = self._signer.sign(fallback)
            payload = fallback
        mode = "selected" if payload.verified_plan_id is not None else "fallback"
        attempted = IssuedPlanRecord.create(
            _idempotency_key(identity, mode, payload),
            request,
            envelope,
        )
        guard = (
            context.production_admission.issuance_guard()
            if payload.verified_plan_id is not None and context.production_admission is not None
            else None
        )
        try:
            existing = await self._store.save(attempted, guard)
        except IssuanceStoreUnavailable:
            return IssuanceRejected("issuance_store_unavailable")
        if isinstance(existing, IssuanceGuardRejected):
            payload = _fallback_payload(
                request,
                identity,
                context.repository,
                "production_revalidation_failed",
            )
            envelope = self._signer.sign(payload)
            attempted = IssuedPlanRecord.create(
                _idempotency_key(identity, "fallback", payload),
                request,
                envelope,
            )
            try:
                existing = await self._store.save(attempted)
            except IssuanceStoreUnavailable:
                return IssuanceRejected("issuance_store_unavailable")
            if isinstance(existing, IssuanceGuardRejected):
                return IssuanceRejected("issuance_store_unavailable")
        if existing is None:
            return Issued(attempted, False)
        if existing.request_hash == attempted.request_hash:
            return Issued(existing, True)
        return IssuanceConflict(existing, attempted)


def _payload(
    request: PlanRequest,
    identity: TrustedActionsRun,
    context: PlanIssuanceContext,
) -> SignedPlanPayload:
    verified = context.verified_plan
    if (
        context.forced_fallback_reason is not None
        or verified is None
        or context.production_admission is None
        or verified.fallback.triggered
        or context.execution_projection is None
    ):
        reason = _fallback_reason(context, verified)
        return _fallback_payload(request, identity, context.repository, reason)
    if not _plan_binds_request(verified, request):
        return _fallback_payload(
            request,
            identity,
            context.repository,
            "verified_plan_request_mismatch",
        )
    if not context.production_admission.binds(
        project_plan_subject(
            RepositoryScope(request.installation_id, request.repository_id),
            verified,
            identity,
            context.execution_projection.target_registry_hash,
            _reconciliation_subject_id(request),
        )
    ):
        return _fallback_payload(
            request,
            identity,
            context.repository,
            "production_admission_mismatch",
        )
    execution = SelectedExecution.project(verified, context.execution_projection)
    return SignedPlanPayload(
        schema_version=SIGNED_PLAN_PAYLOAD_SCHEMA_VERSION,
        plan_id=verified.execution_plan_id,
        repository=context.repository,
        request=request,
        authenticated_run=_authenticated_run(identity),
        verified_plan_id=verified.execution_plan_id,
        production_admission_receipt_id=context.production_admission.authority_id,
        execution=execution,
        verifier_version="verification-core/v1",
        fallback_reason=None,
    )


def _fallback_reason(
    context: PlanIssuanceContext,
    verified: VerifiedPlan | None,
) -> str:
    if context.forced_fallback_reason is not None:
        return context.forced_fallback_reason
    if verified is None:
        return "verified_plan_unavailable"
    if context.production_admission is None:
        return "dynamic_enforcement_disabled"
    if context.execution_projection is None:
        return "selected_execution_unavailable"
    return verified.fallback.reason or "verified_plan_fallback"


def _fallback_payload(
    request: PlanRequest,
    identity: TrustedActionsRun,
    repository: RepositoryBinding,
    reason: str,
) -> SignedPlanPayload:
    return SignedPlanPayload(
        schema_version=SIGNED_PLAN_PAYLOAD_SCHEMA_VERSION,
        plan_id="fallback_plan_"
        + hash_object({"request": request.identity_mapping(), "reason": reason})[:32],
        repository=repository,
        request=request,
        authenticated_run=_authenticated_run(identity),
        verified_plan_id=None,
        production_admission_receipt_id=None,
        execution=FullCiExecution("full-ci", reason),
        verifier_version=None,
        fallback_reason=reason,
    )


def _idempotency_key(
    identity: TrustedActionsRun,
    mode: str,
    payload: SignedPlanPayload,
) -> str:
    return (
        "plan-issue:"
        + hash_object(
            {
                "repository": identity.repository,
                "repositoryId": identity.repository_id,
                "ref": identity.ref,
                "runId": identity.run_id,
                "runAttempt": identity.run_attempt,
                "workflowRef": identity.workflow_ref,
                "jobWorkflowRef": identity.job_workflow_ref,
                "mode": mode,
                "planId": payload.plan_id,
                "productionAdmissionReceiptId": payload.production_admission_receipt_id,
            }
        )[:32]
    )


def _plan_binds_request(verified: VerifiedPlan, request: PlanRequest) -> bool:
    request_epoch = RepositoryEpoch(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        owner=request.owner,
        name=request.repository,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
    )
    return verified.source_plan.repo_epoch_hash == hash_object(request_epoch.to_identity_mapping())


def _repository(request: PlanRequest) -> RepositoryBinding:
    return RepositoryBinding(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        owner=request.owner,
        repository=request.repository,
    )


def _reconciliation_subject_id(request: PlanRequest) -> str:
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


def _authenticated_run(identity: TrustedActionsRun) -> AuthenticatedRunBinding:
    return AuthenticatedRunBinding(
        issuer=identity.issuer,
        audience=identity.audience,
        repository=identity.repository,
        repository_id=identity.repository_id,
        ref=identity.ref,
        execution_sha=identity.execution_sha,
        run_id=identity.run_id,
        run_attempt=identity.run_attempt,
        event_name=identity.event_name,
        workflow_ref=identity.workflow_ref,
        workflow_sha=identity.workflow_sha,
        job_workflow_ref=identity.job_workflow_ref,
        job_workflow_sha=identity.job_workflow_sha,
        check_run_id=identity.check_run_id,
        verified_at=identity.verified_at,
        verifier_version=identity.verifier_version,
        claim_hash=identity.claim_hash,
    )
