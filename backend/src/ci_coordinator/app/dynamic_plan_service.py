"""Application orchestration for conservative signed-plan issuance."""

from __future__ import annotations

import asyncio
from typing import Protocol

from ci_coordinator.app.capacity_planning import ExecutionPlanningResult
from ci_coordinator.app.dynamic_plan import DynamicPlanCommand
from ci_coordinator.app.override_resolution import (
    PlanningOverrideDecision,
    PlanningOverrideResolver,
)
from ci_coordinator.app.reconciliation_registration import (
    ReconciliationRegistrar,
    reconciliation_subject,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpochSnapshot
from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.identity_admission import TrustedActionsRun, workflow_path_from_ref
from ci_coordinator.observability import (
    PlanningStage,
    PlanningUnavailabilityReason,
    RuntimeDiagnosticObserver,
    RuntimeDiagnosticStage,
    RuntimeMetrics,
)
from ci_coordinator.plan_issuance import (
    IssuanceResult,
    PlanIssuanceContext,
    PlanRequest,
    RepositoryBinding,
    SelectedExecution,
    SignedProfileExecution,
)
from ci_coordinator.production_admission import (
    AuthorizedProductionAdmission,
    project_candidate_subject,
    project_plan_subject,
)
from ci_coordinator.production_admission.request_authority import (
    PreparedProductionAuthority,
    ProductionRequestAuthority,
)
from ci_coordinator.runner_capacity import TrustedExecutionProjection
from ci_coordinator.verification_core import VerifiedPlan


class ActiveConfigEpochResolver(Protocol):
    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None: ...


class CandidatePlanProvider(Protocol):
    async def build_candidate(
        self,
        command: DynamicPlanCommand,
        active_epoch: ActiveConfigEpochSnapshot,
    ) -> VerifiedPlan | None: ...


class ExecutionPlanProvider(Protocol):
    async def project(
        self,
        verified_plan: VerifiedPlan,
        request: PlanRequest,
        execution_authority_sha: str | None,
    ) -> ExecutionPlanningResult | None: ...


class PlanIssuer(Protocol):
    async def issue(
        self,
        request: PlanRequest,
        identity: TrustedActionsRun,
        context: PlanIssuanceContext,
    ) -> IssuanceResult: ...


class DynamicPlanService:
    """Resolve fresh inputs, then issue only through the admitted signer/store."""

    def __init__(
        self,
        *,
        active_epochs: ActiveConfigEpochResolver,
        candidates: CandidatePlanProvider,
        issuer: PlanIssuer,
        reconciliation: ReconciliationRegistrar | None = None,
        overrides: PlanningOverrideResolver | None = None,
        capacity: ExecutionPlanProvider | None = None,
        enforcement_authority: ProductionRequestAuthority | None = None,
        runtime_metrics: RuntimeMetrics | None = None,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        if enforcement_authority is not None and reconciliation is None:
            raise ValueError("enforcing plan service requires durable reconciliation")
        self._active_epochs = active_epochs
        self._candidates = candidates
        self._issuer = issuer
        self._reconciliation = reconciliation
        self._overrides = overrides
        self._capacity = capacity
        self._enforcement_authority = enforcement_authority
        self._runtime_metrics = runtime_metrics
        self._diagnostics = diagnostics

    async def request_dynamic_plan(self, command: DynamicPlanCommand) -> IssuanceResult:
        request = command.request
        scope = RepositoryScope(request.installation_id, request.repository_id)
        reconciliation_subject_id = reconciliation_subject(command).subject_id
        override = await self._resolve_override(command)
        active_epoch = (
            None
            if override is not None and override.force_full_ci
            else await self._load_active_epoch(scope)
        )
        verified_plan = (
            None
            if active_epoch is None
            else await self._candidates.build_candidate(command, active_epoch)
        )
        prepared_authority = await self._prepare_authority(scope, verified_plan, command.identity)
        candidate_admitted = prepared_authority is not None
        execution_planning = await self._execution_planning(
            verified_plan,
            request,
            execution_authority_sha=command.identity.workflow_sha,
        )
        if (
            execution_planning is not None
            and workflow_path_from_ref(
                repository=command.identity.repository,
                workflow_ref=command.identity.workflow_ref,
            )
            != execution_planning.target.workflow_path
        ):
            self._observe_unavailable("capacity", "capacity_workflow_run_mismatch")
            execution_planning = None
        if (
            execution_planning is not None
            and not execution_planning.target.admits_plan_request_identity(
                job_workflow_ref=command.identity.job_workflow_ref,
                job_workflow_sha=command.identity.job_workflow_sha,
                repository=command.identity.repository,
                workflow_ref=command.identity.workflow_ref,
                workflow_sha=command.identity.workflow_sha,
            )
        ):
            self._observe_unavailable("capacity", "capacity_plan_request_identity_mismatch")
            execution_planning = None
        projected_execution = None if execution_planning is None else execution_planning.execution
        authorization = (
            self._authorize(
                scope,
                verified_plan,
                command.identity,
                projected_execution,
                reconciliation_subject_id,
                prepared_authority,
            )
            if candidate_admitted
            else None
        )
        execution_projection = projected_execution if authorization is not None else None
        if (
            verified_plan is not None
            and execution_projection is not None
            and self._reconciliation is not None
        ):
            provider_signals = _selected_provider_signals(
                verified_plan,
                execution_projection,
            )
            if provider_signals is None:
                self._observe_unavailable(
                    "selected_execution",
                    "selected_execution_projection_invalid",
                )
                verified_plan = None
                execution_projection = None
            elif not await self._register_reconciliation(
                command,
                verified_plan,
                full_ci=False,
                provider_signals=provider_signals,
                authorization=authorization,
            ):
                verified_plan = None
                execution_projection = None
        elif (
            verified_plan is not None
            and verified_plan.omitted_obligations
            and self._reconciliation is not None
            and execution_planning is not None
        ):
            await self._register_reconciliation(
                command,
                verified_plan,
                full_ci=True,
                provider_signals=(execution_planning.target.provider_signal,),
            )
        return await self._issuer.issue(
            request,
            command.identity,
            PlanIssuanceContext(
                repository=RepositoryBinding(
                    installation_id=request.installation_id,
                    repository_id=request.repository_id,
                    owner=request.owner,
                    repository=request.repository,
                ),
                verified_plan=verified_plan,
                production_admission=self._reauthorize(
                    scope,
                    verified_plan,
                    command.identity,
                    execution_projection,
                    authorization,
                    reconciliation_subject_id,
                    prepared_authority,
                ),
                forced_fallback_reason=(
                    override.reason
                    if override is not None
                    else "selected_execution_unavailable"
                    if candidate_admitted and execution_projection is None
                    else None
                ),
                execution_projection=execution_projection,
            ),
        )

    async def _resolve_override(
        self,
        command: DynamicPlanCommand,
    ) -> PlanningOverrideDecision | None:
        if self._overrides is None:
            return None
        try:
            return await self._overrides.resolve(command)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._observe_unavailable("override", "override_exception")
            self._observe_exception("planning_override", error)
            return PlanningOverrideDecision(True, "override_state_unavailable")

    async def _load_active_epoch(
        self,
        scope: RepositoryScope,
    ) -> ActiveConfigEpochSnapshot | None:
        try:
            return await self._active_epochs.load_active(scope)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._observe_unavailable("active_epoch", "active_epoch_exception")
            self._observe_exception("active_config_epoch", error)
            return None

    async def _execution_planning(
        self,
        verified_plan: VerifiedPlan | None,
        request: PlanRequest,
        *,
        execution_authority_sha: str | None,
    ) -> ExecutionPlanningResult | None:
        if (
            verified_plan is None
            or verified_plan.fallback.triggered
            or not verified_plan.selected_witnesses
            or self._capacity is None
            or (self._enforcement_authority is None and self._reconciliation is None)
        ):
            return None
        try:
            projected = await self._capacity.project(
                verified_plan,
                request,
                execution_authority_sha,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._observe_unavailable("capacity", "capacity_exception")
            self._observe_exception("capacity_projection", error)
            return None
        if projected is not None and type(projected) is not ExecutionPlanningResult:
            self._observe_unavailable("capacity", "capacity_result_invalid")
            return None
        return projected

    async def _prepare_authority(
        self,
        scope: RepositoryScope,
        verified_plan: VerifiedPlan | None,
        identity: TrustedActionsRun,
    ) -> PreparedProductionAuthority | None:
        if self._enforcement_authority is None or verified_plan is None:
            return None
        candidate = project_candidate_subject(scope, verified_plan, identity)
        try:
            admitted = await self._enforcement_authority.prepare(candidate)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._observe_exception("production_preauthorization", error)
            admitted = None
        if type(admitted) is not PreparedProductionAuthority:
            self._observe_unavailable("authority", "authority_not_admitted")
            return None
        return admitted

    def _authorize(
        self,
        scope: RepositoryScope,
        verified_plan: VerifiedPlan | None,
        identity: TrustedActionsRun,
        execution_projection: TrustedExecutionProjection | None,
        reconciliation_subject_id: str,
        prepared_authority: PreparedProductionAuthority | None,
    ) -> AuthorizedProductionAdmission | None:
        if prepared_authority is None or verified_plan is None or execution_projection is None:
            return None
        subject = project_plan_subject(
            scope,
            verified_plan,
            identity,
            execution_projection.target_registry_hash,
            reconciliation_subject_id,
        )
        try:
            authorization = prepared_authority.authorize(subject)
        except Exception as error:
            self._observe_exception("production_authorization", error)
            authorization = None
        if type(authorization) is not AuthorizedProductionAdmission:
            self._observe_unavailable("authority", "authority_not_admitted")
            return None
        return authorization

    def _reauthorize(
        self,
        scope: RepositoryScope,
        verified_plan: VerifiedPlan | None,
        identity: TrustedActionsRun,
        execution_projection: TrustedExecutionProjection | None,
        previous: AuthorizedProductionAdmission | None,
        reconciliation_subject_id: str,
        prepared_authority: PreparedProductionAuthority | None,
    ) -> AuthorizedProductionAdmission | None:
        if previous is None:
            return None
        current = self._authorize(
            scope,
            verified_plan,
            identity,
            execution_projection,
            reconciliation_subject_id,
            prepared_authority,
        )
        if current is None or current.authority_id != previous.authority_id:
            return None
        return current

    async def _register_reconciliation(
        self,
        command: DynamicPlanCommand,
        verified_plan: VerifiedPlan,
        *,
        full_ci: bool,
        provider_signals: tuple[ProviderSignal, ...],
        authorization: AuthorizedProductionAdmission | None = None,
    ) -> bool:
        if self._reconciliation is None:
            return False
        try:
            registered = await self._reconciliation.register(
                command,
                verified_plan,
                full_ci=full_ci,
                provider_signals=provider_signals,
                production_guard=None if authorization is None else authorization.issuance_guard(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._observe_exception("reconciliation_registration", error)
            registered = False
        if registered is not True:
            self._observe_unavailable(
                "reconciliation",
                "reconciliation_registration_unavailable",
            )
            return False
        return True

    def _observe_unavailable(
        self,
        stage: PlanningStage,
        reason: PlanningUnavailabilityReason,
    ) -> None:
        if self._runtime_metrics is not None:
            self._runtime_metrics.planning_unavailable(stage, reason)

    def _observe_exception(
        self,
        stage: RuntimeDiagnosticStage,
        error: BaseException,
    ) -> None:
        if self._diagnostics is not None:
            self._diagnostics.unexpected_failure(stage, error)


def _selected_provider_signals(
    verified_plan: VerifiedPlan,
    projection: TrustedExecutionProjection,
) -> tuple[ProviderSignal, ...] | None:
    try:
        execution = SelectedExecution.project(verified_plan, projection)
    except (TypeError, ValueError):
        return None
    if execution.execution_kind == "native-job-set":
        return (execution.gate_provider_signal,)
    return tuple(
        shard.provider_signal
        for profile in execution.profiles
        if type(profile) is SignedProfileExecution
        for shard in profile.shards
    )
