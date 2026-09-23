"""Durable registration of the provider state expected for an issued plan."""

from __future__ import annotations

from typing import Protocol

from ci_coordinator.app.dynamic_plan import DynamicPlanCommand
from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.production_admission.ports import ProductionRegistrationPersistence
from ci_coordinator.reconciliation import (
    CandidateEvidenceContext,
    OmittedSignal,
    PlanningEvidenceContext,
    ReconciliationContract,
    ReconciliationPersistence,
    ReconciliationSubject,
    SubjectRegistered,
    SubjectRegistrationDuplicate,
)
from ci_coordinator.verification_core import VerifiedPlan


class ReconciliationRegistrar(Protocol):
    async def register(
        self,
        command: DynamicPlanCommand,
        verified_plan: VerifiedPlan,
        *,
        full_ci: bool,
        provider_signals: tuple[ProviderSignal, ...],
        production_guard: ProductionIssuanceGuard | None = None,
    ) -> bool: ...


class DurableReconciliationRegistrar:
    """Register an exact subject/contract before selected-plan issuance."""

    def __init__(
        self,
        persistence: ReconciliationPersistence,
        rollout_profile_id: str,
        *,
        production: ProductionRegistrationPersistence | None = None,
    ) -> None:
        if (
            type(rollout_profile_id) is not str
            or len(rollout_profile_id) != 64
            or any(character not in "0123456789abcdef" for character in rollout_profile_id)
        ):
            raise ValueError("rollout profile id must be a lowercase SHA-256 digest")
        self._persistence = persistence
        self._rollout_profile_id = rollout_profile_id
        self._production = production

    async def register(
        self,
        command: DynamicPlanCommand,
        verified_plan: VerifiedPlan,
        *,
        full_ci: bool,
        provider_signals: tuple[ProviderSignal, ...],
        production_guard: ProductionIssuanceGuard | None = None,
    ) -> bool:
        if type(provider_signals) is not tuple or any(
            type(signal) is not ProviderSignal for signal in provider_signals
        ):
            raise TypeError("reconciliation requires exact provider signals")
        if not provider_signals:
            return False
        subject = reconciliation_subject(command)
        contract = _contract(
            command,
            verified_plan,
            full_ci=full_ci,
            provider_signals=provider_signals,
            rollout_profile_id=self._rollout_profile_id,
        )
        if self._production is not None:
            if full_ci:
                return production_guard is None and await self._production.register_full_ci(
                    subject, contract
                )
            if production_guard is None:
                return False
            return await self._production.register_selected(subject, contract, production_guard)
        result = await self._persistence.register_subject(subject, contract)
        return isinstance(result, SubjectRegistered | SubjectRegistrationDuplicate)


def reconciliation_subject(command: DynamicPlanCommand) -> ReconciliationSubject:
    request = command.request
    return ReconciliationSubject.create(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
        workflow_run_id=request.workflow_run_id,
        run_attempt=request.run_attempt,
    )


def _contract(
    command: DynamicPlanCommand,
    verified_plan: VerifiedPlan,
    *,
    full_ci: bool,
    provider_signals: tuple[ProviderSignal, ...],
    rollout_profile_id: str,
) -> ReconciliationContract:
    return ReconciliationContract(
        provider_signals=tuple(
            sorted(provider_signals, key=lambda signal: utf16_sort_key(signal.signal_id))
        ),
        omitted_signals=()
        if full_ci
        else tuple(
            OmittedSignal(item.obligation_id, item.obligation_id)
            for item in verified_plan.omitted_obligations
        ),
        candidate_evidence=(
            _candidate_evidence(
                command,
                verified_plan,
                rollout_profile_id=rollout_profile_id,
            )
            if full_ci
            else None
        ),
        planning_evidence=_planning_evidence(command, verified_plan),
    )


def _planning_evidence(
    command: DynamicPlanCommand,
    verified_plan: VerifiedPlan,
) -> PlanningEvidenceContext:
    source = verified_plan.source_plan
    return PlanningEvidenceContext(
        request_hash=hash_object(command.request.identity_mapping()),
        input_hash=source.input_hash,
        config_epoch_id=source.config_epoch_id,
        repo_epoch_hash=source.repo_epoch_hash,
        diff_hash=source.diff_hash,
        policy_hash=source.policy_hash,
        graph_hash=source.dependency_graph_hash,
        validation_catalog_hash=source.catalog_hash,
        deterministic_plan_id=source.plan_id,
        verified_plan_id=verified_plan.execution_plan_id,
        verified_plan_hash=hash_object(verified_plan.identity_mapping()),
        planner_version=source.planner_version,
        verifier_version="verification-core/v1",
        fallback_reason=verified_plan.fallback.reason,
    )


def _candidate_evidence(
    command: DynamicPlanCommand,
    verified_plan: VerifiedPlan,
    *,
    rollout_profile_id: str,
) -> CandidateEvidenceContext | None:
    omissions = tuple(
        OmittedSignal(item.obligation_id, item.obligation_id)
        for item in verified_plan.omitted_obligations
    )
    if not omissions:
        return None
    source = verified_plan.source_plan
    request = command.request
    check_ids = tuple(
        sorted(
            (
                *(item.obligation_id for item in verified_plan.selected_obligations),
                *(item.obligation_id for item in verified_plan.omitted_obligations),
            ),
            key=utf16_sort_key,
        )
    )
    return CandidateEvidenceContext(
        profile_id=rollout_profile_id,
        repository=f"{request.owner}/{request.repository}",
        config_epoch=source.config_epoch_id,
        policy_hash=source.policy_hash,
        diff_hash=source.diff_hash,
        graph_hash=source.dependency_graph_hash,
        baseline_plan_id="full_ci_plan_" + hash_object({"checks": list(check_ids)})[:32],
        candidate_plan_id=verified_plan.execution_plan_id,
        omitted_signals=omissions,
    )
