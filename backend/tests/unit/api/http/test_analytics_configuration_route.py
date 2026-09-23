import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from ci_economics.purpose_configuration_factories import purpose_command, purpose_query
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
    HttpRouteDependencies,
    InvalidCredential,
    PurposeSettingsRouteDependencies,
)
from ci_coordinator.app.analytics_configuration import PurposeSettingsUseCase
from ci_coordinator.ci_economics.analytics_configuration import (
    MAX_PURPOSE_BYTES,
    PurposeSettingsSaved,
)
from ci_coordinator.control_plane_identity import CONTROL_PLANE_ROLES, ControlPlaneRole

PATH = "/api/v2/economics/repositories/101/202/history/analytics/settings"


def _body() -> dict[str, object]:
    return purpose_command().model_dump(mode="json", exclude={"actor"})


def _app(
    use_case: AsyncMock,
    *,
    authenticated: bool = True,
    csrf: bool = True,
    roles: frozenset[ControlPlaneRole] = CONTROL_PLANE_ROLES,
) -> FastAPI:
    return create_app(
        HttpRouteDependencies(
            purpose_settings=PurposeSettingsRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(
                    human_principal(roles=roles) if authenticated else InvalidCredential()
                ),
                role_admission=StaticRoleAdmission(),
                mutation_admission=StaticMutationAdmission(csrf),
                use_case=use_case,
            )
        ),
        include_operator_ui=False,
        request_timeout_seconds=30,
    )


@pytest.mark.parametrize("replayed", [False, True])
def test_put_uses_current_actor_and_exact_committed_response(replayed: bool) -> None:
    use_case = AsyncMock(spec=PurposeSettingsUseCase)
    command = purpose_command(actor=human_principal().actor_id)
    use_case.configure.return_value = PurposeSettingsSaved(command.successor(), replayed)
    use_case.read.return_value = command.successor()
    with TestClient(_app(use_case)) as client:
        response = client.put(PATH, json=_body())
        read = client.get(PATH + "?generation=1")
    assert response.status_code == read.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "operationId": command.operation_id,
        "outcome": "replayed" if replayed else "committed",
        "snapshot": command.successor().model_dump(mode="json"),
    }
    use_case.configure.assert_awaited_once_with(command)
    use_case.read.assert_awaited_once_with(actor=command.actor, query=purpose_query())


@pytest.mark.parametrize(
    "authenticated,csrf,roles,status",
    [
        (False, True, CONTROL_PLANE_ROLES, 401),
        (True, False, CONTROL_PLANE_ROLES, 403),
        (True, True, frozenset({"audit"}), 403),
    ],
)
def test_current_auth_and_csrf_deny_before_replay(
    authenticated: bool, csrf: bool, roles: frozenset[ControlPlaneRole], status: int
) -> None:
    use_case = AsyncMock(spec=PurposeSettingsUseCase)
    with TestClient(_app(use_case, authenticated=authenticated, csrf=csrf, roles=roles)) as client:
        response = client.put(PATH, json=_body())
    assert response.status_code == status
    use_case.configure.assert_not_awaited()


def test_get_requires_audit_not_configure() -> None:
    use_case = AsyncMock(spec=PurposeSettingsUseCase)
    with TestClient(_app(use_case, roles=frozenset({"configure"}))) as client:
        assert client.get(PATH + "?generation=1").status_code == 403
    use_case.read.assert_not_awaited()


@pytest.mark.parametrize(
    "changes",
    [
        {"repositoryId": 203},
        {"actor": "forged"},
        {"expected_revision": 0},
        {"generation": True},
        {"entries": [{"workflowId": 1, "jobName": "X", "purposes": ["lint", "lint"]}]},
        {"entries": [{"workflowId": 1, "jobName": "X", "purposes": ["test"]}] * 2},
        {"entries": [{"workflow_id": 1, "jobName": "X", "purposes": ["test"]}]},
        {"entries": [{"workflowId": 1, "jobName": "\ud800", "purposes": ["test"]}]},
    ],
)
def test_invalid_body_and_scope_do_not_reach_application(changes: dict[str, object]) -> None:
    use_case = AsyncMock(spec=PurposeSettingsUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.put(
            PATH,
            content=json.dumps({**_body(), **changes}),
            headers={"content-type": "application/json"},
        )
    assert response.status_code in {400, 422}
    use_case.configure.assert_not_awaited()


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?generation=1&generation=1",
        "?generation=1&actor=x",
        "?generation=0",
        "?generation=9007199254740992",
        "?generation=" + "1" * 257,
    ],
)
def test_closed_query_and_decimal_bounds(query: str) -> None:
    use_case = AsyncMock(spec=PurposeSettingsUseCase)
    with TestClient(_app(use_case)) as client:
        assert client.get(PATH + query).status_code == 400
    use_case.read.assert_not_awaited()


def test_body_bytes_duplicate_keys_and_content_encoding_rejected() -> None:
    use_case = AsyncMock(spec=PurposeSettingsUseCase)
    with TestClient(_app(use_case)) as client:
        assert (
            client.put(
                PATH,
                content=b" " * (MAX_PURPOSE_BYTES + 1),
                headers={"content-type": "application/json"},
            ).status_code
            == 413
        )
        assert (
            client.put(
                PATH,
                content=b'{"generation":1,"generation":1}',
                headers={"content-type": "application/json"},
            ).status_code
            == 400
        )
        assert (
            client.put(PATH, json=_body(), headers={"content-encoding": "gzip"}).status_code == 400
        )
        assert client.put(PATH + "?generation=1", json=_body()).status_code == 400
    use_case.configure.assert_not_awaited()


def test_two_request_bulkhead_has_no_queue() -> None:
    async def scenario() -> None:
        use_case = AsyncMock(spec=PurposeSettingsUseCase)
        entered = asyncio.Event()
        release = asyncio.Event()
        count = 0

        async def read(**_kwargs: object) -> object:
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
            await release.wait()
            return purpose_command().successor()

        use_case.read.side_effect = read
        async with AsyncClient(
            transport=ASGITransport(app=_app(use_case)), base_url="https://example.test"
        ) as client:
            first = asyncio.create_task(client.get(PATH + "?generation=1"))
            second = asyncio.create_task(client.get(PATH + "?generation=1"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                response = await client.get(PATH + "?generation=1")
                assert response.status_code == 503
                assert response.json() == {"ok": False, "error": "overloaded"}
                assert count == 2
                blocked_write = await client.put(PATH, json=_body())
                assert blocked_write.status_code == 503
                use_case.configure.assert_not_awaited()
            finally:
                release.set()
                await asyncio.gather(first, second)

    asyncio.run(scenario())
