from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

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
from ci_coordinator.audit_replay import (
    AuditEventInput,
    build_audit_event,
    prepare_audit_event,
    verify_audit_event_integrity,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable
from ci_coordinator.persistence.audit_codec import prepared_record_to_row
from ci_coordinator.persistence.workbench_projection import project_audit_events
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


def test_fractional_payload_matches_complete_frontend_wire_vector() -> None:
    event_input = AuditEventInput(
        idempotency_key="numeric-response-1",
        installation_id=1,
        repository_id=2,
        subject_type="observation",
        subject_id="numeric-observation",
        event_type="numeric-payload/v1",
        created_at="2026-07-17T12:00:00.000Z",
        actor="operator:example",
        payload={"fraction": 0.5, "nested": [-0.25, {"weight": 1.5}]},
    )
    record = build_audit_event(event_input, None)
    assert verify_audit_event_integrity(record) is None
    assert record.payload_hash == "2ef414fdab8cdf4548c0f901f5891ad1321f14828a222140f4454d25131a065a"
    assert record.input_hash == "037b38ab5935c4f60b7d60a21f2a5905621c1138e40ad17ac8fc0182d1d8a505"
    assert record.event_hash == "f052745ce41ffdd6b9fba64a4835a1a85befda5a43f3a8fd670d5dd0e8c3f9ab"
    events = project_audit_events(
        [prepared_record_to_row(record, prepare_audit_event(event_input))]
    )
    snapshot = RepositoryWorkbenchSnapshot(
        replace(_snapshot().data, ledger_revision=1, audit_events=events),
        ReplayView("valid", 1, 1, None),
    )
    use_case = _UseCase(snapshot)
    with _client("operator", use_case) as client:
        response = client.get("/api/v1/workbench/repositories/1/2?limit=7")
    vector = (
        Path(__file__).resolve().parents[5] / "frontend/tests/workbenchNumericResponse.json"
    ).read_bytes()
    assert vector.endswith(b"\n") and vector.count(b"\n") == 1
    assert response.status_code == 200
    assert response.content + b"\n" == vector
    assert response.json()["auditEvents"][0]["payload"] == {
        "fraction": 0.5,
        "nested": [-0.25, {"weight": 1.5}],
    }
    assert use_case.calls == [(ACTOR, RepositoryScope(1, 2), 7)]


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
