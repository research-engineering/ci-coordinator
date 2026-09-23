from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.compatibility_contracts import required_capabilities
from ci_coordinator.persistence.schema_capabilities import (
    ANALYTICS_PURPOSE_SETTINGS,
    ci_history_requirements,
)
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork


class PostgresHistoryUnitOfWork(PostgresUnitOfWork):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = ci_history_requirements(self._profile)

    @property
    def history_connection(self) -> AsyncConnection:
        self._require_active()
        return self._require_initialized(self._connection, "history connection")

    @property
    def history_audit(self) -> _PostgresAuditEventRepository:
        self._require_active()
        return self._require_initialized(self._audit_events, "history audit")


class PostgresPurposeUnitOfWork(PostgresHistoryUnitOfWork):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = required_capabilities(
            self._profile, (*self._required_capabilities, ANALYTICS_PURPOSE_SETTINGS.declaration())
        )
