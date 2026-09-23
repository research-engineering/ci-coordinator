"""Registration shares activation's scope lock without moving policy into reconciliation."""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.operator_override_repository import (
    PostgresOperatorOverrideRepository,
)
from ci_coordinator.persistence.production_cutover_state import production_database_now
from ci_coordinator.persistence.production_issuance_guard import production_guard_rejection
from ci_coordinator.persistence.reconciliation_pair_repository import (
    _PostgresReconciliationPairRepository,
)
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.reconciliation import (
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationSubject,
    SubjectRegistered,
    SubjectRegistrationDuplicate,
    initial_convergence_state,
)


class PostgresProductionRegistrationRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        pairs: _PostgresReconciliationPairRepository,
        config_epochs: _PostgresConfigEpochRepository,
        overrides: PostgresOperatorOverrideRepository,
        compatibility: CompatibilityProfile,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._pairs = pairs
        self._config_epochs = config_epochs
        self._overrides = overrides
        self._compatibility = compatibility
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def register(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        policy: ReconciliationConvergencePolicy,
        guard: ProductionIssuanceGuard | None,
    ) -> bool:
        self._ensure_active()
        scope = RepositoryScope(subject.installation_id, subject.repository_id)
        if guard is not None and (
            type(guard) is not ProductionIssuanceGuard
            or guard.scope != scope
            or guard.reconciliation_subject_id != subject.subject_id
            or contract.planning_evidence is None
            or contract.planning_evidence.config_epoch_id != guard.config_epoch_id
            or contract.planning_evidence.verified_plan_id != guard.execution_plan_id
        ):
            return False
        if guard is None and contract.omitted_signals:
            return False
        try:
            await lock_repository_scope(self._connection, scope)
            now = await production_database_now(self._connection)
            if (
                guard is not None
                and await production_guard_rejection(
                    self._connection,
                    guard,
                    now=now,
                    config_epochs=self._config_epochs,
                    operator_overrides=self._overrides,
                    compatibility_profile=self._compatibility,
                )
                is not None
            ):
                return False
            result = await self._pairs.register_subject(
                subject,
                contract,
                initial_convergence_state(now, policy),
                occurred_at=now,
                execution_origin="full_ci" if guard is None else "selected",
                production_guard=guard,
            )
            if guard is not None:
                after = await production_database_now(self._connection)
                if (
                    guard.current_evidence is None
                    or not guard.current_evidence.is_current_at(after)
                    or after >= guard.not_after
                ):
                    self._mark_rollback_required()
                    return False
            return isinstance(result, SubjectRegistered | SubjectRegistrationDuplicate)
        except BaseException:
            self._mark_rollback_required()
            raise
