"""Authorization-first exact governance comparison orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GovernanceBaselinePointer,
    GovernanceBaselineRecord,
    GovernanceBaselineStore,
    GovernanceBaselineStoreUnavailable,
)
from ci_coordinator.governance_comparison import (
    GovernanceStateComparison,
    compare_governance_states,
)
from ci_coordinator.governance_observation import (
    GovernanceObservation,
    GovernanceObservationAuthorizer,
    GovernanceObservationForbidden,
    GovernanceObservationUnavailable,
    GovernanceObservationUseCase,
    GovernanceState,
)


@dataclass(frozen=True, slots=True)
class GovernanceComparisonEvidence:
    observation: GovernanceObservation
    baseline: GovernanceBaselineRecord | None
    comparison: GovernanceStateComparison | None

    def __post_init__(self) -> None:
        if type(self.observation) is not GovernanceObservation:
            raise TypeError("governance comparison evidence requires an exact observation")
        if self.baseline is not None and type(self.baseline) is not GovernanceBaselineRecord:
            raise TypeError("governance comparison evidence requires an exact baseline")
        if self.comparison is not None and type(self.comparison) is not GovernanceStateComparison:
            raise TypeError("governance comparison evidence requires an exact comparison")
        if (self.baseline is None) != (self.comparison is None):
            raise ValueError("governance comparison evidence has an incomplete baseline relation")
        if self.baseline is None:
            return
        if (
            self.baseline.command.scope != self.observation.repository.scope
            or self.comparison is None
            or self.comparison.baseline_state_digest != self.baseline.pointer.state_digest
            or self.comparison.current_state_digest != self.observation.state_digest
        ):
            raise ValueError("governance comparison evidence crosses its exact identity")
        expected = compare_governance_states(
            self.baseline.state,
            _state_from_observation(self.observation),
        )
        if self.comparison != expected:
            raise ValueError("governance comparison evidence contradicts its exact states")

    @property
    def state(self) -> Literal["unbaselined", "compared"]:
        return "unbaselined" if self.baseline is None else "compared"


@dataclass(frozen=True, slots=True)
class GovernanceComparisonForbidden:
    pass


@dataclass(frozen=True, slots=True)
class GovernanceComparisonStale:
    pass


type GovernanceComparisonOutcome = (
    GovernanceComparisonEvidence
    | GovernanceComparisonForbidden
    | GovernanceComparisonStale
    | GovernanceObservationUnavailable
)


class GovernanceComparisonUseCase(Protocol):
    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceComparisonOutcome: ...


class GovernanceComparisonService:
    def __init__(
        self,
        *,
        authorizer: GovernanceObservationAuthorizer,
        observer: GovernanceObservationUseCase,
        store: GovernanceBaselineStore,
    ) -> None:
        self._authorizer = authorizer
        self._observer = observer
        self._store = store

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceComparisonOutcome:
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return GovernanceComparisonForbidden()
        try:
            baseline_before = await self._store.load_active(scope)
        except GovernanceBaselineStoreUnavailable:
            return GovernanceObservationUnavailable("unavailable")

        observed = await self._observer(actor=actor, scope=scope)
        if isinstance(observed, GovernanceObservationForbidden):
            return GovernanceComparisonForbidden()
        if isinstance(observed, GovernanceObservationUnavailable):
            return observed
        if not isinstance(observed, GovernanceObservation):
            raise TypeError("governance comparison observer returned an unsupported outcome")
        if observed.repository.scope != scope:
            return GovernanceObservationUnavailable("provider_binding_mismatch")

        try:
            baseline_after = await self._store.load_active(scope)
        except GovernanceBaselineStoreUnavailable:
            return GovernanceObservationUnavailable("unavailable")
        if _pointer(baseline_before) != _pointer(baseline_after):
            return GovernanceComparisonStale()
        if baseline_after is None:
            return GovernanceComparisonEvidence(observed, None, None)
        if baseline_after.command.scope != scope:
            return GovernanceObservationUnavailable("unavailable")

        return GovernanceComparisonEvidence(
            observation=observed,
            baseline=baseline_after,
            comparison=compare_governance_states(
                baseline_after.state,
                _state_from_observation(observed),
            ),
        )


def _pointer(record: GovernanceBaselineRecord | None) -> GovernanceBaselinePointer | None:
    return None if record is None else record.pointer


def _state_from_observation(observation: GovernanceObservation) -> GovernanceState:
    return GovernanceState(
        repository=observation.repository,
        api_version=observation.api_version,
        rules=observation.rules,
    )
