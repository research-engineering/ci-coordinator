import asyncio
from dataclasses import replace
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, history_dataset
from ci_history_http_support import history_app as _app
from control_plane_http_support import (
    human_principal,
)
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient

from ci_coordinator.api.http.routers.ci_history import (
    HISTORY_CONFIGURATION_PATH,
    MAX_HISTORY_BODY_BYTES,
)
from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden, CiEconomicsReadUnavailable
from ci_coordinator.app.ci_history_administration import CiHistoryAdministrationUseCase
from ci_coordinator.ci_economics.archive_retention import DetailRetentionPolicy
from ci_coordinator.ci_economics.history_administration import HistoryStatus
from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigurationConflict,
    HistoryConfigurationInvalid,
    HistoryConfigured,
)
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration, HistoryDefaults
from ci_coordinator.ci_economics.observation import MAX_OBSERVATION_WORKFLOW_IDS
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_STATUS = "/api/v2/economics/repositories/101/202/history"
_DATASET = history_dataset()
_EMPTY = HistoryStatus(
    _DATASET.scope,
    HistoryDefaults(1, DetailRetentionPolicy.default(), ARCHIVE_TIME),
    None,
    None,
    0,
    ARCHIVE_TIME,
)
_BODY = {
    "installationId": 101,
    "repositoryId": 202,
    "expectedRevision": 0,
    "configuration": _DATASET.configuration.model_dump(mode="json"),
    "initialCreatedFrom": ARCHIVE_TIME.isoformat(),
    "rescan": False,
    "operationId": "operation-1",
}
_ROUTES = [("POST", HISTORY_CONFIGURATION_PATH), ("GET", _STATUS)]


@pytest.mark.parametrize("replayed", [False, True])
def test_mutation_uses_only_authenticated_actor_and_projects_exact_receipt(replayed: bool) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    use_case.configure.return_value = HistoryConfigured(_DATASET, replayed)
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schemaVersion": "ci-economics-history-mutation/v1",
        "operationId": "operation-1",
        "outcome": "replayed" if replayed else "committed",
        "snapshot": _DATASET.canonical_mapping(),
    }
    use_case.configure.assert_awaited_once_with(
        ConfigureHistory.model_validate({**_BODY, "actor": human_principal().actor_id})
    )


@pytest.mark.parametrize(
    "reason", ["revision_conflict", "operation_conflict", "capacity_reached", "dataset_fenced"]
)
def test_conflicts_cannot_masquerade_as_successful_snapshots(
    reason: Literal[
        "revision_conflict", "operation_conflict", "capacity_reached", "dataset_fenced"
    ],
) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    use_case.configure.return_value = HistoryConfigurationConflict(reason)
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 409
    assert response.json()["outcome"] == reason and response.json()["snapshot"] is None


def test_impossible_population_is_a_typed_rejection_not_a_transport_failure() -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    use_case.configure.return_value = HistoryConfigurationInvalid()
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 400
    assert response.json()["outcome"] == "invalid_population"
    assert response.json()["snapshot"] is None


@pytest.mark.parametrize("method,path", _ROUTES)
@pytest.mark.parametrize("authority,code", [("invalid", 401), ("unavailable", 503)])
def test_authentication_failure_has_no_application_effect(
    method: str, path: str, authority: str, code: int
) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(_app(use_case, authority=authority)) as client:
        response = client.request(method, path, json=_BODY if method == "POST" else None)
    assert response.status_code == code and not use_case.mock_calls
    assert response.headers["cache-control"] == "no-store"
    assert bool(response.headers.get("www-authenticate")) == (code == 401)


@pytest.mark.parametrize("method,path", _ROUTES)
def test_role_admission_precedes_every_operation(method: str, path: str) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(_app(use_case, roles=frozenset())) as client:
        response = client.request(method, path, json=_BODY if method == "POST" else None)
    assert response.status_code == 403 and not use_case.mock_calls


def test_mutation_integrity_is_required_for_writes_not_for_read_only_audit() -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    use_case.status.return_value = _EMPTY
    with TestClient(_app(use_case, integrity=False, roles=frozenset({"audit"}))) as client:
        status = client.get(_STATUS)
        forbidden = client.post(HISTORY_CONFIGURATION_PATH, json=_BODY)
    assert status.status_code == 200 and forbidden.status_code == 403
    assert status.json()["snapshot"] is None and status.json()["scan"] is None
    assert status.json()["pendingRechecks"] == 0
    use_case.status.assert_awaited_once_with(actor=human_principal().actor_id, scope=_DATASET.scope)
    use_case.configure.assert_not_called()
    with TestClient(_app(use_case, integrity=False)) as client:
        assert client.post(HISTORY_CONFIGURATION_PATH, json=_BODY).status_code == 403
    use_case.configure.assert_not_called()


@pytest.mark.parametrize("method,path", _ROUTES)
@pytest.mark.parametrize(
    "failure,code", [(CiEconomicsReadForbidden(), 403), (CiEconomicsReadUnavailable(), 503)]
)
def test_scope_or_storage_failure_remains_private(
    method: str, path: str, failure: object, code: int
) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    use_case.configure.return_value = use_case.status.return_value = failure
    with TestClient(_app(use_case)) as client:
        response = client.request(method, path, json=_BODY if method == "POST" else None)
    assert response.status_code == code and response.headers["cache-control"] == "no-store"
    assert response.json() == {"ok": False, "error": "forbidden" if code == 403 else "unavailable"}


@pytest.mark.parametrize(
    "content,headers,code",
    [
        (b"{}", [], 400),
        (b"{}", [("content-type", "text/plain")], 400),
        (b"{}", [("content-type", "application/json"), ("content-type", "application/json")], 400),
        (b"{}", [("content-type", "application/json"), ("content-encoding", "identity")], 400),
        (b'{"operationId":"a","operationId":"b"}', [("content-type", "application/json")], 400),
        (
            b'{"configuration":{"enabled":true,"enabled":false}}',
            [("content-type", "application/json")],
            400,
        ),
        (b"[" * 7 + b"0" + b"]" * 7, [("content-type", "application/json")], 400),
        (b"[" + b"0," * 256 + b"0]", [("content-type", "application/json")], 400),
        (b" " * (MAX_HISTORY_BODY_BYTES + 1), [("content-type", "application/json")], 413),
    ],
)
def test_byte_admission_rejects_before_model_or_application(
    content: bytes, headers: list[tuple[str, str]], code: int
) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_CONFIGURATION_PATH, content=content, headers=headers)
    assert response.status_code == code and not use_case.mock_calls


@pytest.mark.parametrize("field", tuple(_BODY))
def test_missing_required_fields_cannot_be_defaulted(field: str) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(
            HISTORY_CONFIGURATION_PATH,
            json={key: value for key, value in _BODY.items() if key != field},
        )
    assert response.status_code == 422 and not use_case.mock_calls


@pytest.mark.parametrize(
    "retention",
    [
        None,
        {"mode": "disabled"},
        {"mode": "forever"},
        {"mode": "days", "days": 365, "anchor": "first_successful_detail_import"},
    ],
)
def test_maximum_selector_and_quota_fit_byte_model_and_retention_boundaries(
    retention: object,
) -> None:
    configuration = HistoryConfiguration.model_validate(
        {
            "enabled": True,
            "workflowIds": [
                MAX_SAFE_JSON_INTEGER - index for index in range(MAX_OBSERVATION_WORKFLOW_IDS)
            ],
            "detailRetention": retention,
            "quota": dict.fromkeys(
                ("attempts", "jobs", "gaps", "canonicalBytes"), MAX_SAFE_JSON_INTEGER
            ),
        }
    )
    body = {
        **_BODY,
        "configuration": configuration.model_dump(mode="json"),
        "operationId": "a" * 128,
    }
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    use_case.configure.return_value = HistoryConfigured(
        replace(_DATASET, configuration=configuration), False
    )
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_CONFIGURATION_PATH, json=body)
    assert response.status_code == 200
    assert response.json()["snapshot"]["configuration"] == configuration.model_dump(mode="json")
    use_case.configure.assert_awaited_once_with(
        ConfigureHistory.model_validate({**body, "actor": human_principal().actor_id})
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"actor": "forged"},
        {"repositoryId": MAX_SAFE_JSON_INTEGER + 1},
        {"expectedRevision": "0"},
        {"initialCreatedFrom": None},
        {"rescan": True},
        {"operationId": ""},
        {"configuration": {**_DATASET.configuration.model_dump(), "enabled": 1}},
        {"configuration": {**_DATASET.configuration.model_dump(), "workflowIds": [1, 1]}},
        {
            "configuration": {
                **_DATASET.configuration.model_dump(),
                "detailRetention": {"mode": "unknown"},
            }
        },
    ],
)
def test_model_boundary_is_strict_and_actor_is_server_owned(patch: dict[str, object]) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_CONFIGURATION_PATH, json={**_BODY, **patch})
    assert response.status_code == 422, response.text
    assert not use_case.mock_calls


@pytest.mark.parametrize("value", [True, False, "101", 101.0])
@pytest.mark.parametrize(
    "field",
    ["installationId", "repositoryId", "workflowIds", "attempts", "jobs", "gaps", "canonicalBytes"],
)
def test_every_shared_numeric_contract_is_strict_in_the_mounted_adapter(
    field: str, value: object
) -> None:
    configuration = _DATASET.configuration.model_dump(mode="json")
    body = {**_BODY, "configuration": configuration}
    if field in {"installationId", "repositoryId"}:
        body[field] = value
    elif field == "workflowIds":
        configuration[field] = [value]
    else:
        configuration["quota"] = {**configuration["quota"], field: value}
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_CONFIGURATION_PATH, json=body)
    assert response.status_code == 422, response.text
    assert not use_case.mock_calls


@pytest.mark.parametrize("method,path", _ROUTES)
def test_query_parameters_cannot_add_hidden_command_or_read_semantics(
    method: str, path: str
) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.request(
            method, path + "?rescan=true", json=_BODY if method == "POST" else None
        )
    assert response.status_code == 400 and not use_case.mock_calls


@pytest.mark.parametrize("scope_id", ["0", "-1", "true", str(MAX_SAFE_JSON_INTEGER + 1)])
@pytest.mark.parametrize("component", ["101", "202"])
def test_read_scope_identifiers_are_bounded(scope_id: str, component: str) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.get(_STATUS.replace(component, scope_id))
    assert response.status_code == 422 and not use_case.mock_calls


def test_request_cancellation_reaches_the_operation_without_background_mutation() -> None:
    async def scenario() -> None:
        entered, cancelled = asyncio.Event(), asyncio.Event()
        use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)

        async def configure(_command: ConfigureHistory) -> None:
            entered.set()
            try:
                await asyncio.Future[None]()
            finally:
                cancelled.set()

        use_case.configure.side_effect = configure
        async with AsyncClient(
            transport=ASGITransport(app=_app(use_case)), base_url="https://test"
        ) as client:
            task = asyncio.create_task(client.post(HISTORY_CONFIGURATION_PATH, json=_BODY))
            async with asyncio.timeout(5):
                await entered.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await cancelled.wait()
        assert use_case.configure.await_count == 1

    asyncio.run(scenario())


async def test_write_bulkhead_rejects_excess_without_waiting_for_a_slot() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    active = 0

    async def held(_command: ConfigureHistory) -> HistoryConfigured:
        nonlocal active
        active += 1
        if active == 2:
            entered.set()
        await release.wait()
        return HistoryConfigured(_DATASET, False)

    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    use_case.configure.side_effect = held
    async with AsyncClient(
        transport=ASGITransport(app=_app(use_case)), base_url="http://test"
    ) as client:
        async with asyncio.timeout(3):
            async with asyncio.TaskGroup() as group:
                requests = [
                    group.create_task(client.post(HISTORY_CONFIGURATION_PATH, json=_BODY))
                    for _ in range(2)
                ]
                try:
                    await entered.wait()
                    excess = await client.post(HISTORY_CONFIGURATION_PATH, json=_BODY)
                    assert excess.status_code == 503 and excess.json() == {
                        "ok": False,
                        "error": "overloaded",
                    }
                    assert active == 2 and excess.headers["cache-control"] == "no-store"
                finally:
                    release.set()
            assert all(request.result().status_code == 200 for request in requests)
