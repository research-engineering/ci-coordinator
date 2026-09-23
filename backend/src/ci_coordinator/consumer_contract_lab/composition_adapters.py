"""In-memory protocol adapters for the consumer laboratory composition."""

from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpochSnapshot
from ci_coordinator.consumer_contract_lab.model import ConsumerLabProfile
from ci_coordinator.execution_orchestration import TargetExecutionRegistry
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.reconciliation import (
    ReconciliationContract,
    ReconciliationSnapshot,
    ReconciliationSubject,
    SubjectRegistration,
    register_subject,
)
from ci_coordinator.repo_context import PlanningInput
from ci_coordinator.runner_capacity import CapacityClassSelector, TrustedExecutionInputs


class ActiveEpochs:
    def __init__(self, active: ActiveConfigEpochSnapshot) -> None:
        self._active = active

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        return self._active if scope == self._active.active.scope else None


@dataclass(frozen=True, slots=True)
class LoadedContext:
    planning_input: PlanningInput


class Contexts:
    def __init__(self, planning_input: PlanningInput) -> None:
        self._planning_input = planning_input

    async def load(self, request: PlanRequest, projection: object) -> LoadedContext:
        del projection
        epoch = self._planning_input.repo_epoch
        if (
            request.installation_id,
            request.repository_id,
            request.base_sha,
            request.head_sha,
        ) != (
            epoch.installation_id,
            epoch.repository_id,
            epoch.base_sha,
            epoch.head_sha,
        ):
            raise ValueError("scenario planning input does not bind the request")
        return LoadedContext(self._planning_input)


class ExecutionInputs:
    def __init__(
        self,
        profile: ConsumerLabProfile,
        registry: TargetExecutionRegistry,
        execution_authority_sha: str | None,
    ) -> None:
        self._profile = profile
        self._registry = registry
        self._execution_authority_sha = execution_authority_sha
        self.load_count = 0

    async def load(
        self,
        request: PlanRequest,
        execution_authority_sha: str,
        /,
    ) -> TrustedExecutionInputs | None:
        self.load_count += 1
        repository = self._profile.repository
        if (
            request.installation_id != repository.installation_id
            or request.repository_id != repository.repository_id
            or execution_authority_sha != self._execution_authority_sha
        ):
            return None
        return TrustedExecutionInputs(self._registry, None)


class NoRunnerSnapshot:
    async def capture(
        self,
        request: PlanRequest,
        selectors: tuple[CapacityClassSelector, ...],
        /,
    ) -> None:
        del request, selectors
        raise AssertionError("native target execution must not request a runner snapshot")


class RegistrationStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, ReconciliationSnapshot] = {}
        self.registration_count = 0

    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
    ) -> SubjectRegistration:
        self.registration_count += 1
        existing = self.snapshots.get(subject.subject_id)
        result = register_subject(existing, subject, contract)
        snapshot = getattr(result, "snapshot", None)
        if type(snapshot) is ReconciliationSnapshot:
            self.snapshots[subject.subject_id] = snapshot
        return result
