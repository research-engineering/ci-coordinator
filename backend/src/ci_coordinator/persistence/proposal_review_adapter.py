"""Transaction-scoped adapter for the proposal-review application port."""

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
from ci_coordinator.persistence.proposal_review_unit_of_work import (
    PostgresProposalReviewUnitOfWork,
)
from ci_coordinator.proposal_review import (
    AttestedConfigActivationResult,
    PreparedProposalReview,
    ProposalReviewCommand,
    ProposalReviewRecord,
    ProposalReviewResolution,
    ProposalReviewStoreUnavailable,
    ProposalReviewWriteResult,
    RepositoryActivationAuthority,
    RepositoryAttestationRegistration,
    RepositoryAttestationTransaction,
)

type ProposalReviewUnitOfWorkFactory = Callable[[], PostgresProposalReviewUnitOfWork]


class TransactionalProposalReviewStore:
    """Give each proposal-review read or write one admitted transaction."""

    def __init__(self, unit_of_work: ProposalReviewUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def current_time(self) -> datetime:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.proposal_reviews.current_time()
        except PersistenceError as error:
            raise ProposalReviewStoreUnavailable("proposal review store is unavailable") from error

    async def register_attestation(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> RepositoryAttestationRegistration:
        try:
            async with self._unit_of_work() as unit_of_work:
                result = await unit_of_work.proposal_reviews.register_attestation(transaction)
                await unit_of_work.commit()
                return result
        except PersistenceError as error:
            raise ProposalReviewStoreUnavailable("proposal review store is unavailable") from error

    async def attestation_is_pending(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> bool:
        try:
            async with self._unit_of_work() as unit_of_work:
                return await unit_of_work.proposal_reviews.attestation_is_pending(transaction)
        except PersistenceError as error:
            raise ProposalReviewStoreUnavailable("proposal review store is unavailable") from error

    async def retire_attestation(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> None:
        try:
            async with self._unit_of_work() as unit_of_work:
                await unit_of_work.proposal_reviews.retire_attestation(transaction)
                await unit_of_work.commit()
        except PersistenceError as error:
            raise ProposalReviewStoreUnavailable("proposal review store is unavailable") from error

    async def resolve_operation(
        self,
        command: ProposalReviewCommand,
    ) -> ProposalReviewResolution:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.proposal_reviews.resolve_operation(command)
        except PersistenceError as error:
            raise ProposalReviewStoreUnavailable("proposal review store is unavailable") from error

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.proposal_reviews.load_active(scope)
        except PersistenceError as error:
            raise ProposalReviewStoreUnavailable("proposal review store is unavailable") from error

    async def load_activation_candidate(
        self,
        *,
        scope: RepositoryScope,
        target_epoch_id: str,
        proposal_manifest_id: str,
    ) -> ProposalReviewRecord | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.proposal_reviews.load_activation_candidate(
                    scope=scope,
                    target_epoch_id=target_epoch_id,
                    proposal_manifest_id=proposal_manifest_id,
                )
        except PersistenceError as error:
            raise ProposalReviewStoreUnavailable("proposal review store is unavailable") from error

    async def activate_config(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RepositoryActivationAuthority,
    ) -> AttestedConfigActivationResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.proposal_reviews.activate_config(prepared, authority)
                await transaction.commit()
                return result
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable(
                "attested config activation is unavailable"
            ) from error

    async def accept(self, prepared: PreparedProposalReview) -> ProposalReviewWriteResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.proposal_reviews.accept(prepared)
                await transaction.commit()
                return result
        except PersistenceError as error:
            raise ProposalReviewStoreUnavailable("proposal review store is unavailable") from error
