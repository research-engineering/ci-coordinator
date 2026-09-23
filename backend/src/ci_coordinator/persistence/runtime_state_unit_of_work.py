from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.operator_override_repository import (
    PostgresOperatorOverrideRepository,
)
from ci_coordinator.persistence.production_admission_repository import (
    _PostgresProductionAdmissionRepository,
)
from ci_coordinator.persistence.runtime_issuance_repository import _PostgresIssuanceRepository
from ci_coordinator.persistence.runtime_state_profile import load_bundled_runtime_state_profile
from ci_coordinator.persistence.schema_capabilities import (
    runtime_ingress_issuance_state_requirements,
)
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork
from ci_coordinator.plan_issuance import IssuanceStore


class PostgresIngressIssuanceUnitOfWork(PostgresUnitOfWork):
    """Expose durable ingress and issuance ports after exact capability admission."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = runtime_ingress_issuance_state_requirements(self._profile)
        self._issuance: _PostgresIssuanceRepository | None = None
        self._production_admissions: _PostgresProductionAdmissionRepository | None = None
        self._config_epochs: _PostgresConfigEpochRepository | None = None
        self._operator_overrides: PostgresOperatorOverrideRepository | None = None
        self._runtime_state_profile = load_bundled_runtime_state_profile()

    @property
    def issuance(self) -> IssuanceStore:
        self._require_active()
        return self._require_initialized(self._issuance, "plan issuance repository")

    @property
    def production_admissions(self) -> _PostgresProductionAdmissionRepository:
        self._require_active()
        return self._require_initialized(
            self._production_admissions,
            "production admission repository",
        )

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
        self._operator_overrides = PostgresOperatorOverrideRepository(
            connection,
            audit_events,
            self._profile,
            self._require_active,
            self._mark_rollback_required,
        )
        self._issuance = _PostgresIssuanceRepository(
            connection,
            audit_events,
            self._config_epochs,
            self._operator_overrides,
            self._profile,
            self._runtime_state_profile,
            self._require_active,
            self._mark_rollback_required,
        )
        self._production_admissions = _PostgresProductionAdmissionRepository(
            connection,
            self._require_active,
            self._mark_rollback_required,
        )
