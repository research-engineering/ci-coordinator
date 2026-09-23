from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.production_admission import ProductionAdmissionGrant
from ci_coordinator.production_admission.current_evidence import CurrentActivationEvidence
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverApplied,
    ProductionCutoverCommand,
    ProductionCutoverResult,
)
from ci_coordinator.production_admission.cutover_drain import AdmittedProductionDrain
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.ports import (
    ProductionCutoverUnavailable,
    RetainedProductionAuthority,
)
from ci_coordinator.production_admission.relation_admission import StagedProductionEvidence


class TransactionalProductionCutoverStore:
    """Each command or read uses one admitted, separately closed database transaction."""

    def __init__(self, unit_of_work: Callable[[], PostgresProductionCutoverUnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[PostgresProductionCutoverUnitOfWork]:
        try:
            async with self._unit_of_work() as transaction:
                yield transaction
        except PersistenceError as error:
            raise ProductionCutoverUnavailable from error

    async def resolve(self, command: ProductionCutoverCommand) -> ProductionCutoverResult | None:
        async with self._transaction() as transaction:
            return await transaction.cutover.resolve(command)

    async def load_evidence(self, scope: RepositoryScope, authority_id: str) -> bytes | None:
        async with self._transaction() as transaction:
            return await transaction.cutover.load_evidence(scope, authority_id)

    async def inspect(self, scope: RepositoryScope) -> ProductionScopeState | None:
        async with self._transaction() as transaction:
            return await transaction.cutover.inspect(scope)

    async def load_authority(
        self, scope: RepositoryScope, *, purpose: Literal["active", "staged"]
    ) -> RetainedProductionAuthority | None:
        async with self._transaction() as transaction:
            return await transaction.cutover.load_authority(scope, purpose=purpose)

    async def stage(
        self,
        command: ProductionCutoverCommand,
        *,
        grant: ProductionAdmissionGrant,
        evidence: StagedProductionEvidence,
    ) -> ProductionCutoverResult:
        async with self._transaction() as transaction:
            result = await transaction.cutover.stage(command, grant=grant, evidence=evidence)
            if isinstance(result, ProductionCutoverApplied):
                await transaction.commit()
            else:
                await transaction.rollback()
            return result

    async def begin(self, command: ProductionCutoverCommand) -> ProductionCutoverResult:
        async with self._transaction() as transaction:
            result = await transaction.cutover.begin(command)
            if isinstance(result, ProductionCutoverApplied):
                await transaction.commit()
            else:
                await transaction.rollback()
            return result

    async def activate(
        self,
        command: ProductionCutoverCommand,
        *,
        grant: ProductionAdmissionGrant,
        current: CurrentActivationEvidence,
        drain: AdmittedProductionDrain,
    ) -> ProductionCutoverResult:
        async with self._transaction() as transaction:
            result = await transaction.cutover.activate(
                command, grant=grant, current=current, drain=drain
            )
            if isinstance(result, ProductionCutoverApplied):
                await transaction.commit()
            else:
                await transaction.rollback()
            return result
