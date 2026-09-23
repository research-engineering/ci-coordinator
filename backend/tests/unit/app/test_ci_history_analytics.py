import asyncio
from unittest.mock import AsyncMock

import pytest
from ci_economics.archive_analytics_factories import analytics_query, analytics_snapshot

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.app.ci_history_analytics import CiHistoryAnalyticsService, HistoryAnalyticsStore
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsReport,
    AnalyticsUnavailable,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable


@pytest.mark.parametrize("allowed", [False, None, 1])
def test_no_archive_read_without_exact_true_repository_authorization(allowed: object) -> None:
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = allowed
    store = AsyncMock(spec=HistoryAnalyticsStore)
    result = asyncio.run(
        CiHistoryAnalyticsService(authorizer=authorizer, store=store).read(
            actor="operator", query=analytics_query()
        )
    )
    assert isinstance(
        result, CiEconomicsReadForbidden if allowed is False else CiEconomicsReadUnavailable
    )
    store.read_analytics.assert_not_awaited()


def test_authorized_query_and_mapping_bind_every_returned_snapshot() -> None:
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = True
    store = AsyncMock(spec=HistoryAnalyticsStore)
    store.read_analytics.return_value = analytics_snapshot()
    service = CiHistoryAnalyticsService(authorizer=authorizer, store=store)
    result = asyncio.run(service.read(actor="operator", query=analytics_query()))
    assert isinstance(result, AnalyticsReport)
    store.read_analytics.assert_awaited_once_with(analytics_query(), None)
    authorizer.allows_scope.assert_awaited_once_with(
        actor="operator", scope=analytics_query().scope
    )
    store.read_analytics.return_value = analytics_snapshot(query=analytics_query(repository_id=999))
    assert isinstance(
        asyncio.run(service.read(actor="operator", query=analytics_query())),
        CiEconomicsReadUnavailable,
    )


def test_scope_authority_cannot_be_replaced_by_purpose_or_store_failures() -> None:
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = True
    store = AsyncMock(spec=HistoryAnalyticsStore)
    service = CiHistoryAnalyticsService(authorizer=authorizer, store=store)
    store.read_analytics.return_value = AnalyticsUnavailable(reason="purpose_mapping_unavailable")
    result = asyncio.run(service.read(actor="operator", query=analytics_query(purpose="lint")))
    assert result == AnalyticsUnavailable(reason="purpose_mapping_unavailable")
    store.read_analytics.assert_awaited_once_with(analytics_query(purpose="lint"), None)
    store.read_analytics.side_effect = CiEconomicsStoreUnavailable("sensitive provider details")
    result = asyncio.run(service.read(actor="operator", query=analytics_query()))
    assert isinstance(result, CiEconomicsReadUnavailable)


def test_structured_store_limit_is_preserved_not_promoted_to_an_empty_success() -> None:
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = True
    store = AsyncMock(spec=HistoryAnalyticsStore)
    store.read_analytics.return_value = AnalyticsUnavailable(reason="query_budget_exceeded")
    result = asyncio.run(
        CiHistoryAnalyticsService(authorizer=authorizer, store=store).read(
            actor="operator", query=analytics_query()
        )
    )
    assert result == AnalyticsUnavailable(reason="query_budget_exceeded")
