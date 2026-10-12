"""Transaction-scoped adapter for registered-epoch review and activation ports."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import (
    ActiveConfigEpochSnapshot,
    ConfigEpochStoreUnavailable,
    PreparedConfigEpochActivation,
)
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.registered_epoch_review_unit_of_work import (
    PostgresRegisteredEpochReviewUnitOfWork,
)
from ci_coordinator.proposal_review import (
    AttestedConfigActivationResult,
    PreparedRegisteredEpochReview,
    ProposalReviewStoreUnavailable,
    RegisteredEpochActivationAuthority,
    RegisteredEpochAttestationTransaction,
    RegisteredEpochLookup,
    RegisteredEpochReviewCommand,
    RegisteredEpochReviewRecord,
    RegisteredEpochReviewResolution,
    RegisteredEpochReviewWriteResult,
    RepositoryAttestationRegistration,
)

type RegisteredEpochReviewUnitOfWorkFactory = Callable[[], PostgresRegisteredEpochReviewUnitOfWork]


class TransactionalRegisteredEpochReviewStore:
    """Give each registered-epoch review read or write one admitted transaction."""

    def __init__(self, unit_of_work: RegisteredEpochReviewUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def current_time(self) -> datetime:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.registered_epoch_reviews.current_time()
        except PersistenceError as error:
            raise _unavailable() from error

    async def register_attestation(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> RepositoryAttestationRegistration:
        try:
            async with self._unit_of_work() as unit_of_work:
                result = await unit_of_work.registered_epoch_reviews.register_attestation(
                    transaction
                )
                await unit_of_work.commit()
                return result
        except PersistenceError as error:
            raise _unavailable() from error

    async def attestation_is_pending(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> bool:
        try:
            async with self._unit_of_work() as unit_of_work:
                return await unit_of_work.registered_epoch_reviews.attestation_is_pending(
                    transaction
                )
        except PersistenceError as error:
            raise _unavailable() from error

    async def retire_attestation(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> None:
        try:
            async with self._unit_of_work() as unit_of_work:
                await unit_of_work.registered_epoch_reviews.retire_attestation(transaction)
                await unit_of_work.commit()
        except PersistenceError as error:
            raise _unavailable() from error

    async def resolve_operation(
        self,
        command: RegisteredEpochReviewCommand,
    ) -> RegisteredEpochReviewResolution:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.registered_epoch_reviews.resolve_operation(command)
        except PersistenceError as error:
            raise _unavailable() from error

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.registered_epoch_reviews.load_active(scope)
        except PersistenceError as error:
            raise _unavailable() from error

    async def load_registered_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> RegisteredEpochLookup:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.registered_epoch_reviews.load_registered_epoch(
                    scope,
                    epoch_id,
                )
        except PersistenceError as error:
            raise _unavailable() from error

    async def accept(
        self,
        prepared: PreparedRegisteredEpochReview,
    ) -> RegisteredEpochReviewWriteResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.registered_epoch_reviews.accept(prepared)
                await transaction.commit()
                return result
        except PersistenceError as error:
            raise _unavailable() from error

    async def load_activation_candidate(
        self,
        *,
        scope: RepositoryScope,
        target_epoch_id: str,
        expected_source_hash: str,
        review_operation_id: str,
    ) -> RegisteredEpochReviewRecord | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.registered_epoch_reviews.load_activation_candidate(
                    scope=scope,
                    target_epoch_id=target_epoch_id,
                    expected_source_hash=expected_source_hash,
                    review_operation_id=review_operation_id,
                )
        except PersistenceError as error:
            raise _unavailable() from error

    async def activate_config(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RegisteredEpochActivationAuthority,
    ) -> AttestedConfigActivationResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.registered_epoch_reviews.activate_config(
                    prepared,
                    authority,
                )
                await transaction.commit()
                return result
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable(
                "registered-epoch config activation is unavailable"
            ) from error


def _unavailable() -> ProposalReviewStoreUnavailable:
    return ProposalReviewStoreUnavailable("registered-epoch review store is unavailable")
