from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import cast

import pytest
from ci_economics.factories import ATTEMPT, NOW
from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.control_plane_security import ControlPlaneMutationGuard
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CiEconomicsSourceRouteDependencies,
    ControlPlaneAuthenticationResult,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.routers.ci_economics_sources import (
    MAX_SOURCE_BODY_BYTES,
    SOURCE_DISCOVERY_PATH,
    SOURCE_REGISTRATION_PATH,
)
from ci_coordinator.app.ci_economics_sources import (
    CiEconomicsSourceForbidden,
    CiEconomicsSourceUnavailable,
    ProviderSourceRegistrationAvailable,
    SourceDiscoveryResult,
    SourceRegistrationResult,
)
from ci_coordinator.ci_economics.discovery import ProviderRunDiscoveryPage, RunDiscoveryWindow
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.ci_economics.sources import (
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import (
    BrowserIdentityUseCase,
    ControlPlaneRole,
    KeycloakHumanPrincipal,
)

SOURCE = ProviderRunCollectionSource(ATTEMPT, NOW, "2026-03-10", "e" * 64)
WINDOW = RunDiscoveryWindow(NOW, NOW + timedelta(days=1))
PAGE = ProviderRunDiscoveryPage(ATTEMPT.scope, WINDOW, 1, 1, (SOURCE,), "exhausted")
REGISTERED = ProviderSourceRegistrationAvailable(SOURCE, "registered")
PRINCIPAL = human_principal()
REGISTER: dict[str, object] = {
    "installationId": 101,
    "repositoryId": 202,
    "workflowRunId": 303,
    "runAttempt": 2,
}
DISCOVER: dict[str, object] = {
    "installationId": 101,
    "repositoryId": 202,
    "createdFrom": "2026-09-04T10:00:00Z",
    "createdThrough": "2026-09-05T10:00:00Z",
    "pageNumber": 1,
}
OPERATIONS: list[tuple[str, dict[str, object]]] = [
    (SOURCE_DISCOVERY_PATH, DISCOVER),
    (SOURCE_REGISTRATION_PATH, REGISTER),
]


@dataclass
class UseCase:
    discovery: SourceDiscoveryResult = PAGE
    registration: SourceRegistrationResult = REGISTERED
    calls: list[tuple[str, object]] = field(default_factory=list)
    wait_for_release: asyncio.Event | None = None
    entered: asyncio.Event | None = None

    async def discover_page(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        window: RunDiscoveryWindow,
        page_number: int,
    ) -> SourceDiscoveryResult:
        self.calls.append(("discover", (actor, scope, window, page_number)))
        await self.wait()
        return self.discovery

    async def register_attempt(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        workflow_run_id: int,
        run_attempt: int,
    ) -> SourceRegistrationResult:
        self.calls.append(("register", (actor, scope, workflow_run_id, run_attempt)))
        await self.wait()
        return self.registration

    async def wait(self) -> None:
        if self.entered is not None:
            self.entered.set()
        if self.wait_for_release is not None:
            await self.wait_for_release.wait()


def app(
    use_case: UseCase,
    *,
    principal: ControlPlaneAuthenticationResult = PRINCIPAL,
    integrity: bool = True,
    timeout: int = 5,
) -> FastAPI:
    return create_app(
        HttpRouteDependencies(
            ci_economics_sources=CiEconomicsSourceRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(principal),
                role_admission=StaticRoleAdmission(),
                mutation_admission=StaticMutationAdmission(integrity),
                use_case=use_case,
            )
        ),
        request_timeout_seconds=timeout,
        include_operator_ui=False,
    )


def test_discovery_projects_an_explicit_page_without_claiming_population_completeness() -> None:
    use_case = UseCase()
    with TestClient(app(use_case)) as client:
        response = client.post(SOURCE_DISCOVERY_PATH, json=DISCOVER)
    body = response.json()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert body == {
        "schemaVersion": "ci-economics-source-discovery/v2",
        "ok": True,
        "installationId": 101,
        "repositoryId": 202,
        "createdFrom": DISCOVER["createdFrom"],
        "createdThrough": DISCOVER["createdThrough"],
        "pageNumber": 1,
        "providerTotal": 1,
        "termination": "exhausted",
        "sources": [
            {
                "sourceKind": "provider_run",
                "sourceId": SOURCE.source_id,
                "attempt": {**REGISTER, "headSha": "b" * 40},
                "runCreatedAt": DISCOVER["createdFrom"],
                "providerApiVersion": "2026-03-10",
                "sourceEvidenceDigest": "e" * 64,
            }
        ],
    }
    assert use_case.calls == [("discover", (ACTOR, ATTEMPT.scope, WINDOW, 1))]


@pytest.mark.parametrize(
    "outcome,code",
    [
        ("registered", 201),
        ("replayed", 200),
        ("source_conflict", 409),
        ("outside_source_window", 409),
        ("capacity_reached", 409),
    ],
)
def test_registration_projects_every_outcome_without_replacing_provenance(
    outcome: ProviderSourceRegistrationResult,
    code: int,
) -> None:
    use_case = UseCase(registration=ProviderSourceRegistrationAvailable(SOURCE, outcome))
    with TestClient(app(use_case)) as client:
        response = client.post(SOURCE_REGISTRATION_PATH, json=REGISTER)
    assert response.status_code == code
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["outcome"] == outcome
    assert response.json()["source"]["attempt"] == {**REGISTER, "headSha": "b" * 40}
    assert response.json()["source"]["sourceEvidenceDigest"] == "e" * 64
    assert use_case.calls == [("register", (ACTOR, ATTEMPT.scope, 303, 2))]


@pytest.mark.parametrize("path,body", OPERATIONS)
@pytest.mark.parametrize(
    "principal,code",
    [
        (InvalidCredential(), 401),
        (AuthenticationDependencyUnavailable(), 503),
        (human_principal(roles=frozenset({"read"})), 403),
    ],
)
def test_authentication_and_roles_precede_use_case(
    path: str,
    body: dict[str, object],
    principal: ControlPlaneAuthenticationResult,
    code: int,
) -> None:
    use_case = UseCase()
    with TestClient(app(use_case, principal=principal)) as client:
        response = client.post(path, json=body)
    assert response.status_code == code
    assert response.headers["cache-control"] == "no-store"
    if code == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    assert use_case.calls == []


@pytest.mark.parametrize("path,body", OPERATIONS)
def test_failed_request_integrity_never_reaches_use_case(
    path: str, body: dict[str, object]
) -> None:
    use_case = UseCase()
    with TestClient(app(use_case, integrity=False)) as client:
        response = client.post(path, json=body)
    assert response.status_code == 403
    assert use_case.calls == []


@pytest.mark.parametrize("path,body", OPERATIONS)
@pytest.mark.parametrize(
    "headers,admitted",
    [
        ([("Origin", "https://coordinator.example"), ("X-CSRF-Token", "bound-token")], True),
        ([], False),
        ([("Origin", "https://other.example"), ("X-CSRF-Token", "bound-token")], False),
        ([("Origin", "https://coordinator.example")], False),
        ([("Origin", "https://coordinator.example"), ("X-CSRF-Token", "wrong")], False),
        (
            [
                ("Origin", "https://coordinator.example"),
                ("Origin", "https://coordinator.example"),
                ("X-CSRF-Token", "bound-token"),
            ],
            False,
        ),
        (
            [
                ("Origin", "https://coordinator.example"),
                ("X-CSRF-Token", "bound-token"),
                ("X-CSRF-Token", "bound-token"),
            ],
            False,
        ),
    ],
)
def test_real_mutation_guard_binds_browser_origin_and_unique_csrf_before_source_operation(
    path: str, body: dict[str, object], headers: list[tuple[str, str]], admitted: bool
) -> None:
    class SessionVerifier:
        def csrf_matches(self, principal: KeycloakHumanPrincipal, candidate: str | None) -> bool:
            return principal == PRINCIPAL and candidate == "bound-token"

    use_case = UseCase()
    application = create_app(
        HttpRouteDependencies(
            ci_economics_sources=CiEconomicsSourceRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(PRINCIPAL),
                role_admission=StaticRoleAdmission(),
                mutation_admission=ControlPlaneMutationGuard(
                    human=cast(BrowserIdentityUseCase, SessionVerifier()),
                    public_origin="https://coordinator.example",
                ),
                use_case=use_case,
            )
        ),
        include_operator_ui=False,
    )
    with TestClient(application) as client:
        response = client.post(path, json=body, headers=headers)
    assert response.status_code == (
        (201 if path == SOURCE_REGISTRATION_PATH else 200) if admitted else 403
    )
    assert bool(use_case.calls) is admitted
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "role,path,body,code",
    [
        ("audit", SOURCE_DISCOVERY_PATH, DISCOVER, 200),
        ("audit", SOURCE_REGISTRATION_PATH, REGISTER, 403),
        ("configure", SOURCE_DISCOVERY_PATH, DISCOVER, 403),
        ("configure", SOURCE_REGISTRATION_PATH, REGISTER, 201),
    ],
)
def test_discovery_does_not_implicitly_grant_registration(
    role: ControlPlaneRole,
    path: str,
    body: dict[str, object],
    code: int,
) -> None:
    use_case = UseCase()
    with TestClient(app(use_case, principal=human_principal(roles=frozenset({role})))) as client:
        response = client.post(path, json=body)
    assert response.status_code == code
    assert len(use_case.calls) == (0 if code == 403 else 1)


@pytest.mark.parametrize(
    "result,code,error",
    [
        (CiEconomicsSourceForbidden(), 403, "forbidden"),
        (CiEconomicsSourceUnavailable(), 503, "unavailable"),
        (ProviderAttemptDeferred("provider_unavailable"), 503, "provider_unavailable"),
        (ProviderAttemptDeferred("provider_binding_mismatch"), 503, "provider_binding_mismatch"),
    ],
)
def test_application_refusals_are_not_success(
    result: SourceRegistrationResult,
    code: int,
    error: str,
) -> None:
    with TestClient(app(UseCase(registration=result))) as client:
        response = client.post(SOURCE_REGISTRATION_PATH, json=REGISTER)
    assert (response.status_code, response.json()) == (code, {"ok": False, "error": error})
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path,body", OPERATIONS)
@pytest.mark.parametrize(
    "mutation", ["duplicate", "nested", "deep", "nonfinite", "empty", "syntax"]
)
def test_raw_json_admission_precedes_authentication_and_framework_recursion(
    path: str,
    body: dict[str, object],
    mutation: str,
) -> None:
    if mutation == "duplicate":
        raw = json.dumps(body)[:-1] + ', "installationId": 101}'
    elif mutation == "nested":
        raw = '{"installationId": {"x": 101}}'
    elif mutation == "deep":
        raw = "[" * 1_000 + "0" + "]" * 1_000
    elif mutation == "nonfinite":
        raw = '{"installationId": NaN}'
    else:
        raw = "" if mutation == "empty" else "{"
    assert len(raw.encode()) <= MAX_SOURCE_BODY_BYTES
    use_case = UseCase()
    with TestClient(app(use_case, principal=InvalidCredential())) as client:
        response = client.post(path, content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "invalid_request"}
    assert use_case.calls == []


@pytest.mark.parametrize("path,body", OPERATIONS)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"content-type": "text/plain"},
        {"content-type": "application/json; charset=utf-8"},
        {"content-type": "application/json", "content-encoding": "gzip"},
    ],
)
def test_unsupported_wire_representation_cannot_reach_use_case(
    path: str,
    body: dict[str, object],
    headers: dict[str, str],
) -> None:
    use_case = UseCase()
    with TestClient(app(use_case)) as client:
        response = client.post(path, content=json.dumps(body), headers=headers)
    assert response.status_code == 400
    assert use_case.calls == []


@pytest.mark.parametrize("field_name", tuple(REGISTER))
@pytest.mark.parametrize("value", [None, True, "1", 1.0, 0, -1, 2**53])
def test_registration_scalars_are_strict(field_name: str, value: object) -> None:
    use_case = UseCase()
    with TestClient(app(use_case)) as client:
        response = client.post(SOURCE_REGISTRATION_PATH, json={**REGISTER, field_name: value})
    assert response.status_code == 422
    assert use_case.calls == []


@pytest.mark.parametrize(
    "operand,value",
    [
        ("createdFrom", "2026-02-30T10:00:00Z"),
        ("createdFrom", "2026-09-06T10:00:00Z"),
        ("createdThrough", "2026-09-12T10:00:00Z"),
        ("createdFrom", "2026-09-04T10:00:00+00:00"),
        ("createdFrom", "2026-09-04T10:00:00.000Z"),
        ("pageNumber", 0),
        ("pageNumber", 11),
        ("pageNumber", True),
        ("pageNumber", "1"),
    ],
)
def test_discovery_uses_the_owned_window_and_page_bounds(operand: str, value: object) -> None:
    use_case = UseCase()
    with TestClient(app(use_case)) as client:
        response = client.post(SOURCE_DISCOVERY_PATH, json={**DISCOVER, operand: value})
    assert response.status_code == 422
    assert use_case.calls == []


@pytest.mark.parametrize("path,body", OPERATIONS)
def test_body_limit_precedes_json_and_use_case(path: str, body: dict[str, object]) -> None:
    use_case = UseCase()
    with TestClient(app(use_case)) as client:
        response = client.post(
            path,
            content=json.dumps(body) + " " * MAX_SOURCE_BODY_BYTES,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"
    assert use_case.calls == []


async def test_source_routes_share_a_no_queue_bulkhead_and_release_capacity() -> None:
    released = asyncio.Event()
    entered = asyncio.Event()
    use_case = UseCase(wait_for_release=released, entered=entered)
    async with AsyncClient(
        transport=ASGITransport(app=app(use_case)), base_url="https://test"
    ) as client:
        first = asyncio.create_task(client.post(SOURCE_DISCOVERY_PATH, json=DISCOVER))
        await asyncio.wait_for(entered.wait(), 2)
        entered.clear()
        second = asyncio.create_task(client.post(SOURCE_REGISTRATION_PATH, json=REGISTER))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            rejected = await client.post(SOURCE_DISCOVERY_PATH, json=DISCOVER)
            assert rejected.status_code == 503
            assert rejected.json() == {"ok": False, "error": "overloaded"}
            assert len(use_case.calls) == 2
        finally:
            released.set()
            results = await asyncio.gather(first, second)
        assert [result.status_code for result in results] == [200, 201]
        assert (await client.post(SOURCE_REGISTRATION_PATH, json=REGISTER)).status_code == 201


@pytest.mark.parametrize("path,body", OPERATIONS)
async def test_absolute_deadline_releases_capacity(path: str, body: dict[str, object]) -> None:
    released = asyncio.Event()
    use_case = UseCase(wait_for_release=released)
    async with AsyncClient(
        transport=ASGITransport(app=app(use_case, timeout=1)), base_url="https://test"
    ) as client:
        response = await client.post(path, json=body)
        assert response.status_code == 503
        assert response.json() == {"ok": False, "error": "unavailable"}
        released.set()
        assert (await client.post(path, json=body)).status_code in {200, 201}


def test_openapi_exposes_native_strict_bodies_and_operation_specific_outcomes() -> None:
    document = app(UseCase()).openapi()
    for path, body in OPERATIONS:
        operation = document["paths"][path]["post"]
        reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        schema = document["components"]["schemas"][reference.rsplit("/", 1)[-1]]
        assert set(schema["required"]) == set(body)
        assert schema["additionalProperties"] is False
        assert {"400", "401", "403", "413", "422", "503"} <= set(operation["responses"])
        assert operation["security"]
    registration = document["paths"][SOURCE_REGISTRATION_PATH]["post"]
    assert {"200", "201", "409"} <= set(registration["responses"])
