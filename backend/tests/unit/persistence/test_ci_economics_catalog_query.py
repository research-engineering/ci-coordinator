from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from ci_economics.factories import ATTEMPT, recorded_source
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.ci_measurement_report_repository import (
    _PostgresCiMeasurementReportRepository,
)
from ci_coordinator.persistence.schema import (
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
)


async def test_report_catalog_selects_only_source_and_report_pointer_columns() -> None:
    connection = AsyncMock(spec=AsyncConnection)
    result = Mock()
    result.mappings.return_value.all.return_value = []
    connection.execute.return_value = result
    active, rollback = Mock(), Mock()
    repository = _PostgresCiMeasurementReportRepository(
        connection, active, rollback, budget_policies=Mock(), budget_signals=Mock()
    )
    page = await repository.list_measurement_reports(
        ATTEMPT.scope, recorded_source().source.source_id, after_cursor=None, limit=20
    )
    assert page is None
    active.assert_called_once_with()
    rollback.assert_not_called()
    connection.execute.assert_awaited_once()
    call = connection.execute.await_args
    assert call is not None
    statement = call.args[0]
    assert isinstance(statement, Select)
    assert tuple(statement.selected_columns) == (
        *ci_workflow_attempt_collections.columns,
        ci_job_measurement_reports.c.report_id,
        ci_job_measurement_reports.c.report_digest,
        ci_job_measurement_reports.c.received_at,
        ci_job_measurement_reports.c.retain_until,
    )
