from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.governance_baseline import GovernanceBaselineStore
from ci_coordinator.persistence.governance_baseline_repository import (
    _PostgresGovernanceBaselineRepository,
)
from ci_coordinator.persistence.schema_capabilities import (
    governance_baseline_state_requirements,
)
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork


class PostgresGovernanceBaselineUnitOfWork(PostgresUnitOfWork):
    """Expose baseline state only after its complete capability is admitted."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = governance_baseline_state_requirements(self._profile)
        self._governance_baselines: _PostgresGovernanceBaselineRepository | None = None

    @property
    def governance_baselines(self) -> GovernanceBaselineStore:
        self._require_active()
        return self._require_initialized(
            self._governance_baselines,
            "governance baseline repository",
        )

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        audit_events = self._require_initialized(self._audit_events, "audit event repository")
        self._governance_baselines = _PostgresGovernanceBaselineRepository(
            connection,
            audit_events,
            self._require_active,
            self._mark_rollback_required,
        )
