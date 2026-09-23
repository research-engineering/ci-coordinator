"""Authorization-first governance-baseline reads and approval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GovernanceBaselineCommand,
    GovernanceBaselineConflict,
    GovernanceBaselineCreated,
    GovernanceBaselineDraft,
    GovernanceBaselineDuplicate,
    GovernanceBaselineOperationConflict,
    GovernanceBaselineRecord,
    GovernanceBaselineStore,
    GovernanceBaselineStoreUnavailable,
    GovernanceBaselineUnchanged,
    normalize_baseline_instant,
    prepare_governance_baseline,
)
from ci_coordinator.governance_observation import (
    GovernanceObservation,
    GovernanceObservationForbidden,
    GovernanceObservationUnavailable,
    GovernanceObservationUseCase,
    GovernanceState,
)
from ci_coordinator.kernel import Clock

type GovernanceBaselineReadState = Literal["active", "absent", "forbidden", "unavailable"]
type GovernanceBaselineApprovalState = Literal[
    "accepted",
    "duplicate",
    "unchanged",
    "forbidden",
    "stale",
    "baseline_conflict",
    "operation_conflict",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class GovernanceBaselineReadOutcome:
    state: GovernanceBaselineReadState
    record: GovernanceBaselineRecord | None = None

    def __post_init__(self) -> None:
        if self.state not in {"active", "absent", "forbidden", "unavailable"}:
            raise ValueError("governance baseline read state is invalid")
        if self.record is not None and type(self.record) is not GovernanceBaselineRecord:
            raise TypeError("governance baseline read record must be exact")
        if (self.state == "active") != (self.record is not None):
            raise ValueError("only an active governance baseline read has a record")


@dataclass(frozen=True, slots=True)
class GovernanceBaselineApprovalOutcome:
    state: GovernanceBaselineApprovalState
    record: GovernanceBaselineRecord | None = None

    def __post_init__(self) -> None:
        if self.state not in {
            "accepted",
            "duplicate",
            "unchanged",
            "forbidden",
            "stale",
            "baseline_conflict",
            "operation_conflict",
            "unavailable",
        }:
            raise ValueError("governance baseline approval state is invalid")
        if self.record is not None and type(self.record) is not GovernanceBaselineRecord:
            raise TypeError("governance baseline approval record must be exact")
        retains_record = self.state in {"accepted", "duplicate", "unchanged"}
        if retains_record != (self.record is not None):
            raise ValueError("successful governance baseline outcome requires a record")


class GovernanceBaselineAuthorizer(Protocol):
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool: ...


class GovernanceBaselineUseCase(Protocol):
    async def read_active(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceBaselineReadOutcome: ...

    async def accept(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineApprovalOutcome: ...


class GovernanceBaselineService:
    def __init__(
        self,
        *,
        authorizer: GovernanceBaselineAuthorizer,
        observer: GovernanceObservationUseCase,
        store: GovernanceBaselineStore,
        clock: Clock,
    ) -> None:
        self._authorizer = authorizer
        self._observer = observer
        self._store = store
        self._clock = clock

    async def read_active(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceBaselineReadOutcome:
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return GovernanceBaselineReadOutcome("forbidden")
        try:
            record = await self._store.load_active(scope)
        except GovernanceBaselineStoreUnavailable:
            return GovernanceBaselineReadOutcome("unavailable")
        if record is None:
            return GovernanceBaselineReadOutcome("absent")
        return GovernanceBaselineReadOutcome("active", record)

    async def accept(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineApprovalOutcome:
        if type(command) is not GovernanceBaselineCommand:
            raise TypeError("governance baseline service requires an exact command")
        if not await self._authorizer.allows_scope(actor=command.actor, scope=command.scope):
            return GovernanceBaselineApprovalOutcome("forbidden")
        try:
            replay = await self._store.resolve_operation(command)
            if isinstance(replay, GovernanceBaselineDuplicate):
                return GovernanceBaselineApprovalOutcome("duplicate", replay.record)
            if isinstance(replay, GovernanceBaselineUnchanged):
                return GovernanceBaselineApprovalOutcome("unchanged", replay.record)
            if isinstance(replay, GovernanceBaselineOperationConflict):
                return GovernanceBaselineApprovalOutcome("operation_conflict")
            active = await self._store.load_active(command.scope)
        except GovernanceBaselineStoreUnavailable:
            return GovernanceBaselineApprovalOutcome("unavailable")
        active_pointer = None if active is None else active.pointer
        if active_pointer != command.expected_active:
            return GovernanceBaselineApprovalOutcome("baseline_conflict")

        observed = await self._observer(actor=command.actor, scope=command.scope)
        if isinstance(observed, GovernanceObservationForbidden):
            return GovernanceBaselineApprovalOutcome("forbidden")
        if isinstance(observed, GovernanceObservationUnavailable):
            return GovernanceBaselineApprovalOutcome("unavailable")
        if not isinstance(observed, GovernanceObservation):
            raise TypeError("governance baseline observer returned an unsupported outcome")
        if (
            observed.repository.scope != command.scope
            or observed.state_digest != command.expected_state_digest
        ):
            return GovernanceBaselineApprovalOutcome("stale")
        draft = GovernanceBaselineDraft(
            command=command,
            state=GovernanceState(
                repository=observed.repository,
                api_version=observed.api_version,
                rules=observed.rules,
            ),
            observed_at=normalize_baseline_instant(observed.observed_at),
        )
        approved_at = normalize_baseline_instant(self._clock.now())
        if approved_at < draft.observed_at:
            return GovernanceBaselineApprovalOutcome("unavailable")
        prepared = prepare_governance_baseline(draft, approved_at=approved_at)
        try:
            result = await self._store.accept(prepared)
        except GovernanceBaselineStoreUnavailable:
            return GovernanceBaselineApprovalOutcome("unavailable")
        if isinstance(result, GovernanceBaselineCreated):
            return GovernanceBaselineApprovalOutcome("accepted", result.record)
        if isinstance(result, GovernanceBaselineDuplicate):
            return GovernanceBaselineApprovalOutcome("duplicate", result.record)
        if isinstance(result, GovernanceBaselineUnchanged):
            return GovernanceBaselineApprovalOutcome("unchanged", result.record)
        if isinstance(result, GovernanceBaselineConflict):
            return GovernanceBaselineApprovalOutcome("baseline_conflict")
        if isinstance(result, GovernanceBaselineOperationConflict):
            return GovernanceBaselineApprovalOutcome("operation_conflict")
        raise RuntimeError("governance baseline result algebra is incomplete")
