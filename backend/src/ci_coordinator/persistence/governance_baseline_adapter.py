"""Transaction-scoped adapter for the governance-baseline application port."""

from __future__ import annotations

from collections.abc import Callable

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GovernanceBaselineCommand,
    GovernanceBaselineRecord,
    GovernanceBaselineResolution,
    GovernanceBaselineStoreUnavailable,
    GovernanceBaselineWriteResult,
    PreparedGovernanceBaseline,
)
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.governance_baseline_unit_of_work import (
    PostgresGovernanceBaselineUnitOfWork,
)

type GovernanceBaselineUnitOfWorkFactory = Callable[
    [],
    PostgresGovernanceBaselineUnitOfWork,
]


class TransactionalGovernanceBaselineStore:
    """Give each baseline read or write one admitted transaction."""

    def __init__(self, unit_of_work: GovernanceBaselineUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def resolve_operation(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineResolution:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.governance_baselines.resolve_operation(command)
        except PersistenceError as error:
            raise GovernanceBaselineStoreUnavailable(
                "governance baseline store is unavailable"
            ) from error

    async def load_active(
        self,
        scope: RepositoryScope,
    ) -> GovernanceBaselineRecord | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.governance_baselines.load_active(scope)
        except PersistenceError as error:
            raise GovernanceBaselineStoreUnavailable(
                "governance baseline store is unavailable"
            ) from error

    async def accept(
        self,
        prepared: PreparedGovernanceBaseline,
    ) -> GovernanceBaselineWriteResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.governance_baselines.accept(prepared)
                await transaction.commit()
                return result
        except PersistenceError as error:
            raise GovernanceBaselineStoreUnavailable(
                "governance baseline store is unavailable"
            ) from error
