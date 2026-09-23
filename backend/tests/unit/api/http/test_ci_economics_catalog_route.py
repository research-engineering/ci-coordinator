from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from ci_economics.factories import ATTEMPT, NOW, recorded_source
from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    CiEconomicsRouteDependencies,
    HttpRouteDependencies,
    MeasurementReportReadRouteDependencies,
)
from ci_coordinator.app.ci_economics import CiEconomicsAuthorizer, CiEconomicsReadService
from ci_coordinator.app.ci_measurement_reports import MeasurementReportReadService
from ci_coordinator.ci_economics.catalog import (
    MeasurementReportPage,
    MeasurementReportPointer,
    ProviderSourcePage,
)
from ci_coordinator.ci_economics.ports import CiEconomicsQuery, MeasurementReportQuery

_ROOT = "/api/v2/economics/repositories/101/202/sources"


def _client() -> tuple[TestClient, AsyncMock, AsyncMock, AsyncMock]:
    source = recorded_source()
    sources = AsyncMock(spec=CiEconomicsQuery)
    sources.list_provider_sources.return_value = ProviderSourcePage(ATTEMPT.scope, (source,), None)
    reports = AsyncMock(spec=MeasurementReportQuery)
    reports.list_measurement_reports.return_value = MeasurementReportPage(
        source.source,
        (MeasurementReportPointer("a" * 64, "b" * 64, NOW, NOW + timedelta(days=1)),),
        None,
    )
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = True
    authenticator = StaticControlPlaneAuthenticator(human_principal())
    role_admission = StaticRoleAdmission()
    client = TestClient(
        create_app(
            dependencies=HttpRouteDependencies(
                ci_economics=CiEconomicsRouteDependencies(
                    authenticator=authenticator,
                    role_admission=role_admission,
                    use_case=CiEconomicsReadService(authorizer=authorizer, query=sources),
                ),
                ci_measurement_reports=MeasurementReportReadRouteDependencies(
                    authenticator=authenticator,
                    role_admission=role_admission,
                    use_case=MeasurementReportReadService(authorizer=authorizer, query=reports),
                ),
            )
        )
    )
    return client, sources, reports, authorizer


@pytest.mark.parametrize("reports", (False, True))
def test_catalog_projects_only_public_metadata(reports: bool) -> None:
    client, sources, report_query, _ = _client()
    path = _ROOT + f"/{recorded_source().source.source_id}/reports" if reports else _ROOT
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["ok"] is True and body["nextCursor"] is None
    assert len(body["items"]) == 1
    if reports:
        assert body["schemaVersion"] == "ci-measurement-report-page/v2"
        assert body["source"]["sourceId"] == recorded_source().source.source_id
        assert set(body["items"][0]) == {"reportId", "reportDigest", "receivedAt", "retainUntil"}
        report_query.list_measurement_reports.assert_awaited_once()
    else:
        assert body["schemaVersion"] == "ci-economics-source-page/v2"
        assert (body["installationId"], body["repositoryId"]) == (101, 202)
        assert set(body["items"][0]) == {
            "source",
            "status",
            "attemptCount",
            "maxAttempts",
            "nextAttemptAt",
            "lastFailureReason",
            "terminalReason",
            "completedAt",
            "retainUntil",
        }
        assert body["items"][0]["status"] == "pending"
        sources.list_provider_sources.assert_awaited_once()
    assert "leaseToken" not in response.text and "payloadCanonical" not in response.text


@pytest.mark.parametrize("reports", (False, True))
@pytest.mark.parametrize(
    "query",
    ("limit=0", "limit=101", "limit=01", "limit=1&limit=2", "extra=x", "afterCursor=invalid"),
)
def test_catalog_rejects_noncanonical_queries_without_reads(reports: bool, query: str) -> None:
    client, sources, report_query, _ = _client()
    path = _ROOT + f"/{recorded_source().source.source_id}/reports" if reports else _ROOT
    assert client.get(path + "?" + query).status_code == 422
    sources.list_provider_sources.assert_not_awaited()
    report_query.list_measurement_reports.assert_not_awaited()


@pytest.mark.parametrize("reports", (False, True))
def test_catalog_forbidden_scope_never_reaches_storage(reports: bool) -> None:
    client, sources, report_query, authorizer = _client()
    authorizer.allows_scope.return_value = False
    path = _ROOT + f"/{recorded_source().source.source_id}/reports" if reports else _ROOT
    assert client.get(path).status_code == 403
    sources.list_provider_sources.assert_not_awaited()
    report_query.list_measurement_reports.assert_not_awaited()
