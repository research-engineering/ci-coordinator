from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from ci_economics.archive_analytics_factories import analytics_query, analytics_snapshot
from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ci_coordinator.api.http.dependencies import (
    CiHistoryAnalyticsRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.routers.ci_history_analytics import (
    build_ci_history_analytics_router,
)
from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden
from ci_coordinator.app.ci_history_analytics import CiHistoryAnalyticsUseCase
from ci_coordinator.ci_economics.archive_analytics import summarize_archive
from ci_coordinator.ci_economics.archive_analytics_models import AnalyticsUnavailable

PATH = "/api/v2/economics/repositories/101/202/history/analytics"
QUERY = "?generation=1&createdFrom=2026-01-01T00:00:00Z&createdUntil=2026-01-31T00:00:00Z"


def _app(use_case: AsyncMock, *, authenticated: bool = True, audit: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_ci_history_analytics_router(
            CiHistoryAnalyticsRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(
                    human_principal(
                        roles=frozenset({"audit"}) if audit else frozenset({"configure"})
                    )
                    if authenticated
                    else InvalidCredential()
                ),
                role_admission=StaticRoleAdmission(),
                use_case=use_case,
            )
        )
    )
    return app


def test_typed_wire_contract_and_exact_query_reach_the_authorized_use_case() -> None:
    use_case = AsyncMock(spec=CiHistoryAnalyticsUseCase)
    query = analytics_query(workflow_id=None, job_name=None, horizon_days=7)
    use_case.read.return_value = summarize_archive(analytics_snapshot(query=query))
    with TestClient(_app(use_case)) as client:
        response = client.get(PATH + QUERY)
        schema = client.get("/openapi.json").json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["report"]["schemaVersion"] == "ci-history-analytics/v1"
    assert response.json()["report"]["buckets"][0]["observedRunnerMs"] == 30000
    assert response.json()["report"]["providerCoverage"] == "unknown"
    use_case.read.assert_awaited_once_with(actor=human_principal().actor_id, query=query)
    parameters = schema["paths"][
        "/api/v2/economics/repositories/{installation_id}/{repository_id}/history/analytics"
    ]["get"]["parameters"]
    assert {"generation", "createdFrom", "createdUntil", "jobName", "purpose"} <= {
        item["name"] for item in parameters
    }


@pytest.mark.parametrize(
    "suffix",
    [
        "&generation=1",
        "&wat=1",
        "&horizonDays=31",
        "&workflowId=-1",
        "&purpose=guessed",
        "&jobName=" + "a" * 513,
        "&horizonDays=true",
        "&actor=someone-else",
    ],
)
def test_malformed_duplicate_or_foreign_parameters_are_safe_and_do_not_reach_store(
    suffix: str,
) -> None:
    use_case = AsyncMock(spec=CiHistoryAnalyticsUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.get(PATH + QUERY + suffix)
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "invalid_request"}
    assert response.headers["cache-control"] == "no-store"
    use_case.read.assert_not_awaited()


@pytest.mark.parametrize("authenticated,audit,status", [(False, True, 401), (True, False, 403)])
def test_authentication_and_audit_role_precede_analytics_read(
    authenticated: bool,
    audit: bool,
    status: int,
) -> None:
    use_case = AsyncMock(spec=CiHistoryAnalyticsUseCase)
    with TestClient(_app(use_case, authenticated=authenticated, audit=audit)) as client:
        response = client.get(PATH + QUERY)
    assert response.status_code == status and response.headers["cache-control"] == "no-store"
    if status == 401:
        assert "www-authenticate" in response.headers
    use_case.read.assert_not_awaited()


def test_repository_denial_and_insufficient_query_budget_are_distinct() -> None:
    use_case = AsyncMock(spec=CiHistoryAnalyticsUseCase)
    use_case.read.return_value = CiEconomicsReadForbidden()
    with TestClient(_app(use_case)) as client:
        denied = client.get(PATH + QUERY)
        use_case.read.return_value = AnalyticsUnavailable(reason="query_budget_exceeded")
        insufficient = client.get(PATH + QUERY)
    assert denied.status_code == 403
    assert insufficient.status_code == 200
    assert insufficient.json() == {
        "outcome": "unavailable",
        "report": None,
        "unavailable": {"reason": "query_budget_exceeded"},
    }


def test_foreign_report_and_response_budget_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import ci_coordinator.api.http.routers.ci_history_analytics as route

    use_case = AsyncMock(spec=CiHistoryAnalyticsUseCase)
    query = analytics_query(workflow_id=None, job_name=None, horizon_days=7)
    use_case.read.return_value = summarize_archive(
        analytics_snapshot(query=analytics_query(repository_id=999))
    )
    with TestClient(_app(use_case)) as client:
        foreign = client.get(PATH + QUERY)
        use_case.read.return_value = summarize_archive(analytics_snapshot(query=query))
        monkeypatch.setattr(route, "MAX_ANALYTICS_RESPONSE_BYTES", 32)
        oversized = client.get(PATH + QUERY)
    for response in (foreign, oversized):
        assert response.status_code == 503
        assert response.json() == {"ok": False, "error": "unavailable"}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("length,expected", [(512, 200), (513, 400), (1024, 400)])
def test_encoded_unicode_job_names_obey_the_same_scalar_and_transport_bounds(
    length: int, expected: int
) -> None:
    use_case = AsyncMock(spec=CiHistoryAnalyticsUseCase)
    use_case.read.return_value = AnalyticsUnavailable(reason="dataset_unavailable")
    name = "\U0001f680" * length
    with TestClient(_app(use_case)) as client:
        response = client.get(PATH + QUERY + "&" + urlencode({"jobName": name}))
    assert response.status_code == expected
    if expected == 200:
        assert use_case.read.await_args is not None
        assert use_case.read.await_args.kwargs["query"].job_name == name
    else:
        use_case.read.assert_not_awaited()
