import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from ci_economics.budget_factories import budget_command
from ci_economics.report_factories import stored_report

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.app.ci_economics_budgets import CiEconomicsBudgetService
from ci_coordinator.ci_economics.budget_commands import BudgetPolicyCommitted, BudgetPolicyConflict
from ci_coordinator.ci_economics.budget_ports import BudgetPolicyStore
from ci_coordinator.ci_economics.budget_signal import BudgetSignal, BudgetSignalPage
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope


async def _invoke(service: CiEconomicsBudgetService, operation: str) -> object:
    command = budget_command()
    if operation == "configure_policy":
        return await service.configure(command)
    if operation == "list_policies":
        return await service.list_policies(actor=command.actor, scope=command.scope)
    return await service.list_signals(
        actor=command.actor, scope=command.scope, after_cursor=None, limit=20
    )


@pytest.mark.parametrize("operation", ["configure_policy", "list_policies", "list_signals"])
@pytest.mark.parametrize("allowed", [False, True])
def test_budget_access_authorizes_before_storage(operation: str, allowed: bool) -> None:
    async def scenario() -> None:
        authorizer, store = AsyncMock(spec=CiEconomicsAuthorizer), AsyncMock(spec=BudgetPolicyStore)
        authorizer.allows_scope.return_value = allowed
        command = budget_command()
        expected = {
            "configure_policy": BudgetPolicyCommitted(command.next_policy, False),
            "list_policies": (command.next_policy,),
            "list_signals": BudgetSignalPage((), None),
        }[operation]
        getattr(store, operation).return_value = expected

        async def authorized_read(*_args: object, **_kwargs: object) -> object:
            authorizer.allows_scope.assert_awaited_once_with(
                actor=command.actor, scope=command.scope
            )
            return expected

        getattr(store, operation).side_effect = authorized_read
        result = await _invoke(
            CiEconomicsBudgetService(authorizer=authorizer, store=store), operation
        )
        if allowed:
            assert result == expected
            getattr(store, operation).assert_awaited_once()
        else:
            assert isinstance(result, CiEconomicsReadForbidden)
            assert not store.mock_calls

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["configure_policy", "list_policies", "list_signals"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_storage_failure_is_not_empty_evidence_and_cancellation_propagates(
    operation: str, cancelled: bool
) -> None:
    async def scenario() -> None:
        authorizer, store = AsyncMock(spec=CiEconomicsAuthorizer), AsyncMock(spec=BudgetPolicyStore)
        authorizer.allows_scope.return_value = True
        getattr(store, operation).side_effect = (
            asyncio.CancelledError() if cancelled else CiEconomicsStoreUnavailable("offline")
        )
        service = CiEconomicsBudgetService(authorizer=authorizer, store=store)
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await _invoke(service, operation)
        else:
            assert isinstance(await _invoke(service, operation), CiEconomicsReadUnavailable)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["configure_policy", "list_policies", "list_signals"])
@pytest.mark.parametrize("operand", ["installation", "repository", "type"])
def test_adapter_output_is_rebound_to_the_requested_scope(operation: str, operand: str) -> None:
    async def scenario() -> None:
        authorizer, store = AsyncMock(spec=CiEconomicsAuthorizer), AsyncMock(spec=BudgetPolicyStore)
        authorizer.allows_scope.return_value = True
        policy = budget_command().next_policy
        scope = RepositoryScope(
            policy.scope.installation_id + (operand == "installation"),
            policy.scope.repository_id + (operand == "repository"),
        )
        wrong_policy = replace(policy, scope=scope)
        signal = replace(BudgetSignal.evaluate(policy, stored_report()), policy=wrong_policy)
        result = {
            "configure_policy": BudgetPolicyCommitted(wrong_policy, False),
            "list_policies": (wrong_policy,),
            "list_signals": BudgetSignalPage((signal,), None),
        }[operation]
        getattr(store, operation).return_value = {} if operand == "type" else result
        assert isinstance(
            await _invoke(CiEconomicsBudgetService(authorizer=authorizer, store=store), operation),
            CiEconomicsReadUnavailable,
        )

    asyncio.run(scenario())


def test_policy_revision_conflict_is_a_confirmed_outcome() -> None:
    async def scenario() -> None:
        authorizer, store = AsyncMock(spec=CiEconomicsAuthorizer), AsyncMock(spec=BudgetPolicyStore)
        authorizer.allows_scope.return_value = True
        store.configure_policy.return_value = BudgetPolicyConflict("revision_conflict")
        assert await _invoke(
            CiEconomicsBudgetService(authorizer=authorizer, store=store), "configure_policy"
        ) == BudgetPolicyConflict("revision_conflict")

    asyncio.run(scenario())


@pytest.mark.parametrize("operand", ["key", "revision", "outcome", "cursor", "limit"])
def test_signal_query_cannot_receive_an_unrequested_page(operand: str) -> None:
    async def scenario() -> None:
        authorizer, store = AsyncMock(spec=CiEconomicsAuthorizer), AsyncMock(spec=BudgetPolicyStore)
        authorizer.allows_scope.return_value = True
        command = budget_command()
        signal = BudgetSignal.evaluate(command.next_policy, stored_report())
        second = replace(signal, report_id="e" * 64)
        items = (
            tuple(sorted((signal, second), key=lambda value: value.signal_id))
            if operand == "limit"
            else (signal,)
        )
        store.list_signals.return_value = BudgetSignalPage(items, None)
        result = await CiEconomicsBudgetService(authorizer=authorizer, store=store).list_signals(
            actor=command.actor,
            scope=command.scope,
            after_cursor=signal.signal_id if operand == "cursor" else None,
            limit=1,
            policy_key="other" if operand == "key" else command.policy_key,
            revision=2 if operand == "revision" else None,
            outcome="breached" if operand == "outcome" else None,
        )
        assert isinstance(result, CiEconomicsReadUnavailable)

    asyncio.run(scenario())
