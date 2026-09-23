from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from governance_state_support import governance_state as _state

from ci_coordinator.app.governance_baseline import GovernanceBaselineService
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GovernanceBaselineCommand,
    GovernanceBaselineConflict,
    GovernanceBaselineCreated,
    GovernanceBaselineDuplicate,
    GovernanceBaselineOperationConflict,
    GovernanceBaselineRecord,
    GovernanceBaselineResolution,
    GovernanceBaselineStoreUnavailable,
    GovernanceBaselineUnchanged,
    GovernanceBaselineWriteResult,
    PreparedGovernanceBaseline,
)
from ci_coordinator.governance_observation import (
    GovernanceObservation,
    GovernanceObservationForbidden,
    GovernanceObservationOutcome,
    GovernanceObservationUnavailable,
    GovernanceState,
)
from ci_coordinator.kernel import FixedClock

SCOPE = RepositoryScope(7, 11)
NOW = datetime(2026, 7, 26, 10, tzinfo=UTC)


def test_denial_reaches_neither_store_nor_provider() -> None:
    store = _Store()
    observer = _Observer(_observation())

    outcome = asyncio.run(_service(False, store, observer).accept(_command()))

    assert outcome.state == "forbidden"
    assert store.calls == []
    assert observer.calls == 0


@pytest.mark.parametrize("conflict", [False, True], ids=["exact-replay", "changed-command"])
def test_operation_resolution_precedes_active_read_and_provider_io(conflict: bool) -> None:
    retained = _record()
    resolution: GovernanceBaselineResolution = (
        GovernanceBaselineOperationConflict(retained)
        if conflict
        else GovernanceBaselineDuplicate(retained)
    )
    store = _Store(resolution=resolution)
    observer = _Observer(_observation())

    outcome = asyncio.run(_service(True, store, observer).accept(_command()))

    assert outcome.state == ("operation_conflict" if conflict else "duplicate")
    assert store.calls == ["resolve"]
    assert observer.calls == 0


def test_unchanged_operation_replay_precedes_active_read_and_provider_io() -> None:
    retained = _record()
    store = _Store(resolution=GovernanceBaselineUnchanged(retained))
    observer = _Observer(_observation())

    outcome = asyncio.run(_service(True, store, observer).accept(_command()))

    assert outcome.state == "unchanged"
    assert outcome.record == retained
    assert store.calls == ["resolve"]
    assert observer.calls == 0


def test_stale_predecessor_and_observation_fail_before_write() -> None:
    active = _record()
    predecessor_store = _Store(active=active)
    predecessor_observer = _Observer(_observation())
    predecessor = asyncio.run(
        _service(True, predecessor_store, predecessor_observer).accept(_command())
    )
    stale_observer = _Observer(_observation(state=_state(SCOPE, rule_type="pull_request")))
    stale_store = _Store()
    stale = asyncio.run(_service(True, stale_store, stale_observer).accept(_command()))

    assert predecessor.state == "baseline_conflict"
    assert predecessor_store.calls == ["resolve", "load_active"]
    assert predecessor_observer.calls == 0
    assert stale.state == "stale"
    assert stale_store.calls == ["resolve", "load_active"]


@pytest.mark.parametrize(
    ("observer_result", "state"),
    [
        (GovernanceObservationForbidden(), "forbidden"),
        (GovernanceObservationUnavailable("unavailable"), "unavailable"),
    ],
)
def test_observation_failures_never_write(
    observer_result: GovernanceObservationOutcome,
    state: str,
) -> None:
    store = _Store()

    outcome = asyncio.run(_service(True, store, _Observer(observer_result)).accept(_command()))

    assert outcome.state == state
    assert store.calls == ["resolve", "load_active"]


@pytest.mark.parametrize(
    ("write_result", "state"),
    [
        ("created", "accepted"),
        ("duplicate", "duplicate"),
        ("unchanged", "unchanged"),
        ("baseline_conflict", "baseline_conflict"),
        ("operation_conflict", "operation_conflict"),
    ],
)
def test_complete_write_algebra_is_projected(write_result: str, state: str) -> None:
    outcome = asyncio.run(
        _service(True, _Store(write_result=write_result), _Observer(_observation())).accept(
            _command()
        )
    )

    assert outcome.state == state
    assert (outcome.record is not None) == (state in {"accepted", "duplicate", "unchanged"})


def test_unknown_commit_retry_resolves_before_reobserving() -> None:
    store = _Store(unknown_commit_once=True)
    observer = _Observer(_observation())
    service = _service(True, store, observer)
    command = _command()

    first = asyncio.run(service.accept(command))
    retry = asyncio.run(service.accept(command))

    assert first.state == "unavailable"
    assert retry.state == "duplicate"
    assert store.calls == ["resolve", "load_active", "accept", "resolve"]
    assert observer.calls == 1


def test_regressing_approval_clock_fails_closed_before_persistence() -> None:
    store = _Store()
    observer = _Observer(_observation())
    service = GovernanceBaselineService(
        authorizer=_Authorizer(True),
        observer=observer,
        store=store,
        clock=FixedClock(NOW - timedelta(milliseconds=1)),
    )

    outcome = asyncio.run(service.accept(_command()))

    assert outcome.state == "unavailable"
    assert store.calls == ["resolve", "load_active"]
    assert observer.calls == 1


def test_active_read_is_authorized_and_distinguishes_absence() -> None:
    denied_store = _Store()
    denied = asyncio.run(_service(False, denied_store).read_active(actor="owner:42", scope=SCOPE))
    absent_store = _Store()
    absent = asyncio.run(_service(True, absent_store).read_active(actor="owner:42", scope=SCOPE))

    assert denied.state == "forbidden"
    assert denied_store.calls == []
    assert absent.state == "absent"
    assert absent_store.calls == ["load_active"]


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


class _Store:
    def __init__(
        self,
        *,
        resolution: GovernanceBaselineResolution = None,
        active: GovernanceBaselineRecord | None = None,
        write_result: str = "created",
        unknown_commit_once: bool = False,
    ) -> None:
        self.resolution = resolution
        self.active = active
        self.write_result = write_result
        self.unknown_commit_once = unknown_commit_once
        self.retained: GovernanceBaselineRecord | None = None
        self.calls: list[str] = []

    async def resolve_operation(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineResolution:
        self.calls.append("resolve")
        if self.retained is not None:
            return GovernanceBaselineDuplicate(self.retained)
        return self.resolution

    async def load_active(self, scope: RepositoryScope) -> GovernanceBaselineRecord | None:
        assert scope == SCOPE
        self.calls.append("load_active")
        return self.active

    async def accept(
        self,
        prepared: PreparedGovernanceBaseline,
    ) -> GovernanceBaselineWriteResult:
        self.calls.append("accept")
        record = GovernanceBaselineRecord.from_draft(
            prepared.draft,
            approved_at=prepared.approved_at,
            audit_event_id="audit_" + "a" * 32,
            audit_input_hash=prepared.audit_input_hash,
        )
        if self.unknown_commit_once:
            self.unknown_commit_once = False
            self.retained = record
            raise GovernanceBaselineStoreUnavailable("unknown commit")
        if self.write_result == "created":
            return GovernanceBaselineCreated(record)
        if self.write_result == "duplicate":
            return GovernanceBaselineDuplicate(record)
        if self.write_result == "unchanged":
            return GovernanceBaselineUnchanged(record)
        if self.write_result == "baseline_conflict":
            return GovernanceBaselineConflict(None)
        return GovernanceBaselineOperationConflict(record)


def _service(
    allowed: bool,
    store: _Store,
    observer: _Observer | None = None,
) -> GovernanceBaselineService:
    return GovernanceBaselineService(
        authorizer=_Authorizer(allowed),
        observer=observer or _Observer(_observation()),
        store=store,
        clock=FixedClock(NOW + timedelta(seconds=1)),
    )


def _command() -> GovernanceBaselineCommand:
    observation = _observation()
    return GovernanceBaselineCommand(
        scope=SCOPE,
        operation_id="approve-1",
        expected_state_digest=observation.state_digest,
        expected_active=None,
        actor="owner:42",
        reason="Adopt repository governance",
    )


def _record() -> GovernanceBaselineRecord:
    command = _command()
    from ci_coordinator.governance_baseline import (
        GovernanceBaselineDraft,
        prepare_governance_baseline,
    )

    draft = GovernanceBaselineDraft(command, _state(SCOPE), NOW)
    prepared = prepare_governance_baseline(draft, approved_at=NOW + timedelta(seconds=1))
    return GovernanceBaselineRecord.from_draft(
        draft,
        approved_at=prepared.approved_at,
        audit_event_id="audit_" + "a" * 32,
        audit_input_hash=prepared.audit_input_hash,
    )


def _observation(*, state: GovernanceState | None = None) -> GovernanceObservation:
    return GovernanceObservation.from_state(state or _state(SCOPE), observed_at=NOW)
