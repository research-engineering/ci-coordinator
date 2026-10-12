from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.registered_epoch_review_repository import (
    _PostgresRegisteredEpochReviewRepository,
)
from ci_coordinator.persistence.schema_capabilities import registered_epoch_review_requirements
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork


class PostgresRegisteredEpochReviewUnitOfWork(PostgresUnitOfWork):
    """Expose registered-epoch review only after its complete capability is admitted."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = registered_epoch_review_requirements(self._profile)
        self._registered_epoch_reviews: _PostgresRegisteredEpochReviewRepository | None = None

    @property
    def registered_epoch_reviews(self) -> _PostgresRegisteredEpochReviewRepository:
        self._require_active()
        return self._require_initialized(
            self._registered_epoch_reviews,
            "registered-epoch review repository",
        )

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        audit_events = self._require_initialized(self._audit_events, "audit event repository")
        config_epochs = _PostgresConfigEpochRepository(
            connection,
            audit_events,
            self._require_active,
            self._mark_rollback_required,
        )
        self._registered_epoch_reviews = _PostgresRegisteredEpochReviewRepository(
            connection,
            audit_events,
            config_epochs,
            self._require_active,
            self._mark_rollback_required,
        )
