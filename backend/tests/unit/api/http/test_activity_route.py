from dataclasses import dataclass, field

import pytest
from control_plane_http_support import (
    NOW,
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.control_plane_authentication import ControlPlaneRoleAuthorizer
from ci_coordinator.api.http.dependencies import HttpRouteDependencies, InvalidCredential
from ci_coordinator.api.http.routers.activity import (
    ACTIVITY_PATHS,
    ActivityRouteDependencies,
    build_activity_router,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import BreakGlassPrincipal, ControlPlanePrincipal
from ci_coordinator.control_plane_identity.activity import ActivityPrincipal
from ci_coordinator.control_plane_identity.activity_query import (
    ActivityPage,
    ActivityQuery,
    ActivityReadService,
)
from ci_coordinator.kernel import FixedClock

_PATH = "/api/v1/activity/repositories/1/2"
_PARAMS = {"since": "2026-09-01T12:00:00Z", "until": "2026-09-02T12:00:00Z"}


@dataclass
class _Store:
    calls: int = 0
    issuer_allowed: bool = False
    last_query: ActivityQuery | None = None

    async def page(
        self, query: ActivityQuery, principal: ActivityPrincipal, cursor: str | None
    ) -> ActivityPage:
        self.calls += 1
        self.last_query = query
        return ActivityPage((), None, NOW, None, "audit_reference_only")

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        return scope == RepositoryScope(1, 2)

    async def allows_issuer(self, *, actor: str, issuer: str) -> bool:
        return self.issuer_allowed and issuer == human_principal().issuer


def _app(principal: ControlPlanePrincipal | InvalidCredential) -> tuple[FastAPI, _Store]:
    store = _Store()
    app = FastAPI()
    app.include_router(
        build_activity_router(
            ActivityRouteDependencies(
                StaticControlPlaneAuthenticator(principal),
                StaticRoleAdmission(),
                ActivityReadService(store, store),
                FixedClock(NOW),
            )
        )
    )
    return app, store


@pytest.mark.parametrize(
    ("principal", "status"),
    [
        (InvalidCredential(), 401),
        (human_principal(roles=frozenset({"read"})), 403),
        (BreakGlassPrincipal("emergency"), 403),
        (human_principal(), 200),
    ],
)
@pytest.mark.parametrize("suffix", ["", "/export"])
def test_activity_authentication_and_export_are_bounded(
    principal: ControlPlanePrincipal | InvalidCredential, status: int, suffix: str
) -> None:
    app, store = _app(principal)
    with TestClient(app) as client:
        response = client.get(_PATH + suffix, params=_PARAMS)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert store.calls == (1 if status == 200 else 0)
    if status == 200:
        assert response.json()["items"] == []
        assert response.json()["integrity"] == "audit_reference_only"
        assert ("content-disposition" in response.headers) == bool(suffix)


@pytest.mark.parametrize(
    "query",
    [
        "limit=101",
        "limit=0",
        "since=secret-password",
        "unexpected=secret-password",
        "actor=secret-password",
        "action=secret-password",
        "cursor=" + "s" * 1025,
    ],
)
def test_invalid_and_duplicate_queries_never_echo_inputs(query: str) -> None:
    app, store = _app(human_principal())
    with TestClient(app) as client:
        response = client.get(
            _PATH + "?since=2026-09-01T12:00:00Z&until=2026-09-02T12:00:00Z&" + query
        )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "invalid_request"}
    assert response.headers["cache-control"] == "no-store"
    assert store.calls == 0


@pytest.mark.parametrize("path", ["/api/v1/activity/repositories/1/3", "/api/v1/activity/security"])
def test_scope_grants_are_not_transitive(path: str) -> None:
    app, store = _app(human_principal())
    params = {
        **_PARAMS,
        **({"issuer": human_principal().issuer} if path.endswith("security") else {}),
    }
    with TestClient(app) as client:
        response = client.get(path, params=params)
    assert response.status_code == 403
    assert store.calls == 0


@dataclass
class _Observer:
    events: list[tuple[str, str, str | None]] = field(default_factory=list)

    async def login_diagnostic(self, action: str) -> None:
        self.events.append(("login", action, None))

    async def principal_diagnostic(self, principal: ActivityPrincipal, action: str) -> None:
        self.events.append(("principal", action, principal.actor_id))


def test_real_role_hook_only_receives_verified_principal_and_closed_action() -> None:
    principal = human_principal(roles=frozenset({"read"}))
    observer, store = _Observer(), _Store()
    app = FastAPI()
    app.include_router(
        build_activity_router(
            ActivityRouteDependencies(
                StaticControlPlaneAuthenticator(principal),
                ControlPlaneRoleAuthorizer(FixedClock(NOW), observer),
                ActivityReadService(store, store),
                FixedClock(NOW),
            )
        )
    )
    with TestClient(app) as client:
        response = client.get(_PATH, params=_PARAMS, headers={"X-Untrusted": "secret-marker"})
    assert response.status_code == 403
    assert observer.events == [("principal", "role_denied", principal.actor_id)]
    assert store.calls == 0


@pytest.mark.parametrize("suffix", ["", "/export"])
@pytest.mark.parametrize("explicit", [False, True])
def test_security_issuer_defaults_only_from_verified_identity(suffix: str, explicit: bool) -> None:
    app, store = _app(human_principal())
    store.issuer_allowed = True
    params = {**_PARAMS, **({"issuer": human_principal().issuer} if explicit else {})}
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/security" + suffix, params=params)
    assert response.status_code == 200
    assert store.last_query is not None
    assert store.last_query.issuer == human_principal().issuer
    assert response.json()["context"] == {
        "source": "security",
        "issuer": human_principal().issuer,
        "installationId": None,
        "repositoryId": None,
    }


@pytest.mark.parametrize("issuer", [None, "https://foreign.example/realm"])
def test_inferred_or_explicit_issuer_never_bypasses_scope_authorization(issuer: str | None) -> None:
    app, store = _app(human_principal())
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/activity/security",
            params={**_PARAMS, **({"issuer": issuer} if issuer is not None else {})},
        )
    assert response.status_code == 403
    assert store.calls == 0


def test_break_glass_cannot_infer_a_security_issuer() -> None:
    app, store = _app(BreakGlassPrincipal("emergency"))
    with TestClient(app) as client:
        response = client.get("/api/v1/activity/security", params=_PARAMS)
    assert response.status_code == 403
    assert store.calls == 0


def test_production_registry_mounts_all_activity_routes_and_auth_contracts() -> None:
    store = _Store()
    app = create_app(
        HttpRouteDependencies(
            activity=ActivityRouteDependencies(
                StaticControlPlaneAuthenticator(InvalidCredential()),
                StaticRoleAdmission(),
                ActivityReadService(store, store),
                FixedClock(NOW),
            )
        ),
        include_operator_ui=False,
    )
    schema = app.openapi()
    assert set(ACTIVITY_PATHS) <= set(schema["paths"])
    with TestClient(app) as client:
        for path in ACTIVITY_PATHS:
            contract = schema["paths"][path]["get"]
            assert contract["security"]
            assert "WWW-Authenticate" in contract["responses"]["401"]["headers"]
            response = client.get(
                path.replace("{installation_id}", "1").replace("{repository_id}", "2"),
                params=_PARAMS,
            )
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
            assert "www-authenticate" in response.headers
    assert store.calls == 0
