from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    InvalidCredential,
    ProviderInventoryRouteDependencies,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.provider_inventory import (
    InstallationCatalog,
    InstallationCatalogResult,
    InstallationFailure,
    InstallationSummary,
    ProviderInventoryForbidden,
    ProviderInventoryIneligible,
    ProviderInventoryUnavailable,
    ProviderInventoryUseCase,
    RepositoryPage,
    RepositoryPageResult,
    RepositorySummary,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


@dataclass
class _UseCase:
    catalog: InstallationCatalogResult
    page: RepositoryPageResult
    calls: list[tuple[object, ...]] = field(default_factory=list)

    async def list_installations(
        self, *, actor: str, page: int = 1, per_page: int = 30
    ) -> InstallationCatalogResult:
        self.calls.append(("installations", actor, page, per_page))
        return self.catalog

    async def list_repositories(
        self,
        *,
        actor: str,
        installation_id: int,
        page: int,
        per_page: int,
    ) -> RepositoryPageResult:
        self.calls.append(("repositories", actor, installation_id, page, per_page))
        return self.page


def test_installation_route_preserves_partial_catalog_evidence() -> None:
    use_case = _UseCase(
        InstallationCatalog(
            NOW,
            False,
            (_installation(),),
            (InstallationFailure(2, "rate_limited", 30),),
        ),
        _page(),
    )

    response = _client("operator", use_case).get("/api/v1/workbench/installations")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "ok": True,
        "observedAt": "2026-07-18T12:00:00Z",
        "complete": False,
        "page": 1,
        "perPage": 30,
        "hasNextPage": False,
        "consistency": "best_effort",
        "installations": [
            {
                "installationId": 1,
                "accountId": 101,
                "accountLogin": "example",
                "accountType": "Organization",
                "repositorySelection": "selected",
                "state": "active",
            }
        ],
        "failures": [{"installationId": 2, "reason": "rate_limited", "retryAfterSeconds": 30}],
    }
    assert use_case.calls == [("installations", ACTOR, 1, 30)]


def test_repository_route_returns_scope_bound_page() -> None:
    use_case = _UseCase(InstallationCatalog(NOW, True, (), ()), _page())

    response = _client("operator", use_case).get(
        "/api/v1/workbench/installations/1/repositories?page=2&perPage=1"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["repositories"][0]["scope"] == {
        "installationId": 1,
        "repositoryId": 10,
    }
    assert response.json()["repositories"][0]["workbenchAuthorized"] is True
    assert response.json()["repositories"][0]["createdAt"] == "2026-07-18T12:00:00Z"
    assert use_case.calls == [("repositories", ACTOR, 1, 2, 1)]


def test_inventory_authentication_precedes_use_case_execution() -> None:
    use_case = _UseCase(InstallationCatalog(NOW, True, (), ()), _page())
    client = _client(None, use_case)

    installation_response = client.get("/api/v1/workbench/installations")
    repository_response = client.get("/api/v1/workbench/installations/1/repositories")

    for response in (installation_response, repository_response):
        assert response.status_code == 401
        assert response.json()["error"] == "unauthenticated"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["cache-control"] == "no-store"
    assert use_case.calls == []


def test_inventory_failures_keep_distinct_http_semantics() -> None:
    cases = (
        (ProviderInventoryForbidden(), 403, "forbidden"),
        (ProviderInventoryIneligible("suspended"), 409, "suspended"),
        (ProviderInventoryUnavailable("not_found"), 404, "not_found"),
        (ProviderInventoryUnavailable("rate_limited", 30), 429, "rate_limited"),
        (
            ProviderInventoryUnavailable("malformed_provider_response"),
            503,
            "malformed_provider_response",
        ),
    )
    for outcome, expected_status, expected_error in cases:
        use_case = _UseCase(InstallationCatalog(NOW, True, (), ()), outcome)

        response = _client("operator", use_case).get(
            "/api/v1/workbench/installations/1/repositories"
        )

        assert (response.status_code, response.json()["error"]) == (
            expected_status,
            expected_error,
        )
        assert response.headers["cache-control"] == "no-store"
        if expected_status == 429:
            assert response.headers["retry-after"] == "30"


def test_inventory_query_bounds_stop_before_use_case() -> None:
    use_case = _UseCase(InstallationCatalog(NOW, True, (), ()), _page())

    response = _client("operator", use_case).get(
        "/api/v1/workbench/installations/1/repositories?page=0&perPage=101"
    )

    assert (response.status_code, response.json()) == (422, {"code": "invalid_request"})
    assert response.headers["cache-control"] == "no-store"
    assert InvalidRequestBody.model_validate(response.json()).code == "invalid_request"
    assert use_case.calls == []


def test_unexpected_inventory_failure_is_a_redacted_internal_error() -> None:
    class _UnavailableUseCase:
        async def list_installations(
            self, *, actor: str, page: int = 1, per_page: int = 30
        ) -> InstallationCatalogResult:
            raise RuntimeError("provider diagnostic")

        async def list_repositories(
            self,
            *,
            actor: str,
            installation_id: int,
            page: int,
            per_page: int,
        ) -> RepositoryPageResult:
            raise RuntimeError("provider diagnostic")

    client = _client("operator", _UnavailableUseCase(), raise_server_exceptions=False)

    responses = (
        client.get("/api/v1/workbench/installations"),
        client.get("/api/v1/workbench/installations/1/repositories"),
    )

    for response in responses:
        assert (response.status_code, response.json()) == (
            500,
            {"code": "internal_error"},
        )
        assert response.headers["cache-control"] == "no-store"


def test_installation_query_is_bounded_and_forwarded() -> None:
    use_case = _UseCase(InstallationCatalog(NOW, True, (), (), page=2, per_page=1), _page())
    client = _client("operator", use_case)
    assert client.get("/api/v1/workbench/installations?page=2&perPage=1").json()["page"] == 2
    assert use_case.calls == [("installations", ACTOR, 2, 1)]
    for query in ("page=0", "page=10001", "perPage=0", "perPage=101"):
        assert client.get(f"/api/v1/workbench/installations?{query}").status_code == 422
    assert len(use_case.calls) == 1


def _client(
    actor: str | None,
    use_case: ProviderInventoryUseCase,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                provider_inventory=ProviderInventoryRouteDependencies(
                    authenticator=StaticControlPlaneAuthenticator(
                        InvalidCredential() if actor is None else human_principal()
                    ),
                    role_admission=StaticRoleAdmission(),
                    use_case=use_case,
                )
            )
        ),
        raise_server_exceptions=raise_server_exceptions,
    )


def _installation() -> InstallationSummary:
    return InstallationSummary(1, 101, "example", "Organization", "selected", "active")


def _page() -> RepositoryPage:
    repository = RepositorySummary(
        scope=RepositoryScope(1, 10),
        node_id="R_10",
        owner_id=101,
        owner_login="example",
        name="ci-coordinator",
        full_name="example/ci-coordinator",
        visibility="private",
        default_branch="master",
        archived=False,
        disabled=False,
        fork=False,
        workbench_authorized=True,
        created_at=NOW,
    )
    return RepositoryPage(_installation(), NOW, 2, 1, 2, False, (repository,))
