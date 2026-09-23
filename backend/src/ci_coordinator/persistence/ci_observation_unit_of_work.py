from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence.ci_economics_unit_of_work import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_observation_repository import _PostgresObservationRepository
from ci_coordinator.persistence.schema_capabilities import ci_observation_requirements


class PostgresObservationUnitOfWork(PostgresCiEconomicsUnitOfWork):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = ci_observation_requirements(self._profile)
        self._observation: _PostgresObservationRepository | None = None

    @property
    def observation(self) -> _PostgresObservationRepository:
        self._require_active()
        return self._require_initialized(self._observation, "observation repository")

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        self._observation = _PostgresObservationRepository(
            self._require_initialized(self._connection, "database connection"),
            self._require_initialized(self._audit_events, "audit repository"),
            self._require_initialized(self._ci_economics_collection, "collection repository"),
            self._require_active,
            self._mark_rollback_required,
        )
