from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from unittest.mock import Mock, create_autospec

import pytest
from control_plane_http_support import (
    ACTOR,
    NOW,
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    HttpRouteDependencies,
    InvalidCredential,
    ObservabilityRouteDependencies,
    ProductionCutoverRouteDependencies,
)
from ci_coordinator.api.http.routers.production_cutover import (
    PRODUCTION_ACTIVATE_PATH,
    PRODUCTION_BEGIN_PATH,
    PRODUCTION_BODY_LIMITS,
    PRODUCTION_EVIDENCE_PATH,
    PRODUCTION_STAGE_PATH,
    PRODUCTION_STATE_PATH,
)
from ci_coordinator.app.production_cutover import (
    ProductionAdministrationError,
    ProductionAdministrationResult,
    ProductionCutoverUseCase,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import (
    CONTROL_PLANE_ROLES,
    ControlPlanePrincipal,
    ControlPlaneRole,
)
from ci_coordinator.kernel import hash_object
from ci_coordinator.observability import ReadinessStatus, RuntimeMetrics
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverApplied,
    ProductionCutoverCommand,
    ProductionCutoverRejected,
    production_stage_input_digest,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState

_AUTHORITY = "production_admission_" + "a" * 32
_SCOPE = RepositoryScope(11, 22)
_STATE = ProductionScopeState(_SCOPE, 1, staged_authority_id=_AUTHORITY)
_OPERATIONS: tuple[tuple[str, str, ControlPlaneRole], ...] = (
    ("stage", PRODUCTION_STAGE_PATH, "configure"),
    ("begin", PRODUCTION_BEGIN_PATH, "override"),
    ("activate", PRODUCTION_ACTIVATE_PATH, "activate"),
    ("inspect", PRODUCTION_STATE_PATH, "read"),
    ("evidence", PRODUCTION_EVIDENCE_PATH, "read"),
)


@pytest.fixture
def use_case() -> Mock:
    result: Mock = create_autospec(ProductionCutoverUseCase, instance=True, spec_set=True)
    for method in ("stage", "begin", "activate"):
        getattr(result, method).return_value = ProductionCutoverApplied(_STATE)
    result.inspect.return_value = _STATE
    result.evidence.return_value = b'{"retained":"exact"}\n'
    return result


def _client(
    use_case: Mock,
    *,
    principal: ControlPlanePrincipal
    | InvalidCredential
    | AuthenticationDependencyUnavailable
    | None = None,
    mutation: bool = True,
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                production_cutover=ProductionCutoverRouteDependencies(
                    StaticControlPlaneAuthenticator(
                        human_principal() if principal is None else principal
                    ),
                    StaticRoleAdmission(),
                    StaticMutationAdmission(mutation),
                    use_case,
                )
            ),
            include_operator_ui=False,
        ),
        raise_server_exceptions=False,
    )


def _body(method: str) -> dict[str, object]:
    result: dict[str, object] = {
        "schemaVersion": "ci-coordinator.production-cutover-request/v1",
        "installationId": 11,
        "repositoryId": 22,
        "operationId": "cutover-1",
        "authorityId": _AUTHORITY,
        "expectedRevision": 0,
        "reason": "deploy qualified generation",
    }
    if method == "stage":
        result.update(
            envelope='{"signed":true}\n',
            evidence='{"bundle":true}\n',
            providerPaths=[".github/workflows/ci.yml"],
        )
    if method == "activate":
        result["drainEnvelope"] = '{"drained":true}\n'
    return result


def _path(template: str) -> str:
    return template.format(installation_id=11, repository_id=22, authority_id=_AUTHORITY)


@pytest.mark.parametrize(("method", "path", "required"), _OPERATIONS)
@pytest.mark.parametrize("role", sorted(CONTROL_PLANE_ROLES))
def test_each_operation_requires_its_exact_role_before_use_case(
    use_case: Mock, method: str, path: str, required: ControlPlaneRole, role: ControlPlaneRole
) -> None:
    with _client(use_case, principal=human_principal(roles=frozenset({role}))) as client:
        response = client.request(
            "GET" if method in {"inspect", "evidence"} else "POST",
            _path(path),
            json=None if method in {"inspect", "evidence"} else _body(method),
        )
    assert response.status_code == (200 if role == required else 403)
    assert response.headers["cache-control"] == "no-store"
    if role != required:
        assert use_case.mock_calls == []
    else:
        assert getattr(use_case, method).await_count == 1


@pytest.mark.parametrize(("method", "path", "required"), _OPERATIONS)
@pytest.mark.parametrize(
    ("principal", "status"),
    (
        (InvalidCredential(), 401),
        (replace(human_principal(), expires_at=NOW), 401),
        (AuthenticationDependencyUnavailable(), 503),
    ),
)
def test_missing_expired_or_unavailable_identity_has_no_effect(
    use_case: Mock,
    method: str,
    path: str,
    required: ControlPlaneRole,
    principal: ControlPlanePrincipal | InvalidCredential | AuthenticationDependencyUnavailable,
    status: int,
) -> None:
    del required
    with _client(use_case, principal=principal) as client:
        response = client.request(
            "GET" if method in {"inspect", "evidence"} else "POST",
            _path(path),
            json=None if method in {"inspect", "evidence"} else _body(method),
        )
    assert response.status_code == status
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    assert use_case.mock_calls == []


@pytest.mark.parametrize(("method", "path"), tuple((row[0], row[1]) for row in _OPERATIONS[:3]))
def test_mutation_admission_cannot_be_bypassed_by_roles(
    use_case: Mock, method: str, path: str
) -> None:
    with _client(use_case, mutation=False) as client:
        response = client.post(path, json=_body(method))
    assert response.status_code == 403
    assert use_case.mock_calls == []


def test_routes_bind_exact_actor_scope_bytes_and_command_identity(use_case: Mock) -> None:
    with _client(use_case) as client:
        for method, path, _ in _OPERATIONS[:3]:
            response = client.post(path, json=_body(method))
            assert response.status_code == 200
            assert response.json() == {
                "schemaVersion": "ci-coordinator.production-cutover-result/v1",
                "ok": True,
                "duplicate": False,
                "state": _STATE.to_mapping(),
            }
        assert client.get(_path(PRODUCTION_EVIDENCE_PATH)).content == b'{"retained":"exact"}\n'
    for method, _, _ in _OPERATIONS[:3]:
        command = getattr(use_case, method).await_args.args[0]
        assert type(command) is ProductionCutoverCommand
        assert (command.kind, command.actor, command.scope, command.authority_id) == (
            method,
            ACTOR,
            _SCOPE,
            _AUTHORITY,
        )
    stage = use_case.stage.await_args
    assert stage.kwargs == {
        "envelope": b'{"signed":true}\n',
        "evidence": b'{"bundle":true}\n',
        "provider_paths": (".github/workflows/ci.yml",),
    }
    assert stage.args[0].input_digest == production_stage_input_digest(**stage.kwargs)
    assert use_case.begin.await_args.args[0].input_digest == hash_object({})
    assert (
        use_case.activate.await_args.args[0].input_digest
        == sha256(b'{"drained":true}\n').hexdigest()
    )
    use_case.evidence.assert_awaited_once_with(actor=ACTOR, scope=_SCOPE, authority_id=_AUTHORITY)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("installationId", True),
        ("repositoryId", "22"),
        ("expectedRevision", -1),
        ("expectedRevision", 9_007_199_254_740_992),
        ("schemaVersion", "v0"),
        ("authorityId", "production_admission:" + "a" * 32),
        ("unknown", "secret"),
        ("reason", ""),
        ("reason", "\u00e9" * 257),
        ("operationId", "\ud800"),
        ("envelope", "\ud800"),
        ("evidence", "\ud800"),
        ("providerPaths", ["\ud800"]),
        ("providerPaths", []),
    ),
)
def test_invalid_wire_shape_is_redacted_and_never_reaches_service(
    use_case: Mock, field: str, value: object
) -> None:
    with _client(use_case) as client:
        response = client.post(
            PRODUCTION_STAGE_PATH,
            content=json.dumps({**_body("stage"), field: value}),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert "secret" not in response.text
    assert use_case.mock_calls == []


@pytest.mark.parametrize("policy", PRODUCTION_BODY_LIMITS)
def test_declared_oversized_body_is_rejected_before_use_case(
    use_case: Mock, policy: BodyLimitPolicy
) -> None:
    with _client(use_case) as client:
        response = client.post(
            policy.path,
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(policy.maximum_body_bytes + 1),
            },
        )
    assert response.status_code == 413
    assert use_case.mock_calls == []


@pytest.mark.parametrize(
    ("result", "status"),
    (
        (ProductionAdministrationError("forbidden"), 403),
        (ProductionAdministrationError("invalid_evidence"), 422),
        (ProductionAdministrationError("not_found"), 404),
        (ProductionAdministrationError("unavailable"), 503),
        (ProductionCutoverRejected("revision_changed"), 409),
        (ProductionCutoverRejected("drain_incomplete"), 409),
        (ProductionCutoverRejected("capacity_exhausted"), 503),
        (ProductionCutoverApplied(_STATE, duplicate=True), 200),
    ),
)
def test_domain_outcomes_preserve_status_and_historical_replay(
    use_case: Mock, result: ProductionAdministrationResult, status: int
) -> None:
    use_case.begin.return_value = result
    with _client(use_case) as client:
        response = client.post(PRODUCTION_BEGIN_PATH, json=_body("begin"))
    assert response.status_code == status
    if isinstance(result, ProductionCutoverApplied):
        assert response.json()["duplicate"] is True
    else:
        assert response.json() == {"ok": False, "error": result.reason}


def test_unexpected_failure_is_redacted_with_correlation(use_case: Mock) -> None:
    use_case.stage.side_effect = RuntimeError("private-key-material")
    with _client(use_case) as client:
        response = client.post(PRODUCTION_STAGE_PATH, json=_body("stage"))
    assert response.status_code == 500
    assert "private-key-material" not in response.text
    assert response.headers["x-correlation-id"]


def test_openapi_describes_all_production_operations_but_is_not_public(use_case: Mock) -> None:
    with _client(use_case) as client:
        assert isinstance(client.app, FastAPI)
        schema = client.app.openapi()
        for method, path, _ in _OPERATIONS:
            operation = schema["paths"][path][
                "get" if method in {"inspect", "evidence"} else "post"
            ]
            assert operation["security"]
            assert {"200", "401", "403", "422", "503"} <= operation["responses"].keys()
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404


def test_unconfigured_http_dependencies_do_not_expose_cutover_routes() -> None:
    async def ready() -> ReadinessStatus:
        return ReadinessStatus(True, ())

    dependencies = HttpRouteDependencies(
        observability=ObservabilityRouteDependencies(readiness=ready, metrics=RuntimeMetrics())
    )
    with TestClient(create_app(dependencies, include_operator_ui=False)) as client:
        assert client.post(PRODUCTION_BEGIN_PATH, json=_body("begin")).status_code == 404
