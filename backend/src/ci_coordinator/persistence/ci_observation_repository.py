import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
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
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.ci_economics_collection_repository import (
    _PostgresCiEconomicsCollectionRepository,
)
from ci_coordinator.persistence.ci_observation_claims import claim_observation
from ci_coordinator.persistence.ci_observation_completion import (
    defer_observation,
    record_observation_page,
)
from ci_coordinator.persistence.ci_observation_config import configure_observation
from ci_coordinator.persistence.ci_observation_queries import (
    observation_gaps,
    observation_status,
    purge_observation_gaps,
)
from ci_coordinator.persistence.ci_observation_scan_state import ObservationLeaseExpired
from ci_coordinator.persistence.errors import PersistenceError


class _PostgresObservationRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        audit: _PostgresAuditEventRepository,
        collections: _PostgresCiEconomicsCollectionRepository,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit = audit
        self._collections = collections
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def configure_observation(self, command: ConfigureObservation) -> ObservationWriteResult:
        async with self._operation():
            return await configure_observation(self._connection, self._audit, command)

    async def observation_status(self, scope: RepositoryScope) -> ObservationStatus:
        async with self._operation():
            return await observation_status(self._connection, scope)

    async def observation_gaps(
        self, scope: RepositoryScope, *, after_cursor: str | None, limit: int
    ) -> ObservationGapPage:
        async with self._operation():
            return await observation_gaps(
                self._connection, scope, after_cursor=after_cursor, limit=limit
            )

    async def claim_observation(self, *, worker_id: str) -> ClaimedObservation | None:
        async with self._operation():
            return await claim_observation(self._connection, worker_id)

    async def record_observation_page(
        self, claim: ObservationClaim, page: ProviderObservationPage
    ) -> ObservationTransitionResult:
        async with self._operation():
            return await record_observation_page(self._connection, self._collections, claim, page)

    async def defer_observation(
        self, claim: ObservationClaim, reason: ObservationFailure
    ) -> ObservationTransitionResult:
        async with self._operation():
            return await defer_observation(self._connection, claim, reason)

    async def purge_observation_gaps(self, *, scope_limit: int) -> int:
        async with self._operation():
            return await purge_observation_gaps(self._connection, scope_limit=scope_limit)

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        self._ensure_active()
        try:
            yield
        except (asyncio.CancelledError, ObservationLeaseExpired):
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise CiEconomicsStoreUnavailable("observation storage unavailable") from error
