from typing import Literal
from unittest.mock import AsyncMock

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_detail, archived_statistics
from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ci_coordinator.api.http.body_limits import RequestBodyLimitMiddleware
from ci_coordinator.api.http.dependencies import CiHistoryReadRouteDependencies, InvalidCredential
from ci_coordinator.api.http.request_admission import RequestAdmissionMiddleware
from ci_coordinator.api.http.routers.ci_history_read import (
    HISTORY_RETENTION_APPLY_PATH,
    HISTORY_RETENTION_BODY_LIMITS,
    HISTORY_RETENTION_PREVIEW_PATH,
    HISTORY_RETENTION_REQUEST_LIMITS,
    MAX_RETENTION_BODY_BYTES,
    build_ci_history_read_router,
    history_attempt_detail_request_limit,
    history_read_request_limit,
)
from ci_coordinator.app.ci_history_read import CiHistoryReadService, HistoryReadResult
from ci_coordinator.ci_economics.archive_retention_payload import ForeverDetailRetentionPayload
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptHeader
from ci_coordinator.ci_economics.history_read import (
    HistoryDetailView,
    HistoryReadPage,
    HistoryReadQuery,
    HistoryReadRejected,
    HistoryRecordSummary,
)
from ci_coordinator.ci_economics.history_retention_commands import (
    ApplyHistoryRetention,
    HistoryRetentionResult,
)
from ci_coordinator.control_plane_identity import CONTROL_PLANE_ROLES, ControlPlaneRole

_READ = "/api/v2/economics/repositories/101/202/history/archive/records"
_DETAIL_READ = "/api/v2/economics/repositories/101/202/history/attempts/303/1/detail"
_SELECTION = {
    "installationId": 101,
    "repositoryId": 202,
    "generation": 1,
    "configurationRevision": 1,
    "dataRevision": 1,
    "defaultRevision": 1,
    "importedThrough": ARCHIVE_TIME.isoformat(),
    "action": "erase_details",
    "keys": [{"workflowRunId": 303, "runAttempt": 1}],
}
_APPLY = {"selection": _SELECTION, "reviewedDigest": "a" * 64, "operationId": "apply-retention"}


def _app(
    service: AsyncMock,
    *,
    valid: bool = True,
    integrity: bool = True,
    roles: frozenset[ControlPlaneRole] = CONTROL_PLANE_ROLES,
) -> FastAPI:
    dependencies = CiHistoryReadRouteDependencies(
        authenticator=StaticControlPlaneAuthenticator(
            human_principal(roles=roles) if valid else InvalidCredential()
        ),
        role_admission=StaticRoleAdmission(),
        mutation_admission=StaticMutationAdmission(integrity),
        use_case=service,
    )
    app = FastAPI()
    app.include_router(build_ci_history_read_router(dependencies))
    app.add_middleware(RequestBodyLimitMiddleware, policies=HISTORY_RETENTION_BODY_LIMITS)
    app.add_middleware(
        RequestAdmissionMiddleware,
        timeout_seconds=30,
        policies=(
            *HISTORY_RETENTION_REQUEST_LIMITS,
            history_read_request_limit(4),
            history_attempt_detail_request_limit(4),
        ),
        liveness_paths=frozenset(),
        default_timeout_response={"ok": False, "error": "unavailable"},
    )
    return app


@pytest.mark.parametrize(
    "valid,roles,code", [(False, CONTROL_PLANE_ROLES, 401), (True, frozenset(), 403)]
)
@pytest.mark.parametrize(
    "path", [_READ, _DETAIL_READ, HISTORY_RETENTION_PREVIEW_PATH, HISTORY_RETENTION_APPLY_PATH]
)
def test_all_routes_reject_missing_authentication_or_role(
    path: str, valid: bool, roles: frozenset[ControlPlaneRole], code: int
) -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    with TestClient(_app(service, valid=valid, roles=roles)) as client:
        response = (
            client.get(path + "?generation=1")
            if path in {_READ, _DETAIL_READ}
            else client.post(
                path, json=_SELECTION if path == HISTORY_RETENTION_PREVIEW_PATH else _APPLY
            )
        )
    assert response.status_code == code and not service.mock_calls
    assert response.headers["cache-control"] == "no-store"
    assert bool(response.headers.get("www-authenticate")) == (code == 401)


def test_read_uses_exact_unequal_scope_and_never_serializes_internal_cursor_key() -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    query = HistoryReadQuery(installationId=101, repositoryId=202, generation=1, kind="records")
    service.read.return_value = HistoryReadResult(
        HistoryReadPage(
            query=query, configurationRevision=2, dataRevision=3, observedAt=ARCHIVE_TIME
        ),
        None,
    )
    with TestClient(_app(service, integrity=False, roles=frozenset({"audit"}))) as client:
        response = client.get(_READ + "?generation=1")
    assert response.status_code == 200
    body = response.json()
    assert body["query"]["installationId"] == 101 and body["query"]["repositoryId"] == 202
    assert (
        body["records"] == []
        and body["nextCursor"] is None
        and "nextKey" not in body
        and "detailPayload" not in body
    )
    assert response.headers["cache-control"] == "no-store"
    service.read.assert_awaited_once_with(
        actor=human_principal().actor_id, query=query, cursor=None
    )


def test_detail_route_binds_exact_attempt_and_generation_without_a_cursor() -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    service.read.return_value = HistoryReadRejected("not_found")
    with TestClient(_app(service, roles=frozenset({"audit"}))) as client:
        response = client.get(_DETAIL_READ + "?generation=7")
    assert response.status_code == 404
    query = service.read.await_args.kwargs["query"]
    assert query.scope.installation_id == 101 and query.scope.repository_id == 202
    assert query.generation == 7
    assert query.kind == "detail"
    assert query.workflow_run_id == 303 and query.run_attempt == 1
    service.read.assert_awaited_once_with(
        actor=human_principal().actor_id, query=query, cursor=None
    )


@pytest.mark.parametrize("with_payload", [False, True])
def test_detail_route_serializes_successful_null_and_populated_payloads(with_payload: bool) -> None:
    statistics = archived_statistics()
    query = HistoryReadQuery(
        installationId=101,
        repositoryId=202,
        generation=7,
        kind="detail",
        limit=1,
        workflowRunId=303,
        runAttempt=1,
    )
    retention = HistoryDetailView(
        state="retained" if with_payload else "not_imported",
        firstImportedAt=ARCHIVE_TIME if with_payload else None,
        expiresAt=None,
        appliedPolicy=ForeverDetailRetentionPayload(mode="forever") if with_payload else None,
        policySource="repository_override" if with_payload else None,
        policyRevision=2 if with_payload else None,
        content="unavailable_format" if with_payload else "not_imported",
    )
    record = HistoryRecordSummary(
        header=ArchivedAttemptHeader.model_validate(statistics.model_dump(exclude={"jobs"})),
        jobCount=1,
        hasConflict=False,
        firstImportedAt=ARCHIVE_TIME,
        detail=retention,
    )
    page = HistoryReadPage(
        query=query,
        configurationRevision=2,
        dataRevision=3,
        observedAt=ARCHIVE_TIME,
        records=(record,),
        detail=retention,
        detail_payload=archived_detail() if with_payload else None,
    )
    service = AsyncMock(spec=CiHistoryReadService)
    service.read.return_value = HistoryReadResult(page, None)
    with TestClient(_app(service, integrity=False, roles=frozenset({"audit"}))) as client:
        response = client.get(_DETAIL_READ + "?generation=7")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["schemaVersion"] == "ci-economics-history-attempt-detail/v1"
    assert (body["configurationRevision"], body["dataRevision"]) == (2, 3)
    assert {
        key: body["query"][key]
        for key in ("installationId", "repositoryId", "generation", "workflowRunId", "runAttempt")
    } == {
        "installationId": 101,
        "repositoryId": 202,
        "generation": 7,
        "workflowRunId": 303,
        "runAttempt": 1,
    }
    assert body["query"]["kind"] == "detail"
    assert body["query"]["limit"] == 1
    assert body["coverage"] == "retained_local_rows"
    assert body["providerCompleteness"] == "not_established"
    assert body["detail"]["state"] == ("retained" if with_payload else "not_imported")
    assert body["detailPayload"] == (
        {
            "schemaVersion": "ci-economics-archive-detail/v1",
            "attempt": {
                "installationId": 101,
                "repositoryId": 202,
                "workflowRunId": 303,
                "runAttempt": 1,
                "headSha": "a" * 40,
            },
            "jobs": [
                {
                    "providerJobId": 1,
                    "steps": [
                        {
                            "number": 1,
                            "status": "completed",
                            "conclusion": "success",
                            "startedAt": "2020-01-01T00:01:00Z",
                            "completedAt": "2020-01-01T00:02:00Z",
                        }
                    ],
                }
            ],
        }
        if with_payload
        else None
    )
    assert "nextCursor" not in body and "nextKey" not in body
    service.read.assert_awaited_once_with(
        actor=human_principal().actor_id, query=query, cursor=None
    )


def test_detail_route_rejects_extra_query_fields_before_the_use_case() -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    with TestClient(_app(service, roles=frozenset({"audit"}))) as client:
        response = client.get(_DETAIL_READ + "?generation=7&cursor=unexpected")
    assert response.status_code == 400
    assert not service.mock_calls


@pytest.mark.parametrize(
    "query,code",
    [
        ("generation=1&generation=2", 400),
        ("generation=1&hidden=true", 400),
        ("generation=1&limit=51", 422),
        ("generation=true", 422),
        ("generation=0", 422),
        ("generation=1&cursor=" + "x" * 4097, 422),
        ("generation=1&cursor=" + "x" * 16384, 400),
    ],
)
def test_query_boundary_rejects_duplicates_forgery_and_quota_overflow(
    query: str, code: int
) -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    with TestClient(_app(service)) as client:
        response = client.get(_READ + "?" + query)
    assert response.status_code == code and not service.mock_calls
    assert response.headers["cache-control"] == "no-store"


def test_mutation_requires_integrity_and_server_actor() -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    service.apply.return_value = HistoryRetentionResult(
        outcome="revision_conflict", operationId="apply-retention", preview=None, dataRevision=None
    )
    with TestClient(_app(service, integrity=False)) as client:
        assert client.post(HISTORY_RETENTION_APPLY_PATH, json=_APPLY).status_code == 403
    assert not service.mock_calls
    with TestClient(_app(service)) as client:
        assert (
            client.post(HISTORY_RETENTION_APPLY_PATH, json={**_APPLY, "actor": "spoof"}).status_code
            == 422
        )
        response = client.post(HISTORY_RETENTION_APPLY_PATH, json=_APPLY)
    assert response.status_code == 409 and response.json()["preview"] is None
    command = service.apply.await_args.args[0]
    assert isinstance(command, ApplyHistoryRetention)
    assert command.actor == human_principal().actor_id and command.selection.repository_id == 202


@pytest.mark.parametrize(
    "content,code",
    [
        (b'{"action":"apply_policy","action":"erase_details"}', 400),
        (b" " * (MAX_RETENTION_BODY_BYTES + 1), 413),
        (b"[" * 9 + b"0" + b"]" * 9, 400),
    ],
)
def test_retention_body_limits_precede_application(content: bytes, code: int) -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    with TestClient(_app(service)) as client:
        response = client.post(
            HISTORY_RETENTION_APPLY_PATH,
            content=content,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == code and not service.mock_calls
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "reason,code",
    [("invalid_cursor", 400), ("stale_cursor", 409), ("not_found", 404), ("dataset_fenced", 409)],
)
def test_read_failures_are_explicit_private_and_not_cached(
    reason: Literal["invalid_cursor", "stale_cursor", "not_found", "dataset_fenced"], code: int
) -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    service.read.return_value = HistoryReadRejected(reason)
    with TestClient(_app(service)) as client:
        response = client.get(_READ + "?generation=1")
    assert response.status_code == code and response.json() == {"ok": False, "error": reason}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "field",
    [
        "installationId",
        "repositoryId",
        "generation",
        "configurationRevision",
        "dataRevision",
        "defaultRevision",
    ],
)
@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_native_retention_adapter_preserves_strict_numeric_admission(
    field: str, value: object
) -> None:
    service = AsyncMock(spec=CiHistoryReadService)
    with TestClient(_app(service)) as client:
        response = client.post(HISTORY_RETENTION_PREVIEW_PATH, json={**_SELECTION, field: value})
    assert response.status_code == 422 and not service.mock_calls
    assert response.headers["cache-control"] == "no-store"
