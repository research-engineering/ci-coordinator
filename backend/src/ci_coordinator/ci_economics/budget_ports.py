from typing import Protocol

from ci_coordinator.ci_economics.budget import ReportBudgetOutcome
from ci_coordinator.ci_economics.budget_commands import (
    BudgetPolicyWriteResult,
    ConfigureBudgetPolicy,
)
from ci_coordinator.ci_economics.budget_policy import BudgetPolicySnapshot
from ci_coordinator.ci_economics.budget_signal import BudgetSignalPage
from ci_coordinator.config_control import RepositoryScope


class BudgetPolicyStore(Protocol):
    async def configure_policy(self, command: ConfigureBudgetPolicy) -> BudgetPolicyWriteResult: ...

    async def list_policies(self, scope: RepositoryScope) -> tuple[BudgetPolicySnapshot, ...]: ...

    async def list_signals(
        self,
        scope: RepositoryScope,
        *,
        after_cursor: str | None,
        limit: int,
        policy_key: str | None = None,
        revision: int | None = None,
        outcome: ReportBudgetOutcome | None = None,
    ) -> BudgetSignalPage: ...
