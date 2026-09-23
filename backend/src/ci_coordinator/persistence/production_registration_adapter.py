from collections.abc import Callable

from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.reconciliation import (
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationSubject,
)


class TransactionalProductionRegistrationStore:
    def __init__(
        self,
        unit_of_work: Callable[[], PostgresProductionCutoverUnitOfWork],
        policy: ReconciliationConvergencePolicy,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy

    async def register_selected(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        guard: ProductionIssuanceGuard,
    ) -> bool:
        if type(guard) is not ProductionIssuanceGuard:
            return False
        return await self._register(subject, contract, guard)

    async def register_full_ci(
        self, subject: ReconciliationSubject, contract: ReconciliationContract
    ) -> bool:
        return await self._register(subject, contract, None)

    async def _register(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        guard: ProductionIssuanceGuard | None,
    ) -> bool:
        async with self._unit_of_work() as transaction:
            result = await transaction.registrations.register(
                subject, contract, self._policy, guard
            )
            if result:
                await transaction.commit()
            return result
