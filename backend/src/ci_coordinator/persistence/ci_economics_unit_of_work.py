"""Compatibility-admitted unit of work for CI economics evidence."""

from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.ci_economics import load_bundled_ci_economics_profile
from ci_coordinator.persistence.ci_economics_budget_repository import (
    _PostgresBudgetPolicyRepository,
)
from ci_coordinator.persistence.ci_economics_budget_signals import _PostgresBudgetSignalRepository
from ci_coordinator.persistence.ci_economics_collection_repository import (
    _PostgresCiEconomicsCollectionRepository,
)
from ci_coordinator.persistence.ci_economics_repository import (
    _PostgresCiEconomicsRepository,
)
from ci_coordinator.persistence.ci_measurement_report_repository import (
    _PostgresCiMeasurementReportRepository,
)
from ci_coordinator.persistence.schema_capabilities import ci_economics_evidence_requirements
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    load_bundled_shadow_reconciliation_state_profile,
)
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork


class PostgresCiEconomicsUnitOfWork(PostgresUnitOfWork):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = ci_economics_evidence_requirements(self._profile)
        self._ci_economics: _PostgresCiEconomicsRepository | None = None
        self._ci_economics_collection: _PostgresCiEconomicsCollectionRepository | None = None
        self._ci_measurement_reports: _PostgresCiMeasurementReportRepository | None = None
        self._budget_policies: _PostgresBudgetPolicyRepository | None = None
        self._budget_signals: _PostgresBudgetSignalRepository | None = None
        self._reconciliation_profile = load_bundled_shadow_reconciliation_state_profile()
        self._ci_economics_profile = load_bundled_ci_economics_profile()

    @property
    def budget_policies(self) -> _PostgresBudgetPolicyRepository:
        self._require_active()
        return self._require_initialized(self._budget_policies, "budget policies")

    @property
    def budget_signals(self) -> _PostgresBudgetSignalRepository:
        self._require_active()
        return self._require_initialized(self._budget_signals, "budget signals")

    @property
    def ci_measurement_reports(self) -> _PostgresCiMeasurementReportRepository:
        self._require_active()
        return self._require_initialized(self._ci_measurement_reports, "CI measurement reports")

    @property
    def ci_economics(self) -> _PostgresCiEconomicsRepository:
        self._require_active()
        return self._require_initialized(self._ci_economics, "CI economics repository")

    @property
    def ci_economics_collection(self) -> _PostgresCiEconomicsCollectionRepository:
        self._require_active()
        return self._require_initialized(
            self._ci_economics_collection,
            "CI economics collection repository",
        )

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        self._budget_policies = _PostgresBudgetPolicyRepository(
            connection,
            self._require_initialized(self._audit_events, "audit repository"),
            self._require_active,
            self._mark_rollback_required,
        )
        self._budget_signals = _PostgresBudgetSignalRepository(
            connection,
            self._require_active,
            self._mark_rollback_required,
        )
        self._ci_economics = _PostgresCiEconomicsRepository(
            connection,
            self._require_active,
            self._mark_rollback_required,
        )
        self._ci_measurement_reports = _PostgresCiMeasurementReportRepository(
            connection,
            self._require_active,
            self._mark_rollback_required,
            budget_policies=self._budget_policies,
            budget_signals=self._budget_signals,
        )
        self._ci_economics_collection = _PostgresCiEconomicsCollectionRepository(
            connection,
            self._ci_economics,
            self._reconciliation_profile,
            self._ci_economics_profile.collection_policy,
            self._ci_economics_profile.maximum_terminalizations_per_claim,
            self._require_active,
            self._mark_rollback_required,
        )
