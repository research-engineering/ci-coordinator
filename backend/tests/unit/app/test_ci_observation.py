import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Literal, cast

import pytest
from ci_economics.observation_factories import NOW, SCOPE, observation

from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden, CiEconomicsReadUnavailable
from ci_coordinator.app.ci_observation import CiObservationService, ObservationCursorRejected
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
    ObservationCommitted,
    ObservationConflict,
    ObservationWriteResult,
)
from ci_coordinator.ci_economics.observation_gaps import InvalidObservationGapCursor, ObservationGap
from ci_coordinator.ci_economics.observation_ports import ObservationGapPage, ObservationStatus
from ci_coordinator.ci_economics.observation_workflows import ObservationWorkflowPage
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable, ProviderAttemptDeferred
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

COMMAND = ConfigureObservation(SCOPE, 2, observation().configuration, "operation-1", "admin")
COMMITTED = ObservationCommitted(observation(), False)
STATUS = ObservationStatus(SCOPE, None, (), 0, None, NOW)
GAP_PAGE = ObservationGapPage(SCOPE, (), None, NOW)
WORKFLOWS = ObservationWorkflowPage(SCOPE, 1, 0, (), "exhausted")
type Operation = Literal["configure", "status", "gaps"]


@dataclass
class Boundary:
    allowed: bool = True
    authorization_error: BaseException | None = None
    store_error: BaseException | None = None
    receipt: ObservationWriteResult = COMMITTED
    status_result: ObservationStatus = STATUS
    gaps_result: ObservationGapPage = GAP_PAGE
    workflows_result: ObservationWorkflowPage | ProviderAttemptDeferred = WORKFLOWS
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append(("authorize", (actor, scope)))
        if self.authorization_error is not None:
            raise self.authorization_error
        return self.allowed

    def record(self, operation: str, value: object) -> None:
        self.calls.append((operation, value))
        if self.store_error is not None:
            raise self.store_error

    async def configure_observation(self, command: ConfigureObservation) -> ObservationWriteResult:
        self.record("configure", command)
        return self.receipt

    async def observation_status(self, scope: RepositoryScope) -> ObservationStatus:
        self.record("status", scope)
        return self.status_result

    async def observation_gaps(
        self, scope: RepositoryScope, *, after_cursor: str | None, limit: int
    ) -> ObservationGapPage:
        self.record("gaps", (scope, after_cursor, limit))
        return self.gaps_result

    async def workflow_page(
        self, scope: RepositoryScope, *, page_number: int
    ) -> ObservationWorkflowPage | ProviderAttemptDeferred:
        self.record("workflows", (scope, page_number))
        return self.workflows_result

    def service(self) -> CiObservationService:
        return CiObservationService(
            authorizer=self, configuration_store=self, query=self, workflow_catalog=self
        )


async def invoke(boundary: Boundary, operation: Operation) -> object:
    service = boundary.service()
    if operation == "configure":
        return await service.configure(COMMAND)
    if operation == "status":
        return await service.status(actor="admin", scope=SCOPE)
    return await service.gaps(actor="admin", scope=SCOPE, after_cursor=None, limit=1)


@pytest.mark.parametrize("authority", ["allow", "deny", "unavailable", "malformed"])
async def test_workflow_catalogue_current_authorization_precedes_provider_access(
    authority: str,
) -> None:
    boundary = Boundary(
        allowed=True
        if authority == "allow"
        else cast(bool, 1)
        if authority == "malformed"
        else False,
        authorization_error=RepositoryAccessUnavailable() if authority == "unavailable" else None,
    )
    result = await boundary.service().workflows(actor="admin", scope=SCOPE, page_number=1)
    assert boundary.calls[0] == ("authorize", ("admin", SCOPE))
    if authority == "allow":
        assert result is WORKFLOWS and boundary.calls[1] == ("workflows", (SCOPE, 1))
    else:
        assert result == (
            CiEconomicsReadForbidden() if authority == "deny" else CiEconomicsReadUnavailable()
        )
        assert len(boundary.calls) == 1


@pytest.mark.parametrize("operand", ["valid", "scope", "page", "type", "unavailable"])
async def test_workflow_catalogue_result_is_bound_and_unavailability_is_not_empty(
    operand: str,
) -> None:
    result: ObservationWorkflowPage | ProviderAttemptDeferred = WORKFLOWS
    if operand == "scope":
        result = replace(WORKFLOWS, scope=RepositoryScope(101, 203))
    elif operand == "page":
        result = replace(WORKFLOWS, page_number=2, termination="truncated")
    elif operand == "type":
        result = cast(ObservationWorkflowPage, {})
    elif operand == "unavailable":
        result = ProviderAttemptDeferred("provider_unavailable")
    actual = (
        await Boundary(workflows_result=result)
        .service()
        .workflows(actor="admin", scope=SCOPE, page_number=1)
    )
    assert actual == (WORKFLOWS if operand == "valid" else CiEconomicsReadUnavailable())


@pytest.mark.parametrize("number", [0, True, 21])
async def test_invalid_workflow_page_precedes_authorization_or_provider(number: int) -> None:
    boundary = Boundary()
    with pytest.raises(ValueError):
        await boundary.service().workflows(actor="admin", scope=SCOPE, page_number=number)
    assert not boundary.calls


@pytest.mark.parametrize("stage", ["authorization", "provider"])
async def test_workflow_catalogue_cancellation_is_not_converted_to_unavailable(stage: str) -> None:
    error = asyncio.CancelledError()
    boundary = Boundary(
        authorization_error=error if stage == "authorization" else None,
        store_error=error if stage == "provider" else None,
    )
    with pytest.raises(asyncio.CancelledError) as raised:
        await boundary.service().workflows(actor="admin", scope=SCOPE, page_number=1)
    assert raised.value is error
    assert len(boundary.calls) == (1 if stage == "authorization" else 2)


@pytest.mark.parametrize("stage", ["allows_scope", "workflow_page"])
async def test_workflow_catalogue_deadline_covers_authorization_and_provider(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ci_coordinator.app import ci_observation

    assert ci_observation.OBSERVATION_WORKFLOW_DEADLINE_SECONDS == 20
    monkeypatch.setattr(ci_observation, "OBSERVATION_WORKFLOW_DEADLINE_SECONDS", 0.01)
    boundary = Boundary()
    stopped = asyncio.Event()

    async def blocked(*args: object, **kwargs: object) -> bool:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        return True

    monkeypatch.setattr(boundary, stage, blocked)
    result = await asyncio.wait_for(
        boundary.service().workflows(actor="admin", scope=SCOPE, page_number=1), timeout=3
    )
    assert result == CiEconomicsReadUnavailable() and stopped.is_set()
    assert boundary.calls == ([] if stage == "allows_scope" else [("authorize", ("admin", SCOPE))])


@pytest.mark.parametrize("operation", ["configure", "status", "gaps"])
@pytest.mark.parametrize("authority", ["allow", "deny", "unavailable", "malformed"])
async def test_current_authorization_precedes_every_store_operation(
    operation: Operation, authority: str
) -> None:
    boundary = Boundary(
        allowed=True
        if authority == "allow"
        else cast(bool, 1)
        if authority == "malformed"
        else False,
        authorization_error=RepositoryAccessUnavailable() if authority == "unavailable" else None,
    )
    result = await invoke(boundary, operation)
    assert boundary.calls[0] == ("authorize", ("admin", SCOPE))
    if authority == "allow":
        assert result is {"configure": COMMITTED, "status": STATUS, "gaps": GAP_PAGE}[operation]
        assert len(boundary.calls) == 2
    else:
        assert result == (
            CiEconomicsReadForbidden() if authority == "deny" else CiEconomicsReadUnavailable()
        )
        assert len(boundary.calls) == 1


@pytest.mark.parametrize("operation", ["configure", "status", "gaps"])
async def test_unavailable_storage_never_becomes_a_success(operation: Operation) -> None:
    boundary = Boundary(store_error=CiEconomicsStoreUnavailable("private"))
    assert await invoke(boundary, operation) == CiEconomicsReadUnavailable()
    assert len(boundary.calls) == 2


@pytest.mark.parametrize("operation", ["configure", "status", "gaps"])
@pytest.mark.parametrize("stage", ["authorization", "store"])
async def test_cancellation_is_not_a_response_or_a_retry(operation: Operation, stage: str) -> None:
    error = asyncio.CancelledError()
    boundary = Boundary(
        authorization_error=error if stage == "authorization" else None,
        store_error=error if stage == "store" else None,
    )
    with pytest.raises(asyncio.CancelledError) as raised:
        await invoke(boundary, operation)
    assert raised.value is error
    assert len(boundary.calls) == (1 if stage == "authorization" else 2)


@pytest.mark.parametrize("replayed", [False, True])
@pytest.mark.parametrize("operand", ["valid", "scope", "revision", "configuration", "type"])
async def test_configuration_receipt_is_bound_to_the_exact_command(
    replayed: bool, operand: str
) -> None:
    snapshot = observation()
    if operand == "scope":
        snapshot = replace(snapshot, scope=RepositoryScope(101, 203))
    elif operand == "revision":
        snapshot = replace(snapshot, revision=4)
    elif operand == "configuration":
        snapshot = replace(snapshot, configuration=replace(snapshot.configuration, enabled=False))
    receipt = ObservationCommitted(snapshot, replayed)
    boundary = Boundary(
        receipt=cast(ObservationWriteResult, object()) if operand == "type" else receipt
    )
    result = await invoke(boundary, "configure")
    assert result == (receipt if operand == "valid" else CiEconomicsReadUnavailable())


@pytest.mark.parametrize("reason", ["revision_conflict", "operation_conflict", "capacity_reached"])
async def test_durable_conflict_is_preserved(
    reason: Literal["revision_conflict", "operation_conflict", "capacity_reached"],
) -> None:
    conflict = ObservationConflict(reason)
    assert await invoke(Boundary(receipt=conflict), "configure") is conflict


@pytest.mark.parametrize("operation", ["status", "gaps"])
async def test_foreign_scope_result_is_not_disclosed(operation: Operation) -> None:
    foreign = RepositoryScope(101, 999)
    boundary = Boundary(
        status_result=replace(STATUS, scope=foreign), gaps_result=replace(GAP_PAGE, scope=foreign)
    )
    assert await invoke(boundary, operation) == CiEconomicsReadUnavailable()


@pytest.mark.parametrize("limit", [0, 51, True])
async def test_gap_limit_is_rejected_before_authorization(limit: int) -> None:
    boundary = Boundary()
    with pytest.raises(ValueError):
        await boundary.service().gaps(actor="admin", scope=SCOPE, after_cursor=None, limit=limit)
    assert not boundary.calls


@pytest.mark.parametrize("cursor", ["", "a" * 63, "A" * 64, "a" * 65])
async def test_gap_cursor_is_rejected_without_storage_after_authorization(cursor: str) -> None:
    boundary = Boundary()
    result = await boundary.service().gaps(actor="admin", scope=SCOPE, after_cursor=cursor, limit=1)
    assert result == ObservationCursorRejected()
    assert boundary.calls == [("authorize", ("admin", SCOPE))]


async def test_expired_gap_cursor_is_restartable_not_storage_unavailability() -> None:
    boundary = Boundary(store_error=InvalidObservationGapCursor())
    assert await invoke(boundary, "gaps") == ObservationCursorRejected()


@pytest.mark.parametrize("scope_prefix", ["102.202", "101.203"])
async def test_foreign_gap_cursor_never_reaches_storage(scope_prefix: str) -> None:
    boundary = Boundary()
    cursor = f"{scope_prefix}.3." + "a" * 64
    assert (
        await boundary.service().gaps(actor="admin", scope=SCOPE, after_cursor=cursor, limit=1)
        == ObservationCursorRejected()
    )
    assert boundary.calls == [("authorize", ("admin", SCOPE))]


@pytest.mark.parametrize("relation", ["valid", "over_limit", "before_cursor", "at_cursor"])
async def test_gap_result_respects_requested_page_and_cursor(relation: str) -> None:
    gaps = tuple(
        sorted(
            (
                ObservationGap(
                    SCOPE,
                    3,
                    "c" * 64,
                    "recent",
                    NOW,
                    NOW - timedelta(seconds=i),
                    NOW,
                    "provider_truncated",
                )
                for i in (1, 2)
            ),
            key=lambda gap: gap.gap_id,
        )
    )
    selected = gaps if relation == "over_limit" else (gaps[0],)
    after = {
        "valid": None,
        "over_limit": None,
        "before_cursor": f"101.202.3.{gaps[1].gap_id}",
        "at_cursor": f"101.202.3.{gaps[0].gap_id}",
    }[relation]
    page = ObservationGapPage(SCOPE, selected, None, NOW)
    boundary = Boundary(gaps_result=page)
    result = await boundary.service().gaps(actor="admin", scope=SCOPE, after_cursor=after, limit=1)
    assert result == (page if relation == "valid" else CiEconomicsReadUnavailable())
