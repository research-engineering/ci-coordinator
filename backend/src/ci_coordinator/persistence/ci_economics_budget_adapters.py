"""Transactions for repository budget configuration and retained signal reads."""

from ci_coordinator.ci_economics.budget import ReportBudgetOutcome
from ci_coordinator.ci_economics.budget_commands import (
    BudgetPolicyCommitted,
    BudgetPolicyWriteResult,
    ConfigureBudgetPolicy,
)
from ci_coordinator.ci_economics.budget_policy import BudgetPolicySnapshot
from ci_coordinator.ci_economics.budget_signal import BudgetSignalPage
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_economics_adapters import CiEconomicsUnitOfWorkFactory
from ci_coordinator.persistence.errors import PersistenceError


class TransactionalBudgetPolicyStore:
    def __init__(self, unit_of_work: CiEconomicsUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def configure_policy(self, command: ConfigureBudgetPolicy) -> BudgetPolicyWriteResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.budget_policies.configure_policy(command)
                if isinstance(result, BudgetPolicyCommitted) and not result.replayed:
                    await transaction.commit()
                return result
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("budget policy write is unavailable") from error

    async def list_policies(self, scope: RepositoryScope) -> tuple[BudgetPolicySnapshot, ...]:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.budget_policies.list_policies(scope)
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("budget policy read is unavailable") from error

    async def list_signals(
        self,
        scope: RepositoryScope,
        *,
        after_cursor: str | None,
        limit: int,
        policy_key: str | None = None,
        revision: int | None = None,
        outcome: ReportBudgetOutcome | None = None,
    ) -> BudgetSignalPage:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.budget_signals.list_signals(
                    scope,
                    after_cursor=after_cursor,
                    limit=limit,
                    policy_key=policy_key,
                    revision=revision,
                    outcome=outcome,
                )
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("budget signal read is unavailable") from error
