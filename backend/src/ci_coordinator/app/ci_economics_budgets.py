from typing import Protocol

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics.budget import ReportBudgetOutcome
from ci_coordinator.ci_economics.budget_commands import (
    BudgetPolicyCommitted,
    BudgetPolicyConflict,
    BudgetPolicyWriteResult,
    ConfigureBudgetPolicy,
)
from ci_coordinator.ci_economics.budget_policy import (
    MAX_BUDGET_POLICIES_PER_REPOSITORY,
    BudgetPolicySnapshot,
)
from ci_coordinator.ci_economics.budget_ports import BudgetPolicyStore
from ci_coordinator.ci_economics.budget_signal import BudgetSignalPage
from ci_coordinator.ci_economics.catalog import require_catalog_digest, require_catalog_page
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.ci_economics.reports import require_report_sample_key
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type BudgetAccessFailure = CiEconomicsReadForbidden | CiEconomicsReadUnavailable
type BudgetConfigurationResult = BudgetPolicyWriteResult | BudgetAccessFailure
type BudgetPolicyListResult = tuple[BudgetPolicySnapshot, ...] | BudgetAccessFailure
type BudgetSignalListResult = BudgetSignalPage | BudgetAccessFailure


class CiEconomicsBudgetUseCase(Protocol):
    async def configure(self, command: ConfigureBudgetPolicy) -> BudgetConfigurationResult: ...

    async def list_policies(
        self, *, actor: str, scope: RepositoryScope
    ) -> BudgetPolicyListResult: ...

    async def list_signals(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_cursor: str | None,
        limit: int,
        policy_key: str | None = None,
        revision: int | None = None,
        outcome: ReportBudgetOutcome | None = None,
    ) -> BudgetSignalListResult: ...


class CiEconomicsBudgetService:
    def __init__(self, *, authorizer: CiEconomicsAuthorizer, store: BudgetPolicyStore) -> None:
        self._authorizer = authorizer
        self._store = store

    async def configure(self, command: ConfigureBudgetPolicy) -> BudgetConfigurationResult:
        if type(command) is not ConfigureBudgetPolicy:
            raise TypeError("budget configuration requires an exact command")
        if not await self._authorizer.allows_scope(actor=command.actor, scope=command.scope):
            return CiEconomicsReadForbidden()
        try:
            result = await self._store.configure_policy(command)
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if type(result) is BudgetPolicyConflict:
            return result
        if type(result) is BudgetPolicyCommitted and result.policy == command.next_policy:
            return result
        return CiEconomicsReadUnavailable()

    async def list_policies(self, *, actor: str, scope: RepositoryScope) -> BudgetPolicyListResult:
        if type(scope) is not RepositoryScope:
            raise TypeError("budget catalog requires an exact repository scope")
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsReadForbidden()
        try:
            policies = await self._store.list_policies(scope)
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if (
            type(policies) is not tuple
            or len(policies) > MAX_BUDGET_POLICIES_PER_REPOSITORY
            or any(
                type(policy) is not BudgetPolicySnapshot or policy.scope != scope
                for policy in policies
            )
        ):
            return CiEconomicsReadUnavailable()
        keys = tuple(policy.policy_key for policy in policies)
        return policies if keys == tuple(sorted(set(keys))) else CiEconomicsReadUnavailable()

    async def list_signals(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_cursor: str | None,
        limit: int,
        policy_key: str | None = None,
        revision: int | None = None,
        outcome: ReportBudgetOutcome | None = None,
    ) -> BudgetSignalListResult:
        require_catalog_page(scope, limit)
        if after_cursor is not None:
            require_catalog_digest(after_cursor)
        if policy_key is not None:
            require_report_sample_key(policy_key)
        if revision is not None and (
            policy_key is None
            or type(revision) is not int
            or not 1 <= revision <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("signal revision requires a policy key and positive safe integer")
        if outcome is not None and outcome not in {
            "within_budget",
            "breached",
            "insufficient_evidence",
        }:
            raise ValueError("unknown budget signal outcome")
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsReadForbidden()
        try:
            page = await self._store.list_signals(
                scope,
                after_cursor=after_cursor,
                limit=limit,
                policy_key=policy_key,
                revision=revision,
                outcome=outcome,
            )
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if (
            type(page) is not BudgetSignalPage
            or len(page.items) > limit
            or any(
                item.policy.scope != scope
                or (after_cursor is not None and item.signal_id <= after_cursor)
                or (policy_key is not None and item.policy.policy_key != policy_key)
                or (revision is not None and item.policy.revision != revision)
                or (outcome is not None and item.outcome != outcome)
                for item in page.items
            )
        ):
            return CiEconomicsReadUnavailable()
        return page
