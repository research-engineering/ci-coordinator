import asyncio
from dataclasses import replace
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from ci_economics.observation_factories import NOW, SCOPE, claimed_scan, observation
from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CiObservationRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.routers.ci_observation import OBSERVATION_CONFIGURATION_PATH
from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden, CiEconomicsReadUnavailable
from ci_coordinator.app.ci_observation import CiObservationUseCase, ObservationCursorRejected
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
    ObservationCommitted,
    ObservationConflict,
)
from ci_coordinator.ci_economics.observation_ports import ObservationGapPage, ObservationStatus
from ci_coordinator.ci_economics.observation_progress import ObservationScanProgress
from ci_coordinator.ci_economics.observation_workflows import (
    ObservationWorkflowChoice,
    ObservationWorkflowPage,
)
from ci_coordinator.control_plane_identity import CONTROL_PLANE_ROLES, ControlPlaneRole

_STATUS = "/api/v2/economics/repositories/101/202/observation"
_GAPS = _STATUS + "/gaps"
_WORKFLOWS = _STATUS + "/workflows"
_SNAPSHOT = observation()
_BODY = {
    "installationId": 101,
    "repositoryId": 202,
    "expectedRevision": 2,
    "configuration": _SNAPSHOT.configuration.canonical_mapping(),
    "operationId": "operation-1",
}
_ROUTES = [
    ("POST", OBSERVATION_CONFIGURATION_PATH),
    ("GET", _STATUS),
    ("GET", _GAPS),
    ("GET", _WORKFLOWS),
]


def _app(
    use_case: AsyncMock,
    *,
    authority: str = "valid",
    integrity: bool = True,
    roles: frozenset[ControlPlaneRole] = CONTROL_PLANE_ROLES,
) -> FastAPI:
    return create_app(
        HttpRouteDependencies(
            ci_observation=CiObservationRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(
                    InvalidCredential()
                    if authority == "invalid"
                    else AuthenticationDependencyUnavailable()
                    if authority == "unavailable"
                    else human_principal(roles=roles)
                ),
                role_admission=StaticRoleAdmission(),
                mutation_admission=StaticMutationAdmission(integrity),
                use_case=use_case,
            )
        ),
        include_operator_ui=False,
        request_timeout_seconds=30,
    )


@pytest.mark.parametrize("replayed", [False, True])
def test_configure_projects_only_committed_snapshot_and_authenticated_actor(replayed: bool) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    use_case.configure.return_value = ObservationCommitted(_SNAPSHOT, replayed)
    with TestClient(_app(use_case)) as client:
        response = client.post(OBSERVATION_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schemaVersion": "ci-economics-observation-mutation/v1",
        "operationId": "operation-1",
        "outcome": "replayed" if replayed else "committed",
        "snapshot": _SNAPSHOT.canonical_mapping(),
    }
    use_case.configure.assert_awaited_once_with(
        ConfigureObservation(
            SCOPE, 2, _SNAPSHOT.configuration, "operation-1", human_principal().actor_id
        )
    )


@pytest.mark.parametrize("reason", ["revision_conflict", "operation_conflict", "capacity_reached"])
def test_configuration_conflict_has_no_snapshot(
    reason: Literal["revision_conflict", "operation_conflict", "capacity_reached"],
) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    use_case.configure.return_value = ObservationConflict(reason)
    with TestClient(_app(use_case)) as client:
        response = client.post(OBSERVATION_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 409
    assert response.json()["outcome"] == reason and response.json()["snapshot"] is None


@pytest.mark.parametrize("method,path", _ROUTES)
@pytest.mark.parametrize("authority,code", [("invalid", 401), ("unavailable", 503)])
def test_authentication_failure_prevents_every_operation(
    method: str, path: str, authority: str, code: int
) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    with TestClient(_app(use_case, authority=authority)) as client:
        response = client.request(method, path, json=_BODY if method == "POST" else None)
    assert response.status_code == code and not use_case.mock_calls
    assert response.headers["cache-control"] == "no-store"
    assert bool(response.headers.get("www-authenticate")) == (code == 401)


@pytest.mark.parametrize("method,path", _ROUTES)
def test_missing_required_role_prevents_every_operation(method: str, path: str) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    with TestClient(_app(use_case, roles=frozenset())) as client:
        response = client.request(method, path, json=_BODY if method == "POST" else None)
    assert response.status_code == 403 and not use_case.mock_calls


def test_browser_mutation_integrity_precedes_configuration() -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    with TestClient(_app(use_case, integrity=False)) as client:
        response = client.post(OBSERVATION_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 403 and not use_case.mock_calls


@pytest.mark.parametrize("method,path", _ROUTES)
@pytest.mark.parametrize(
    "failure,code", [(CiEconomicsReadForbidden(), 403), (CiEconomicsReadUnavailable(), 503)]
)
def test_application_failure_preserves_bounded_private_response(
    method: str, path: str, failure: object, code: int
) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    for name in ("configure", "status", "gaps", "workflows"):
        getattr(use_case, name).return_value = failure
    with TestClient(_app(use_case)) as client:
        response = client.request(method, path, json=_BODY if method == "POST" else None)
    assert response.status_code == code
    assert response.json() == {"ok": False, "error": "forbidden" if code == 403 else "unavailable"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "content,headers,code",
    [
        (b"{}", [("content-type", "text/plain")], 400),
        (b"{}", [("content-type", "application/json"), ("content-type", "application/json")], 400),
        (b"{}", [("content-type", "application/json"), ("content-encoding", "identity")], 400),
        (b'{"operationId":"a","operationId":"b"}', [("content-type", "application/json")], 400),
        (
            b'{"configuration":{"selector":{"kind":"all","kind":"selected"}}}',
            [("content-type", "application/json")],
            400,
        ),
        (b"[" * 10 + b"0" + b"]" * 10, [("content-type", "application/json")], 400),
        (b" " * 2049, [("content-type", "application/json")], 413),
    ],
)
def test_raw_body_admission_precedes_pydantic_and_application(
    content: bytes, headers: list[tuple[str, str]], code: int
) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(OBSERVATION_CONFIGURATION_PATH, content=content, headers=headers)
    assert response.status_code == code and not use_case.mock_calls


@pytest.mark.parametrize("field", tuple(_BODY))
def test_all_configuration_fields_are_required(field: str) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(
            OBSERVATION_CONFIGURATION_PATH,
            json={key: value for key, value in _BODY.items() if key != field},
        )
    assert response.status_code == 422 and not use_case.mock_calls


@pytest.mark.parametrize(
    "path,code",
    [
        (_STATUS + "?unknown=x", 400),
        (_GAPS + "?limit=1&limit=2", 400),
        (
            _GAPS + "?afterCursor=101.202.3." + "a" * 64 + "&afterCursor=101.202.3." + "b" * 64,
            400,
        ),
        (_GAPS + "?limit=51", 422),
        (_GAPS + "?afterCursor=invalid", 422),
        (_GAPS + "?unknown=x", 400),
        (_STATUS.replace("/101/", "/0/"), 422),
        (_WORKFLOWS + "?pageNumber=0", 422),
        (_WORKFLOWS + "?pageNumber=21", 422),
        (_WORKFLOWS + "?page=1", 400),
        (_WORKFLOWS + "?pageNumber=1&pageNumber=2", 400),
        (_WORKFLOWS + "?unknown=x", 400),
    ],
)
def test_queries_are_closed_and_bounded(path: str, code: int) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.get(path)
    assert response.status_code == code and not use_case.mock_calls


@pytest.mark.parametrize("page", [1, 2])
def test_workflow_catalogue_projects_exact_provider_id_and_page_without_mutation(page: int) -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    use_case.workflows.return_value = ObservationWorkflowPage(
        SCOPE,
        page,
        (page - 1) * 100 + 1,
        (ObservationWorkflowChoice(300, "Full Check", "dynamic/build", "active"),),
        "exhausted",
    )
    with TestClient(_app(use_case, integrity=False)) as client:
        response = client.get(_WORKFLOWS + f"?pageNumber={page}")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schemaVersion": "ci-economics-observation-workflows/v1",
        "installationId": 101,
        "repositoryId": 202,
        "pageNumber": page,
        "providerTotal": (page - 1) * 100 + 1,
        "termination": "exhausted",
        "items": [
            {"workflowId": 300, "name": "Full Check", "path": "dynamic/build", "state": "active"}
        ],
    }
    use_case.workflows.assert_awaited_once_with(
        actor=human_principal().actor_id, scope=SCOPE, page_number=page
    )
    use_case.configure.assert_not_awaited()


def test_stale_gap_cursor_maps_to_restartable_invalid_request() -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    use_case.gaps.return_value = ObservationCursorRejected()
    with TestClient(_app(use_case)) as client:
        response = client.get(_GAPS + "?afterCursor=101.202.3." + "a" * 64)
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "invalid_request"}
    assert response.headers["cache-control"] == "no-store"


def test_configuration_accepts_no_query_override() -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(OBSERVATION_CONFIGURATION_PATH + "?enabled=true", json=_BODY)
    assert response.status_code == 400 and not use_case.mock_calls


@pytest.mark.parametrize("configured", [False, True])
def test_status_reports_real_progress_without_lease_credentials(configured: bool) -> None:
    snapshot, state, claim = claimed_scan()
    progress = (
        ObservationScanProgress(state),
        ObservationScanProgress(replace(state, lane="recent", lease=None)),
    )
    use_case = AsyncMock(spec=CiObservationUseCase)
    use_case.status.return_value = ObservationStatus(
        SCOPE, snapshot if configured else None, progress if configured else (), 17, None, NOW
    )
    with TestClient(_app(use_case)) as client:
        response = client.get(_STATUS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["occupiedSourceSlots"] == 17 and body["maximumSourceSlots"] == 10000
    assert len(body["scans"]) == (2 if configured else 0)
    assert body["observedAt"] == "2026-09-09T12:00:00Z"
    assert claim.lease.worker_id not in response.text and claim.lease.token not in response.text
    assert "expectedRevision" not in response.text
    if configured:
        assert body["scans"][0]["leaseExpiresAt"] == "2026-09-09T12:01:00Z"
        assert body["scans"][0]["pagesSeen"] == 0
        assert body["scans"][0]["lastOutcome"] is None


def test_gap_cursor_and_limit_are_forwarded_exactly() -> None:
    use_case = AsyncMock(spec=CiObservationUseCase)
    use_case.gaps.return_value = ObservationGapPage(SCOPE, (), None, NOW)
    cursor = "101.202.3." + "a" * 64
    with TestClient(_app(use_case)) as client:
        response = client.get(_GAPS + "?afterCursor=" + cursor + "&limit=50")
    assert response.status_code == 200 and response.json()["items"] == []
    use_case.gaps.assert_awaited_once_with(
        actor=human_principal().actor_id, scope=SCOPE, after_cursor=cursor, limit=50
    )


async def test_configuration_bulkhead_rejects_excess_without_queueing() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    active = 0

    async def held(_: ConfigureObservation) -> ObservationCommitted:
        nonlocal active
        active += 1
        if active == 2:
            entered.set()
        await release.wait()
        return ObservationCommitted(_SNAPSHOT, False)

    use_case = AsyncMock(spec=CiObservationUseCase)
    use_case.configure.side_effect = held
    async with AsyncClient(
        transport=ASGITransport(app=_app(use_case)), base_url="http://test"
    ) as client:
        async with asyncio.timeout(3):
            async with asyncio.TaskGroup() as group:
                requests = [
                    group.create_task(client.post(OBSERVATION_CONFIGURATION_PATH, json=_BODY))
                    for _ in range(2)
                ]
                try:
                    await entered.wait()
                    excess = await client.post(OBSERVATION_CONFIGURATION_PATH, json=_BODY)
                    assert excess.status_code == 503
                    assert excess.json() == {"ok": False, "error": "overloaded"}
                    assert active == 2
                finally:
                    release.set()
            assert all(request.result().status_code == 200 for request in requests)
