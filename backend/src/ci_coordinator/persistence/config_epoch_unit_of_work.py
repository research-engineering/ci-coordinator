from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.config_epochs import ConfigEpochRepository
from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.schema_capabilities import config_epoch_registration_requirements
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork


class PostgresConfigEpochUnitOfWork(PostgresUnitOfWork):
    """Expose config lifecycle storage only after its full capability set is admitted."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = config_epoch_registration_requirements(self._profile)
        self._config_epochs: _PostgresConfigEpochRepository | None = None

    @property
    def config_epochs(self) -> ConfigEpochRepository:
        self._require_active()
        return self._require_initialized(self._config_epochs, "config epoch repository")

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        audit_events = self._require_initialized(self._audit_events, "audit event repository")
        self._config_epochs = _PostgresConfigEpochRepository(
            connection,
            audit_events,
            self._require_active,
            self._mark_rollback_required,
        )
