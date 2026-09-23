"""Transaction boundary for atomic webhook delivery and workflow evidence."""

from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.github_ingestion import WebhookIngestionStore
from ci_coordinator.persistence.runtime_delivery_repository import (
    _PostgresWebhookIngestionRepository,
)
from ci_coordinator.persistence.schema_capabilities import webhook_ingestion_requirements
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork


class PostgresWebhookIngestionUnitOfWork(PostgresUnitOfWork):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = webhook_ingestion_requirements(self._profile)
        self._webhook_ingestion: _PostgresWebhookIngestionRepository | None = None

    @property
    def webhook_ingestion(self) -> WebhookIngestionStore:
        self._require_active()
        return self._require_initialized(
            self._webhook_ingestion,
            "webhook ingestion repository",
        )

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        audit_events = self._require_initialized(self._audit_events, "audit event repository")
        self._webhook_ingestion = _PostgresWebhookIngestionRepository(
            connection,
            audit_events,
            self._require_active,
            self._mark_rollback_required,
        )
