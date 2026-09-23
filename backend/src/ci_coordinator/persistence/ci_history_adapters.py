from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.archive_detail import ArchivedAttemptDetail
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.history_administration import HistoryStatus
from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigurationResult,
    HistoryConfigured,
)
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_gap_recovery import (
    HistoryGapRepairResult,
    RepairHistoryGaps,
)
from ci_coordinator.ci_economics.history_ports import (
    ClaimedHistoryRecheck,
    HistoryDeferral,
    HistoryDeliveryTransfer,
    HistoryTransition,
)
from ci_coordinator.ci_economics.history_rechecks import (
    HistoryRecheckClaim,
    HistoryRecheckHint,
    HistoryRecheckSource,
)
from ci_coordinator.ci_economics.history_scan import HistoryClaim, HistoryScanLane
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_history_claims import claim_history
from ci_coordinator.persistence.ci_history_completion import (
    complete_history_page,
    complete_history_statistics,
    complete_unavailable_history_attempt,
    complete_unselected_history_run,
    defer_history_claim,
    handoff_discovered_history_run,
)
from ci_coordinator.persistence.ci_history_configuration_store import configure_history
from ci_coordinator.persistence.ci_history_delivery_transfer import (
    list_history_delivery_scopes,
    transfer_history_deliveries,
)
from ci_coordinator.persistence.ci_history_detail_cleanup import expire_history_details
from ci_coordinator.persistence.ci_history_gap_recovery import repair_history_gaps
from ci_coordinator.persistence.ci_history_queries import history_status
from ci_coordinator.persistence.ci_history_recheck_claims import (
    claim_history_recheck,
)
from ci_coordinator.persistence.ci_history_recheck_completion import (
    complete_recheck_failure,
    complete_recheck_statistics,
    wait_for_history_attempt,
)
from ci_coordinator.persistence.ci_history_recheck_rows import (
    RecheckAdmission,
    enqueue_history_recheck,
)
from ci_coordinator.persistence.ci_history_state_store import HistoryWriteConflict
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresHistoryUnitOfWork
from ci_coordinator.persistence.errors import PersistenceError


class TransactionalHistoryStore:
    def __init__(self, unit_of_work: Callable[[], PostgresHistoryUnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    async def configure_history(self, command: ConfigureHistory) -> HistoryConfigurationResult:
        async with self._transaction() as transaction:
            result = await configure_history(
                transaction.history_connection, transaction.history_audit, command
            )
            if isinstance(result, HistoryConfigured) and not result.replayed:
                await transaction.commit()
            return result

    async def history_status(self, scope: RepositoryScope) -> HistoryStatus:
        async with self._transaction() as transaction:
            return await history_status(transaction.history_connection, scope)

    async def expire_details(self) -> int:
        async with self._transaction() as transaction:
            expired = await expire_history_details(transaction.history_connection)
            if expired:
                await transaction.commit()
            return expired

    async def repair_history_gaps(self, command: RepairHistoryGaps) -> HistoryGapRepairResult:
        async with self._transaction() as transaction:
            result = await repair_history_gaps(
                transaction.history_connection, transaction.history_audit, command
            )
            if result.outcome == "committed":
                await transaction.commit()
            return result

    async def claim_history(
        self, *, worker_id: str, lane: HistoryScanLane = "backfill"
    ) -> tuple[HistoryDataset, HistoryClaim] | None:
        try:
            async with self._transaction() as transaction:
                result = await claim_history(transaction.history_connection, worker_id, lane=lane)
                if result is not None:
                    await transaction.commit()
                return result
        except HistoryWriteConflict:
            return None

    async def record_history_page(
        self, claim: HistoryClaim, page: ProviderObservationPage
    ) -> HistoryTransition:
        return await self._complete(
            lambda connection: complete_history_page(connection, claim, page)
        )

    async def record_history_statistics(
        self,
        claim: HistoryClaim,
        statistics: ArchivedAttemptStatistics,
        *,
        detail: ArchivedAttemptDetail | None = None,
    ) -> HistoryTransition:
        if detail is None:
            return await self._complete(
                lambda connection: complete_history_statistics(connection, claim, statistics)
            )
        return await self._complete(
            lambda connection: complete_history_statistics(
                connection, claim, statistics, detail=detail
            )
        )

    async def record_unavailable_history_attempt(self, claim: HistoryClaim) -> HistoryTransition:
        return await self._complete(
            lambda connection: complete_unavailable_history_attempt(connection, claim)
        )

    async def skip_unselected_history_run(self, claim: HistoryClaim) -> HistoryTransition:
        return await self._complete(
            lambda connection: complete_unselected_history_run(connection, claim)
        )

    async def defer_history(
        self,
        claim: HistoryClaim,
        reason: HistoryDeferral,
    ) -> HistoryTransition:
        return await self._complete(
            lambda connection: defer_history_claim(connection, claim, reason)
        )

    async def handoff_discovered_history_run(self, claim: HistoryClaim) -> HistoryTransition:
        return await self._complete(
            lambda connection: handoff_discovered_history_run(connection, claim)
        )

    async def _complete(
        self, operation: Callable[[AsyncConnection], Awaitable[HistoryTransition]]
    ) -> HistoryTransition:
        try:
            async with self._transaction() as transaction:
                result = await operation(transaction.history_connection)
                if result != "claim_lost":
                    await transaction.commit()
                return result
        except HistoryWriteConflict:
            return "claim_lost"

    async def enqueue_recheck(self, hint: HistoryRecheckHint) -> RecheckAdmission:
        async with self._transaction() as transaction:
            result = await enqueue_history_recheck(transaction.history_connection, hint)
            if result == "admitted":
                await transaction.commit()
            return result

    async def transfer_deliveries(self, scope: RepositoryScope) -> HistoryDeliveryTransfer:
        try:
            async with self._transaction() as transaction:
                result = await transfer_history_deliveries(transaction.history_connection, scope)
                if result.transferred_count:
                    await transaction.commit()
                return result
        except HistoryWriteConflict:
            return HistoryDeliveryTransfer("deferred", 0, 0)

    async def list_delivery_scopes(self) -> tuple[RepositoryScope, ...]:
        async with self._transaction() as transaction:
            return await list_history_delivery_scopes(transaction.history_connection)

    async def claim_recheck(
        self, *, worker_id: str, source: HistoryRecheckSource
    ) -> ClaimedHistoryRecheck:
        try:
            async with self._transaction() as transaction:
                result = await claim_history_recheck(
                    transaction.history_connection, worker_id, source
                )
                if isinstance(result, HistoryRecheckClaim) or result == "recovered":
                    await transaction.commit()
                return result
        except HistoryWriteConflict:
            return None

    async def record_recheck_statistics(
        self,
        claim: HistoryRecheckClaim,
        statistics: ArchivedAttemptStatistics,
        *,
        detail: ArchivedAttemptDetail | None = None,
    ) -> HistoryTransition:
        if detail is None:
            return await self._complete(
                lambda connection: complete_recheck_statistics(connection, claim, statistics)
            )
        return await self._complete(
            lambda connection: complete_recheck_statistics(
                connection, claim, statistics, detail=detail
            )
        )

    async def record_recheck_failure(
        self, claim: HistoryRecheckClaim, *, missing: bool
    ) -> HistoryTransition:
        return await self._complete(
            lambda connection: complete_recheck_failure(connection, claim, missing=missing)
        )

    async def wait_for_history_attempt(self, claim: HistoryRecheckClaim) -> HistoryTransition:
        return await self._complete(lambda connection: wait_for_history_attempt(connection, claim))

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[PostgresHistoryUnitOfWork]:
        try:
            async with self._unit_of_work() as transaction:
                yield transaction
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable("history transaction unavailable") from error
