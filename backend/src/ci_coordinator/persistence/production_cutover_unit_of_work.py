from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.operator_override_repository import (
    PostgresOperatorOverrideRepository,
)
from ci_coordinator.persistence.production_admission_repository import (
    _PostgresProductionAdmissionRepository,
)
from ci_coordinator.persistence.production_cutover_repository import (
    PostgresProductionCutoverRepository,
)
from ci_coordinator.persistence.production_registration_repository import (
    PostgresProductionRegistrationRepository,
)
from ci_coordinator.persistence.reconciliation_pair_repository import (
    _PostgresReconciliationPairRepository,
)
from ci_coordinator.persistence.reconciliation_state_repository import (
    _PostgresReconciliationRepository,
)
from ci_coordinator.persistence.schema_capabilities import production_cutover_requirements
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    load_bundled_shadow_reconciliation_state_profile,
)
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork


class PostgresProductionCutoverUnitOfWork(PostgresUnitOfWork):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = production_cutover_requirements(self._profile)
        self._cutover: PostgresProductionCutoverRepository | None = None
        self._registrations: PostgresProductionRegistrationRepository | None = None

    @property
    def cutover(self) -> PostgresProductionCutoverRepository:
        self._require_active()
        return self._require_initialized(self._cutover, "production cutover repository")

    @property
    def registrations(self) -> PostgresProductionRegistrationRepository:
        self._require_active()
        return self._require_initialized(self._registrations, "production registration repository")

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        audit = self._require_initialized(self._audit_events, "audit event repository")
        config_epochs = _PostgresConfigEpochRepository(
            connection, audit, self._require_active, self._mark_rollback_required
        )
        overrides = PostgresOperatorOverrideRepository(
            connection, audit, self._profile, self._require_active, self._mark_rollback_required
        )
        state_profile = load_bundled_shadow_reconciliation_state_profile()
        self._cutover = PostgresProductionCutoverRepository(
            connection,
            audit,
            _PostgresProductionAdmissionRepository(
                connection, self._require_active, self._mark_rollback_required
            ),
            config_epochs,
            overrides,
            state_profile,
            self._require_active,
            self._mark_rollback_required,
        )
        self._registrations = PostgresProductionRegistrationRepository(
            connection,
            _PostgresReconciliationPairRepository(
                _PostgresReconciliationRepository(
                    connection, state_profile, self._require_active, self._mark_rollback_required
                ),
                audit,
                self._mark_rollback_required,
            ),
            config_epochs,
            overrides,
            self._profile,
            self._require_active,
            self._mark_rollback_required,
        )
