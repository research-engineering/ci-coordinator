from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal, cast

import pytest
from ci_economics.observation_factories import NOW, SCOPE, claimed_scan, observation

from ci_coordinator.ci_economics.discovery import ProviderObservationPage, ProviderRunDiscoveryPage
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
    ObservationCommitted,
    ObservationConflict,
)
from ci_coordinator.ci_economics.observation_ports import (
    ClaimedObservation,
    ObservationGapPage,
    ObservationStatus,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.persistence.ci_observation_adapters import TransactionalObservationStore
from ci_coordinator.persistence.ci_observation_scan_state import ObservationLeaseExpired
from ci_coordinator.persistence.ci_observation_unit_of_work import PostgresObservationUnitOfWork
from ci_coordinator.persistence.errors import (
    CommitCancelledOutcomeUnknown,
    CommitOutcomeUnknown,
    CommittedButCleanupFailed,
    StoreUnavailable,
)

type Operation = Literal["configure", "status", "gaps", "claim", "record", "defer", "purge"]
_OPERATIONS: tuple[Operation, ...] = (
    "configure",
    "status",
    "gaps",
    "claim",
    "record",
    "defer",
    "purge",
)


class _Repository:
    def __init__(self, result: object, step: Callable[[str], None]) -> None:
        self._result = result
        self._step = step
        self.arguments: tuple[object, ...] = ()

    def _return(self, *arguments: object) -> object:
        self.arguments = arguments
        self._step("operation")
        return self._result

    async def configure_observation(self, command: object) -> object:
        return self._return(command)

    async def observation_status(self, scope: object) -> object:
        return self._return(scope)

    async def observation_gaps(
        self, scope: object, *, after_cursor: object, limit: object
    ) -> object:
        return self._return(scope, after_cursor, limit)

    async def claim_observation(self, *, worker_id: object) -> object:
        return self._return(worker_id)

    async def record_observation_page(self, claim: object, page: object) -> object:
        return self._return(claim, page)

    async def defer_observation(self, claim: object, reason: object) -> object:
        return self._return(claim, reason)

    async def purge_observation_gaps(self, *, scope_limit: object) -> object:
        return self._return(scope_limit)


class _Transaction:
    def __init__(self, result: object, faults: dict[str, BaseException] | None = None) -> None:
        self.events: list[str] = []
        self.observation = _Repository(result, self._step)
        self._faults = faults or {}

    def _step(self, name: str) -> None:
        self.events.append(name)
        if name in self._faults:
            raise self._faults[name]

    async def __aenter__(self) -> _Transaction:
        self._step("enter")
        return self

    async def __aexit__(self, *args: object) -> None:
        del args
        self._step("exit")

    async def commit(self) -> None:
        self._step("commit")

    async def rollback(self) -> None:
        self._step("rollback")


def _adapter(transaction: _Transaction) -> TransactionalObservationStore:
    return TransactionalObservationStore(lambda: cast(PostgresObservationUnitOfWork, transaction))


async def _invoke(adapter: TransactionalObservationStore, operation: Operation) -> object:
    snapshot, _, claim = claimed_scan()
    if operation == "configure":
        return await adapter.configure_observation(
            ConfigureObservation(SCOPE, 2, snapshot.configuration, "operation-1", "admin-1")
        )
    if operation == "status":
        return await adapter.observation_status(SCOPE)
    if operation == "gaps":
        return await adapter.observation_gaps(SCOPE, after_cursor="a" * 64, limit=5)
    if operation == "claim":
        return await adapter.claim_observation(worker_id="a" * 64)
    if operation == "record":
        page = ProviderObservationPage(
            ProviderRunDiscoveryPage(SCOPE, claim.cursor.window, 1, 0, (), "exhausted"), ()
        )
        return await adapter.record_observation_page(claim, page)
    if operation == "defer":
        return await adapter.defer_observation(claim, "provider_unavailable")
    return await adapter.purge_observation_gaps(scope_limit=4)


_SNAPSHOT, _, _CLAIM = claimed_scan()
_RESULTS: tuple[tuple[Operation, object, bool], ...] = (
    ("configure", ObservationCommitted(observation(), False), True),
    ("configure", ObservationCommitted(observation(), True), False),
    ("configure", ObservationConflict("revision_conflict"), False),
    ("status", ObservationStatus(SCOPE, None, (), 0, None, NOW), False),
    ("gaps", ObservationGapPage(SCOPE, (), None, NOW), False),
    ("claim", ClaimedObservation(_SNAPSHOT, _CLAIM), True),
    ("claim", None, False),
    ("record", "applied", True),
    ("record", "capacity_reached", True),
    ("record", "claim_lost", False),
    ("defer", "applied", True),
    ("defer", "claim_lost", False),
    ("purge", 1, True),
    ("purge", 0, False),
)


@pytest.mark.parametrize("operation,result,commit", _RESULTS)
def test_public_result_follows_required_commit_and_finalization(
    operation: Operation, result: object, commit: bool
) -> None:
    transaction = _Transaction(result)
    returned = asyncio.run(_invoke(_adapter(transaction), operation))
    transaction.events.append("returned")
    assert returned is result
    assert transaction.events == [
        "enter",
        "operation",
        *(["commit"] if commit else []),
        "exit",
        "returned",
    ]
    assert transaction.observation.arguments


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("stage", ["enter", "operation", "exit"])
def test_transaction_failure_is_not_a_success(operation: Operation, stage: str) -> None:
    error = StoreUnavailable("private connection detail")
    transaction = _Transaction(None, {stage: error})
    with pytest.raises(CiEconomicsStoreUnavailable) as caught:
        asyncio.run(_invoke(_adapter(transaction), operation))
    assert caught.value.__cause__ is error
    assert "private connection detail" not in str(caught.value)


@pytest.mark.parametrize("operation,result,commit", [case for case in _RESULTS if case[2]])
@pytest.mark.parametrize("stage", ["commit", "exit"])
def test_no_positive_write_receipt_survives_commit_or_cleanup_failure(
    operation: Operation, result: object, commit: bool, stage: str
) -> None:
    assert commit
    error = (
        CommitOutcomeUnknown("unknown commit")
        if stage == "commit"
        else CommittedButCleanupFailed("failed finalization")
    )
    transaction = _Transaction(result, {stage: error})
    with pytest.raises(CiEconomicsStoreUnavailable) as caught:
        asyncio.run(_invoke(_adapter(transaction), operation))
    assert caught.value.__cause__ is error
    assert transaction.events.count("commit") == 1


@pytest.mark.parametrize("operation", ["claim", "record", "defer"])
@pytest.mark.parametrize("rollback_fails", [False, True])
def test_lease_loss_requires_successful_rollback(
    operation: Operation, rollback_fails: bool
) -> None:
    faults: dict[str, BaseException] = {"operation": ObservationLeaseExpired()}
    if rollback_fails:
        faults["rollback"] = StoreUnavailable("rollback failed")
    transaction = _Transaction(None, faults)
    if rollback_fails:
        with pytest.raises(CiEconomicsStoreUnavailable):
            asyncio.run(_invoke(_adapter(transaction), operation))
    else:
        result = asyncio.run(_invoke(_adapter(transaction), operation))
        assert result == (None if operation == "claim" else "claim_lost")
    assert transaction.events == ["enter", "operation", "rollback", "exit"]


@pytest.mark.parametrize("operation", _OPERATIONS)
def test_cancellation_is_not_reclassified_as_storage_failure(operation: Operation) -> None:
    error = asyncio.CancelledError("cancelled")
    transaction = _Transaction(None, {"operation": error})
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(_invoke(_adapter(transaction), operation))
    assert caught.value is error
    assert "commit" not in transaction.events


def test_commit_cancellation_keeps_unknown_outcome_identity() -> None:
    error = CommitCancelledOutcomeUnknown("unknown commit")
    transaction = _Transaction(ObservationCommitted(observation(), False), {"commit": error})
    with pytest.raises(CommitCancelledOutcomeUnknown) as caught:
        asyncio.run(_invoke(_adapter(transaction), "configure"))
    assert caught.value is error
