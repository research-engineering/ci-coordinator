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
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    InvalidCredential,
    WorkbenchRouteDependencies,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable
from ci_coordinator.workbench_read_models import (
    ReplayView,
    RepositoryDataSnapshot,
    RepositoryWorkbenchSnapshot,
    TruncationView,
    WorkbenchForbidden,
    WorkbenchResult,
    WorkbenchUnavailable,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


@dataclass
class _UseCase:
    outcome: WorkbenchResult
    calls: list[tuple[str, RepositoryScope, int]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        limit: int,
    ) -> WorkbenchResult:
        self.calls.append((actor, scope, limit))
        return self.outcome


def test_workbench_route_returns_one_typed_redacted_snapshot() -> None:
    use_case = _UseCase(_snapshot())

    response = _client("operator", use_case).get("/api/v1/workbench/repositories/1/2?limit=7")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "ok": True,
        "scope": {"installationId": 1, "repositoryId": 2},
        "observedAt": "2026-07-17T12:00:00Z",
        "ledgerRevision": 0,
        "plans": [],
        "runs": [],
        "overrides": [],
        "configEpochs": [],
        "auditEvents": [],
        "replay": {
            "status": "valid",
            "snapshotRevision": 0,
            "verifiedRevision": 0,
            "reason": None,
        },
        "truncated": {
            "plans": False,
            "runs": False,
            "overrides": False,
            "configEpochs": False,
            "auditEvents": False,
        },
    }
    assert use_case.calls == [(ACTOR, RepositoryScope(1, 2), 7)]
    assert all(
        forbidden not in response.text
        for forbidden in ("signature", "leaseToken", "sourceBytes", "bearerToken")
    )


def test_workbench_route_authenticates_before_calling_the_use_case() -> None:
    use_case = _UseCase(_snapshot())

    response = _client(None, use_case).get("/api/v1/workbench/repositories/1/2")

    assert (response.status_code, response.json()) == (
        401,
        {"ok": False, "error": "unauthenticated"},
    )
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert use_case.calls == []


def test_workbench_route_preserves_forbidden_and_unavailable_outcomes() -> None:
    forbidden = _client("operator", _UseCase(WorkbenchForbidden())).get(
        "/api/v1/workbench/repositories/1/2"
    )
    unavailable = _client("operator", _UseCase(WorkbenchUnavailable())).get(
        "/api/v1/workbench/repositories/1/2"
    )

    assert (forbidden.status_code, forbidden.json()) == (
        403,
        {"ok": False, "error": "forbidden"},
    )
    assert forbidden.headers["cache-control"] == "no-store"
    assert (unavailable.status_code, unavailable.json()) == (
        503,
        {"ok": False, "error": "unavailable"},
    )
    assert unavailable.headers["cache-control"] == "no-store"


def test_membership_failure_is_a_redacted_503_not_an_unexpected_500() -> None:
    class UnavailableAccess(_UseCase):
        async def __call__(
            self, *, actor: str, scope: RepositoryScope, limit: int
        ) -> WorkbenchResult:
            self.calls.append((actor, scope, limit))
            raise RepositoryAccessUnavailable("private provider diagnostic")

    use_case = UnavailableAccess(_snapshot())
    response = _client("operator", use_case).get(
        "/api/v1/workbench/repositories/1/2",
        headers={"X-Correlation-ID": "membership-check"},
    )
    assert (response.status_code, response.json()) == (503, {"ok": False, "error": "unavailable"})
    assert response.headers["cache-control"] == "no-store"
    correlation_id = response.headers["x-correlation-id"]
    assert len(correlation_id) == 32 and set(correlation_id) <= set("0123456789abcdef")
    assert correlation_id != "membership-check"
    assert len(use_case.calls) == 1
    assert _client(None, use_case).get("/api/v1/workbench/repositories/1/2").status_code == 401
    assert len(use_case.calls) == 1


def test_workbench_route_rejects_unbounded_queries_before_the_use_case() -> None:
    use_case = _UseCase(_snapshot())

    response = _client("operator", use_case).get("/api/v1/workbench/repositories/1/2?limit=21")

    assert (response.status_code, response.json()) == (422, {"code": "invalid_request"})
    assert response.headers["cache-control"] == "no-store"
    assert use_case.calls == []


def test_workbench_openapi_declares_the_scoped_authenticated_contract() -> None:
    app = create_app(
        HttpRouteDependencies(
            workbench=WorkbenchRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(human_principal()),
                role_admission=StaticRoleAdmission(),
                use_case=_UseCase(_snapshot()),
            )
        )
    )
    document = app.openapi()
    operation = document["paths"][
        "/api/v1/workbench/repositories/{installation_id}/{repository_id}"
    ]["get"]

    assert operation["operationId"] == "get_repository_workbench_snapshot"
    assert set(operation["responses"]) == {"200", "401", "403", "422", "500", "503"}
    assert operation["security"] == [{"ControlPlaneBearer": []}, {"ControlPlaneSession": []}]


def _client(actor: str | None, use_case: _UseCase) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                workbench=WorkbenchRouteDependencies(
                    authenticator=StaticControlPlaneAuthenticator(
                        InvalidCredential() if actor is None else human_principal()
                    ),
                    role_admission=StaticRoleAdmission(),
                    use_case=use_case,
                )
            )
        )
    )


def _snapshot() -> RepositoryWorkbenchSnapshot:
    data = RepositoryDataSnapshot(
        scope=RepositoryScope(1, 2),
        observed_at=NOW,
        ledger_revision=0,
        plans=(),
        runs=(),
        overrides=(),
        config_epochs=(),
        audit_events=(),
        truncated=TruncationView(False, False, False, False, False),
    )
    return RepositoryWorkbenchSnapshot(data, ReplayView("valid", 0, 0, None))
