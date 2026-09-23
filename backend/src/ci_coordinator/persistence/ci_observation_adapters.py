from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
    ObservationCommitted,
    ObservationWriteResult,
)
from ci_coordinator.ci_economics.observation_ports import (
    ClaimedObservation,
    ObservationGapPage,
    ObservationStatus,
    ObservationTransitionResult,
)
from ci_coordinator.ci_economics.observation_progress import ObservationFailure
from ci_coordinator.ci_economics.observation_scan import ObservationClaim
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_scan_state import ObservationLeaseExpired
from ci_coordinator.persistence.ci_observation_unit_of_work import PostgresObservationUnitOfWork
from ci_coordinator.persistence.errors import PersistenceError


class TransactionalObservationStore:
    def __init__(self, unit_of_work: Callable[[], PostgresObservationUnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    async def configure_observation(self, command: ConfigureObservation) -> ObservationWriteResult:
        async with self._transaction() as transaction:
            result = await transaction.observation.configure_observation(command)
            if isinstance(result, ObservationCommitted) and not result.replayed:
                await transaction.commit()
            return result

    async def observation_status(self, scope: RepositoryScope) -> ObservationStatus:
        async with self._transaction() as transaction:
            return await transaction.observation.observation_status(scope)

    async def observation_gaps(
        self, scope: RepositoryScope, *, after_cursor: str | None, limit: int
    ) -> ObservationGapPage:
        async with self._transaction() as transaction:
            return await transaction.observation.observation_gaps(
                scope, after_cursor=after_cursor, limit=limit
            )

    async def claim_observation(self, *, worker_id: str) -> ClaimedObservation | None:
        async with self._transaction() as transaction:
            try:
                result = await transaction.observation.claim_observation(worker_id=worker_id)
            except ObservationLeaseExpired:
                await transaction.rollback()
                return None
            if result is not None:
                await transaction.commit()
            return result

    async def record_observation_page(
        self, claim: ObservationClaim, page: ProviderObservationPage
    ) -> ObservationTransitionResult:
        async with self._transaction() as transaction:
            try:
                result = await transaction.observation.record_observation_page(claim, page)
            except ObservationLeaseExpired:
                await transaction.rollback()
                return "claim_lost"
            if result != "claim_lost":
                await transaction.commit()
            return result

    async def defer_observation(
        self, claim: ObservationClaim, reason: ObservationFailure
    ) -> ObservationTransitionResult:
        async with self._transaction() as transaction:
            try:
                result = await transaction.observation.defer_observation(claim, reason)
            except ObservationLeaseExpired:
                await transaction.rollback()
                return "claim_lost"
            if result == "applied":
                await transaction.commit()
            return result

    async def purge_observation_gaps(self, *, scope_limit: int) -> int:
        async with self._transaction() as transaction:
            result = await transaction.observation.purge_observation_gaps(scope_limit=scope_limit)
            if result:
                await transaction.commit()
            return result

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[PostgresObservationUnitOfWork]:
        try:
            async with self._unit_of_work() as transaction:
                yield transaction
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("observation transaction unavailable") from error
