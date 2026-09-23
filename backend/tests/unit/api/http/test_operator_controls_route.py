from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    InvalidCredential,
    OperatorControlsRouteDependencies,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls import (
    ActiveOverride,
    OverrideApplied,
    OverrideCommand,
    OverrideConflict,
    OverrideDuplicate,
    OverrideResult,
    OverrideUnavailable,
)

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


@dataclass
class RecordingUseCase:
    outcome: OverrideResult
    commands: list[OverrideCommand] = field(default_factory=list)

    async def __call__(self, command: OverrideCommand) -> OverrideResult:
        self.commands.append(command)
        return self.outcome


@pytest.mark.parametrize(
    "result_type,duplicate", [(OverrideApplied, False), (OverrideDuplicate, True)]
)
def test_operator_route_uses_authenticated_actor_and_returns_a_typed_result(
    result_type: type[OverrideApplied] | type[OverrideDuplicate], duplicate: bool
) -> None:
    command = _command(actor=ACTOR)
    use_case = RecordingUseCase(result_type(ActiveOverride.create(command, NOW)))
    client = _client("authenticated-operator", use_case)

    response = client.post("/api/v1/overrides/full-ci", json=_body())

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "ok": True,
        "overrideId": ActiveOverride.create(command, NOW).override_id,
        "duplicate": duplicate,
    }
    assert use_case.commands == [command]


def test_operator_route_never_accepts_actor_from_the_request_body() -> None:
    use_case = RecordingUseCase(OverrideUnavailable())
    client = _client("authenticated-operator", use_case)

    unauthenticated = _client(None, use_case).post("/api/v1/overrides/full-ci", json=_body())
    spoofed_actor = client.post(
        "/api/v1/overrides/full-ci",
        json={**_body(), "actor": "spoofed"},
    )

    assert unauthenticated.status_code == 401
    assert spoofed_actor.status_code == 422
    assert unauthenticated.headers["cache-control"] == "no-store"
    assert spoofed_actor.headers["cache-control"] == "no-store"
    assert use_case.commands == []


def test_disable_omission_accepts_no_subject_and_projects_repository_scope() -> None:
    command = _command(actor=ACTOR, kind="disable_omission")
    use_case = RecordingUseCase(OverrideApplied(ActiveOverride.create(command, NOW)))
    body = _body(kind="disable_omission")

    response = _client("authenticated-operator", use_case).post(
        "/api/v1/overrides/full-ci",
        json=body,
    )

    assert response.status_code == 202
    assert use_case.commands == [command]


def test_enable_omission_requires_and_projects_the_exact_disabled_override() -> None:
    command = _command(actor=ACTOR, kind="enable_omission")
    use_case = RecordingUseCase(OverrideApplied(ActiveOverride.create(command, NOW)))

    response = _client("authenticated-operator", use_case).post(
        "/api/v1/overrides/full-ci",
        json=_body(kind="enable_omission"),
    )

    assert response.status_code == 202
    assert use_case.commands == [command]


@pytest.mark.parametrize(
    ("kind", "subject_id"),
    [
        ("force_full_ci", None),
        ("disable_omission", "repository-control"),
    ],
)
def test_override_kind_determines_exact_subject_shape(
    kind: str,
    subject_id: object,
) -> None:
    use_case = RecordingUseCase(OverrideUnavailable())
    body = _body(kind=kind)
    body["subjectId"] = subject_id

    response = _client("operator", use_case).post(
        "/api/v1/overrides/full-ci",
        json=body,
    )

    assert (response.status_code, response.json()) == (422, {"code": "invalid_request"})
    assert use_case.commands == []


def test_operator_route_maps_domain_invalid_expiry_without_invoking_the_use_case() -> None:
    use_case = RecordingUseCase(OverrideUnavailable())
    body = _body()
    body["expiresAt"] = "2026-07-14T12:05:00"

    response = _client("operator", use_case).post(
        "/api/v1/overrides/full-ci",
        json=body,
    )

    assert (response.status_code, response.json()) == (
        400,
        {"ok": False, "error": "invalid_override"},
    )
    assert response.headers["cache-control"] == "no-store"
    assert use_case.commands == []


def test_operator_route_maps_conflict_and_unavailability_without_partial_success() -> None:
    conflict = _client("operator", RecordingUseCase(OverrideConflict())).post(
        "/api/v1/overrides/full-ci", json=_body()
    )
    unavailable = _client("operator", RecordingUseCase(OverrideUnavailable())).post(
        "/api/v1/overrides/full-ci", json=_body()
    )

    assert (conflict.status_code, conflict.json()) == (409, {"ok": False, "error": "conflict"})
    assert conflict.headers["cache-control"] == "no-store"
    assert unavailable.headers["cache-control"] == "no-store"
    assert (unavailable.status_code, unavailable.json()) == (
        503,
        {"ok": False, "error": "unavailable"},
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("installationId", "1"),
        ("repositoryId", 9_007_199_254_740_992),
        ("subjectId", "\u00e9" * 257),
        ("operationId", "\u00e9" * 257),
        ("reason", "\u00e9" * 257),
    ],
)
def test_operator_route_rejects_non_exact_or_unpersistable_request_facts(
    field_name: str,
    invalid_value: object,
) -> None:
    use_case = RecordingUseCase(OverrideUnavailable())
    body = _body()
    body[field_name] = invalid_value

    response = _client("operator", use_case).post(
        "/api/v1/overrides/full-ci",
        json=body,
    )

    assert (response.status_code, response.json()) == (422, {"code": "invalid_request"})
    assert use_case.commands == []


def test_operator_route_openapi_declares_one_authenticated_override_operation() -> None:
    app = create_app(
        HttpRouteDependencies(
            operator_controls=OperatorControlsRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(human_principal()),
                role_admission=StaticRoleAdmission(),
                mutation_admission=StaticMutationAdmission(),
                override_use_case=RecordingUseCase(OverrideUnavailable()),
            )
        )
    )

    operation = app.openapi()["paths"]["/api/v1/overrides/full-ci"]["post"]

    assert operation["operationId"] == "apply_validation_override"
    assert set(operation["responses"]) == {
        "202",
        "400",
        "401",
        "403",
        "409",
        "422",
        "500",
        "503",
    }
    assert operation["security"] == [{"ControlPlaneBearer": []}, {"ControlPlaneSession": []}]


def test_operator_unauthorized_response_contains_a_bearer_challenge() -> None:
    response = _client(None, RecordingUseCase(OverrideUnavailable())).post(
        "/api/v1/overrides/full-ci", json=_body()
    )

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["www-authenticate"] == "Bearer"


def test_operator_forbidden_response_is_not_cacheable_and_has_no_effect() -> None:
    use_case = RecordingUseCase(OverrideUnavailable())
    response = _client("operator", use_case, mutation_allowed=False).post(
        "/api/v1/overrides/full-ci", json=_body()
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert use_case.commands == []


def _client(
    actor: str | None, use_case: RecordingUseCase, *, mutation_allowed: bool = True
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                operator_controls=OperatorControlsRouteDependencies(
                    authenticator=StaticControlPlaneAuthenticator(
                        InvalidCredential() if actor is None else human_principal()
                    ),
                    role_admission=StaticRoleAdmission(),
                    mutation_admission=StaticMutationAdmission(mutation_allowed),
                    override_use_case=use_case,
                )
            )
        )
    )


def _body(*, kind: str = "force_full_ci") -> dict[str, object]:
    body: dict[str, object] = {
        "schemaVersion": "operator-override/v1",
        "kind": kind,
        "installationId": 1,
        "repositoryId": 2,
        "operationId": "operation-1",
        "reason": "investigate failure",
    }
    if kind == "force_full_ci":
        body["subjectId"] = "run-1"
        body["expiresAt"] = (NOW + timedelta(minutes=5)).isoformat()
    elif kind == "enable_omission":
        body["overrideId"] = "override_" + "a" * 32
    return body


def _command(
    *,
    actor: str,
    kind: Literal["force_full_ci", "disable_omission", "enable_omission"] = "force_full_ci",
) -> OverrideCommand:
    return OverrideCommand(
        kind=kind,
        scope=RepositoryScope(1, 2),
        subject_id=(
            "run-1"
            if kind == "force_full_ci"
            else "override_" + "a" * 32
            if kind == "enable_omission"
            else None
        ),
        operation_id="operation-1",
        actor=actor,
        reason="investigate failure",
        expires_at=(NOW + timedelta(minutes=5) if kind == "force_full_ci" else None),
    )
