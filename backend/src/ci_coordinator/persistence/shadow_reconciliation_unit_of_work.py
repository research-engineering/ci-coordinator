from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence.reconciliation_pair_repository import (
    _PostgresReconciliationPairRepository,
)
from ci_coordinator.persistence.reconciliation_state_repository import (
    _PostgresReconciliationRepository,
)
from ci_coordinator.persistence.schema_capabilities import (
    shadow_reconciliation_state_requirements,
)
from ci_coordinator.persistence.shadow_evidence_repository import _PostgresShadowEvidenceRepository
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    load_bundled_shadow_reconciliation_state_profile,
)
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork
from ci_coordinator.shadow_mode import ShadowEvidencePersistence


class PostgresShadowReconciliationUnitOfWork(PostgresUnitOfWork):
    """Expose durable shadow and reconciliation ports after exact capability admission."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = shadow_reconciliation_state_requirements(self._profile)
        self._state_profile = load_bundled_shadow_reconciliation_state_profile()
        self._shadow_evidence: _PostgresShadowEvidenceRepository | None = None
        self._reconciliation: _PostgresReconciliationRepository | None = None
        self._reconciliation_pairs: _PostgresReconciliationPairRepository | None = None

    @property
    def shadow_evidence(self) -> ShadowEvidencePersistence:
        self._require_active()
        return self._require_initialized(self._shadow_evidence, "shadow evidence repository")

    @property
    def reconciliation(self) -> _PostgresReconciliationRepository:
        self._require_active()
        return self._require_initialized(self._reconciliation, "reconciliation repository")

    @property
    def reconciliation_pairs(self) -> _PostgresReconciliationPairRepository:
        self._require_active()
        return self._require_initialized(
            self._reconciliation_pairs, "reconciliation pair repository"
        )

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        audit_events = self._require_initialized(self._audit_events, "audit event repository")
        self._shadow_evidence = _PostgresShadowEvidenceRepository(
            connection,
            self._state_profile,
            self._require_active,
            self._mark_rollback_required,
        )
        self._reconciliation = _PostgresReconciliationRepository(
            connection,
            self._state_profile,
            self._require_active,
            self._mark_rollback_required,
        )
        self._reconciliation_pairs = _PostgresReconciliationPairRepository(
            self._reconciliation,
            audit_events,
            self._mark_rollback_required,
        )
