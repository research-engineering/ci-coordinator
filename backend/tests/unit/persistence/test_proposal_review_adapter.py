from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal, cast

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence import CommitCancelledOutcomeUnknown, CommitOutcomeUnknown
from ci_coordinator.persistence.errors import StoreUnavailable
from ci_coordinator.persistence.proposal_review_adapter import TransactionalProposalReviewStore
from ci_coordinator.proposal_review import (
    PreparedProposalReview,
    ProposalReviewCommand,
    ProposalReviewStoreUnavailable,
    RepositoryAttestationTransaction,
)

type _Operation = Literal[
    "resolve_operation",
    "load_active",
    "register_attestation",
    "attestation_is_pending",
    "retire_attestation",
    "accept",
]
_OPERATIONS: tuple[_Operation, ...] = (
    "resolve_operation",
    "load_active",
    "register_attestation",
    "attestation_is_pending",
    "retire_attestation",
    "accept",
)


class _Repository:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[str] = []

    async def resolve_operation(self, command: object) -> object:
        del command
        return self._return("resolve_operation")

    async def load_active(self, scope: object) -> object:
        del scope
        return self._return("load_active")

    async def accept(self, prepared: object) -> object:
        del prepared
        return self._return("accept")

    async def retire_attestation(self, attestation: object) -> object:
        del attestation
        return self._return("retire_attestation")

    async def register_attestation(self, transaction: object) -> object:
        del transaction
        return self._return("register_attestation")

    async def attestation_is_pending(self, transaction: object) -> object:
        del transaction
        return self._return("attestation_is_pending")

    def _return(self, operation: str) -> object:
        self.calls.append(operation)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _Transaction:
    def __init__(
        self,
        result: object,
        *,
        commit_error: BaseException | None = None,
    ) -> None:
        self.proposal_reviews = _Repository(result)
        self._commit_error = commit_error
        self.commits = 0

    async def __aenter__(self) -> _Transaction:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def commit(self) -> None:
        self.commits += 1
        if self._commit_error is not None:
            raise self._commit_error


@pytest.mark.parametrize("operation", _OPERATIONS)
def test_store_translates_persistence_errors(operation: _Operation) -> None:
    persistence_error = StoreUnavailable("database-dsn must stay private")
    transaction = _Transaction(persistence_error)
    adapter = TransactionalProposalReviewStore(_factory(transaction))

    with pytest.raises(ProposalReviewStoreUnavailable) as caught:
        asyncio.run(_invoke(adapter, operation))

    assert caught.value.__cause__ is persistence_error
    assert transaction.proposal_reviews.calls == [operation]
    assert "database-dsn" not in str(caught.value)


@pytest.mark.parametrize("operation", _OPERATIONS)
def test_store_preserves_cancellation(operation: _Operation) -> None:
    cancellation = asyncio.CancelledError(f"cancelled {operation}")
    transaction = _Transaction(cancellation)
    adapter = TransactionalProposalReviewStore(_factory(transaction))

    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(_invoke(adapter, operation))

    assert caught.value is cancellation
    assert transaction.proposal_reviews.calls == [operation]


@pytest.mark.parametrize("operation", ["register_attestation", "retire_attestation", "accept"])
def test_store_commits_mutations(operation: _Operation) -> None:
    result = object()
    transaction = _Transaction(result)
    adapter = TransactionalProposalReviewStore(_factory(transaction))

    returned = asyncio.run(_invoke(adapter, operation))

    assert returned is (result if operation in {"register_attestation", "accept"} else None)
    assert transaction.commits == 1


def test_store_translates_commit_failure_without_claiming_known_rollback() -> None:
    persistence_error = CommitOutcomeUnknown("commit outcome unknown")
    transaction = _Transaction(object(), commit_error=persistence_error)
    adapter = TransactionalProposalReviewStore(_factory(transaction))

    with pytest.raises(ProposalReviewStoreUnavailable) as caught:
        asyncio.run(adapter.accept(cast(PreparedProposalReview, object())))

    assert caught.value.__cause__ is persistence_error
    assert transaction.commits == 1


def test_store_preserves_unknown_commit_cancellation() -> None:
    cancellation = CommitCancelledOutcomeUnknown("commit outcome unknown")
    transaction = _Transaction(object(), commit_error=cancellation)
    adapter = TransactionalProposalReviewStore(_factory(transaction))

    with pytest.raises(CommitCancelledOutcomeUnknown) as caught:
        asyncio.run(adapter.accept(cast(PreparedProposalReview, object())))

    assert caught.value is cancellation
    assert transaction.commits == 1


def _factory(transaction: object) -> Callable[[], Any]:
    return cast(Callable[[], Any], lambda: transaction)


async def _invoke(
    adapter: TransactionalProposalReviewStore,
    operation: _Operation,
) -> object:
    if operation == "resolve_operation":
        return await adapter.resolve_operation(cast(ProposalReviewCommand, object()))
    if operation == "load_active":
        return await adapter.load_active(RepositoryScope(1, 2))
    if operation == "register_attestation":
        return await adapter.register_attestation(
            transaction=cast(RepositoryAttestationTransaction, object())
        )
    if operation == "attestation_is_pending":
        return await adapter.attestation_is_pending(
            transaction=cast(RepositoryAttestationTransaction, object())
        )
    if operation == "retire_attestation":
        await adapter.retire_attestation(
            transaction=cast(RepositoryAttestationTransaction, object())
        )
        return None
    return await adapter.accept(cast(PreparedProposalReview, object()))
