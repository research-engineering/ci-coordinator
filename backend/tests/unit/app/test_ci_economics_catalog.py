from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from ci_economics.factories import ATTEMPT, NOW, recorded_source

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadNotFound,
    CiEconomicsReadService,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.app.ci_measurement_reports import MeasurementReportReadService
from ci_coordinator.ci_economics.catalog import (
    MeasurementReportPage,
    MeasurementReportPointer,
    ProviderSourcePage,
)
from ci_coordinator.ci_economics.ports import (
    CiEconomicsQuery,
    CiEconomicsStoreUnavailable,
    MeasurementReportQuery,
)
from ci_coordinator.config_control import RepositoryScope


@pytest.mark.parametrize("reports", (False, True))
@pytest.mark.parametrize("allowed", (False, True))
async def test_catalog_authorization_precedes_query(reports: bool, allowed: bool) -> None:
    source = recorded_source()
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = allowed
    query = AsyncMock(spec=MeasurementReportQuery if reports else CiEconomicsQuery)
    page = (
        MeasurementReportPage(source.source, (), None)
        if reports
        else ProviderSourcePage(ATTEMPT.scope, (source,), None)
    )
    method = query.list_measurement_reports if reports else query.list_provider_sources

    async def read(*args: object, **kwargs: object) -> object:
        authorizer.allows_scope.assert_awaited_once_with(actor="actor", scope=ATTEMPT.scope)
        return page

    method.side_effect = read
    result = (
        await MeasurementReportReadService(authorizer=authorizer, query=query).list_reports(
            actor="actor",
            scope=ATTEMPT.scope,
            source_id=source.source.source_id,
            after_cursor=None,
            limit=20,
        )
        if reports
        else await CiEconomicsReadService(authorizer=authorizer, query=query).list_provider_sources(
            actor="actor",
            scope=ATTEMPT.scope,
            after_cursor=None,
            limit=20,
        )
    )
    if allowed:
        assert result == page
        method.assert_awaited_once()
        assert method.await_args.kwargs == {"after_cursor": None, "limit": 20}
    else:
        assert isinstance(result, CiEconomicsReadForbidden)
        method.assert_not_awaited()


@pytest.mark.parametrize("operand", ("scope", "limit", "cursor", "unavailable", "type"))
async def test_source_catalog_rejects_mismatched_results(operand: str) -> None:
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = True
    query = AsyncMock(spec=CiEconomicsQuery)
    row = recorded_source()
    cursor = None
    page = ProviderSourcePage(ATTEMPT.scope, (row,), None)
    if operand == "scope":
        other = recorded_source(replace(ATTEMPT, scope=RepositoryScope(101, 203)))
        page = ProviderSourcePage(other.source.attempt.scope, (other,), None)
    elif operand == "limit":
        page = ProviderSourcePage(
            ATTEMPT.scope, (recorded_source(replace(ATTEMPT, run_attempt=3)), row), None
        )
    elif operand == "cursor":
        cursor = row.cursor
    query.list_provider_sources.return_value = None if operand == "type" else page
    if operand == "unavailable":
        query.list_provider_sources.side_effect = CiEconomicsStoreUnavailable("offline")
    result = await CiEconomicsReadService(authorizer=authorizer, query=query).list_provider_sources(
        actor="actor",
        scope=ATTEMPT.scope,
        after_cursor=cursor,
        limit=1,
    )
    assert isinstance(result, CiEconomicsReadUnavailable)


@pytest.mark.parametrize(
    "operand", ("scope", "source", "limit", "cursor", "unavailable", "missing", "type")
)
async def test_report_catalog_rejects_mismatched_results(operand: str) -> None:
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = True
    query = AsyncMock(spec=MeasurementReportQuery)
    source = recorded_source().source
    pointer = MeasurementReportPointer("a" * 64, "b" * 64, NOW, NOW + timedelta(days=1))
    page = MeasurementReportPage(source, (pointer,), None)
    cursor = None
    if operand == "scope":
        page = replace(
            page, source=recorded_source(replace(ATTEMPT, scope=RepositoryScope(102, 202))).source
        )
    elif operand == "source":
        page = replace(page, source=recorded_source(replace(ATTEMPT, run_attempt=3)).source)
    elif operand == "limit":
        page = replace(page, items=(pointer, replace(pointer, report_id="c" * 64)))
    elif operand == "cursor":
        cursor = pointer.report_id
    query.list_measurement_reports.return_value = (
        None if operand == "missing" else object() if operand == "type" else page
    )
    if operand == "unavailable":
        query.list_measurement_reports.side_effect = CiEconomicsStoreUnavailable("offline")
    result = await MeasurementReportReadService(authorizer=authorizer, query=query).list_reports(
        actor="actor",
        scope=ATTEMPT.scope,
        source_id=source.source_id,
        after_cursor=cursor,
        limit=1,
    )
    assert isinstance(
        result, CiEconomicsReadNotFound if operand == "missing" else CiEconomicsReadUnavailable
    )
