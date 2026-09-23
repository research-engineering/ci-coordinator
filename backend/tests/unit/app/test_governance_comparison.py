from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ci_coordinator.app.governance_comparison import (
    GovernanceComparisonEvidence,
    GovernanceComparisonForbidden,
    GovernanceComparisonService,
    GovernanceComparisonStale,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GovernanceBaselineCommand,
    GovernanceBaselineDraft,
    GovernanceBaselineRecord,
    GovernanceBaselineResolution,
    GovernanceBaselineStoreUnavailable,
    GovernanceBaselineWriteResult,
    PreparedGovernanceBaseline,
    prepare_governance_baseline,
)
from ci_coordinator.governance_comparison import GovernanceStateComparison
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceObservation,
    GovernanceObservationOutcome,
    GovernanceObservationUnavailable,
    GovernanceRepository,
    GovernanceState,
    encode_governance_state,
)
from ci_coordinator.kernel import canonical_json, sha256_hex

SCOPE = RepositoryScope(7, 11)
NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


def test_denial_reaches_neither_store_nor_provider() -> None:
    store = _Store([])
    observer = _Observer(_observation())

    result = asyncio.run(_service(False, store, observer)(actor="owner:42", scope=SCOPE))

    assert isinstance(result, GovernanceComparisonForbidden)
    assert store.calls == 0
    assert observer.calls == 0


def test_initial_store_failure_precedes_provider_io() -> None:
    store = _Store([], fail_at=1)
    observer = _Observer(_observation())

    result = asyncio.run(_service(True, store, observer)(actor="owner:42", scope=SCOPE))

    assert result == GovernanceObservationUnavailable("unavailable")
    assert store.calls == 1
    assert observer.calls == 0


@pytest.mark.parametrize("baseline", [None, "active"])
def test_stable_pointer_yields_bound_evidence(baseline: str | None) -> None:
    record = None if baseline is None else _record(_state())
    store = _Store([record, record])

    result = asyncio.run(
        _service(True, store, _Observer(_observation()))(actor="owner:42", scope=SCOPE)
    )

    assert isinstance(result, GovernanceComparisonEvidence)
    assert result.state == ("unbaselined" if record is None else "compared")
    assert result.baseline == record
    if record is None:
        assert result.comparison is None
    else:
        assert result.comparison is not None
        assert result.comparison.relation == "matches"
    assert store.calls == 2


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, "active"),
        ("active", None),
        ("active", "replacement"),
    ],
)
def test_baseline_transition_during_observation_is_stale(
    before: str | None,
    after: str | None,
) -> None:
    first = None if before is None else _record(_state())
    second = (
        None if after is None else _record(_state(default_branch="main"), expected_active=first)
    )

    result = asyncio.run(
        _service(True, _Store([first, second]), _Observer(_observation()))(
            actor="owner:42",
            scope=SCOPE,
        )
    )

    assert isinstance(result, GovernanceComparisonStale)


def test_provider_failure_skips_second_baseline_read() -> None:
    store = _Store([None])
    failure = GovernanceObservationUnavailable("rate_limited", 30)

    result = asyncio.run(_service(True, store, _Observer(failure))(actor="owner:42", scope=SCOPE))

    assert result == failure
    assert store.calls == 1


def test_final_store_failure_invalidates_the_completed_observation() -> None:
    store = _Store([None], fail_at=2)
    observer = _Observer(_observation())

    result = asyncio.run(_service(True, store, observer)(actor="owner:42", scope=SCOPE))

    assert result == GovernanceObservationUnavailable("unavailable")
    assert store.calls == 2
    assert observer.calls == 1


def test_cancellation_propagates_without_becoming_comparison_evidence() -> None:
    store = _Store([None])

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            _service(True, store, _CancellingObserver())(
                actor="owner:42",
                scope=SCOPE,
            )
        )

    assert store.calls == 1


def test_cross_scope_observation_fails_closed() -> None:
    foreign = GovernanceObservation.from_state(
        _state(scope=RepositoryScope(7, 12)),
        observed_at=NOW,
    )

    result = asyncio.run(
        _service(True, _Store([None]), _Observer(foreign))(actor="owner:42", scope=SCOPE)
    )

    assert result == GovernanceObservationUnavailable("provider_binding_mismatch")


def test_evidence_rejects_a_false_projection_over_exact_states() -> None:
    state = _state()
    record = _record(state)
    observation = GovernanceObservation.from_state(state, observed_at=NOW)
    false_comparison = GovernanceStateComparison(
        relation="differs",
        baseline_state_digest=record.pointer.state_digest,
        current_state_digest=observation.state_digest,
        changed_coordinates=("api_version",),
        added_rule_count=0,
        removed_rule_count=0,
    )

    with pytest.raises(ValueError, match="contradicts its exact states"):
        GovernanceComparisonEvidence(observation, record, false_comparison)


@dataclass
class _Authorizer:
    allowed: bool

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        assert actor == "owner:42"
        assert scope == SCOPE
        return self.allowed


@dataclass
class _Observer:
    outcome: GovernanceObservationOutcome
    calls: int = 0

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceObservationOutcome:
        assert actor == "owner:42"
        assert scope == SCOPE
        self.calls += 1
        return self.outcome


@dataclass
class _CancellingObserver:
    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceObservationOutcome:
        assert actor == "owner:42"
        assert scope == SCOPE
        raise asyncio.CancelledError


@dataclass
class _Store:
    records: list[GovernanceBaselineRecord | None]
    fail_at: int | None = None
    calls: int = 0

    async def load_active(self, scope: RepositoryScope) -> GovernanceBaselineRecord | None:
        assert scope == SCOPE
        self.calls += 1
        if self.calls == self.fail_at:
            raise GovernanceBaselineStoreUnavailable("sentinel")
        return self.records.pop(0)

    async def resolve_operation(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineResolution:
        raise AssertionError(command)

    async def accept(
        self,
        prepared: PreparedGovernanceBaseline,
    ) -> GovernanceBaselineWriteResult:
        raise AssertionError(prepared)


def _service(
    allowed: bool,
    store: _Store,
    observer: _Observer | _CancellingObserver,
) -> GovernanceComparisonService:
    return GovernanceComparisonService(
        authorizer=_Authorizer(allowed),
        observer=observer,
        store=store,
    )


def _record(
    state: GovernanceState,
    *,
    expected_active: GovernanceBaselineRecord | None = None,
) -> GovernanceBaselineRecord:
    command = GovernanceBaselineCommand(
        scope=SCOPE,
        operation_id=f"approve-{1 if expected_active is None else 2}",
        expected_state_digest=sha256_hex(encode_governance_state(state)),
        expected_active=None if expected_active is None else expected_active.pointer,
        actor="owner:42",
        reason="Adopt repository governance",
    )
    draft = GovernanceBaselineDraft(command, state, NOW)
    prepared = prepare_governance_baseline(
        draft,
        approved_at=NOW + timedelta(seconds=draft.version),
    )
    return GovernanceBaselineRecord.from_draft(
        draft,
        approved_at=prepared.approved_at,
        audit_event_id="audit_" + f"{draft.version:x}".rjust(32, "0"),
        audit_input_hash=prepared.audit_input_hash,
    )


def _observation() -> GovernanceObservation:
    return GovernanceObservation.from_state(_state(), observed_at=NOW)


def _state(
    *,
    default_branch: str = "master",
    scope: RepositoryScope = SCOPE,
) -> GovernanceState:
    value = {
        "ruleset_id": 41,
        "ruleset_source": "example/repository",
        "ruleset_source_type": "Repository",
        "type": "required_status_checks",
    }
    return GovernanceState(
        GovernanceRepository(
            scope,
            101,
            "example",
            "repository",
            "example/repository",
            default_branch,
        ),
        "2026-03-10",
        (
            EffectiveGovernanceRule(
                "required_status_checks",
                "Repository",
                "example/repository",
                41,
                canonical_json(value),
            ),
        ),
    )
