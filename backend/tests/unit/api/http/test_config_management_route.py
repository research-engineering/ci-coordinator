from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
from types import SimpleNamespace
from typing import cast

from config_epoch_support import CONFIG_SOURCE, admitted_config_epoch
from control_plane_http_support import (
    ACTOR,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import Request
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    ConfigManagementRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.routers.config_management import (
    MAX_CONFIG_COMMAND_BODY_BYTES,
    MAX_CONFIG_REGISTRATION_BODY_BYTES,
)
from ci_coordinator.app import (
    ActivateConfigEpoch,
    ConfigActivationOutcome,
    ConfigRegistrationOutcome,
    RegisterConfigEpoch,
    RollbackConfigEpoch,
)
from ci_coordinator.app.config_admission import (
    ConfigAdmissionAccepted,
    ConfigAdmissionResult,
    ConfigAdmissionUnavailable,
)
from ci_coordinator.app.config_queries import (
    ConfigQueryUnavailable,
    ConfigSourceAvailable,
    ConfigSourceResult,
    ConfigStatusAvailable,
    ConfigStatusResult,
)
from ci_coordinator.config_control import (
    PolicyDiagnostic,
    PolicySourceFormat,
    RepositoryScope,
    ValidatedEpochDraft,
)
from ci_coordinator.config_control.limits import MAX_POLICY_SOURCE_BYTES
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ConfigEpochPage,
    ConfigEpochStatus,
    ConfigEpochSummary,
)
from ci_coordinator.control_plane_identity import (
    ControlPlanePrincipal,
    ControlPlaneRole,
    RoleAdmission,
)
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable


class _Authenticator:
    def __init__(self, actor: str | None) -> None:
        self.actor = actor
        self.calls = 0

    async def authenticate(self, request: Request) -> ControlPlanePrincipal | InvalidCredential:
        del request
        self.calls += 1
        return InvalidCredential() if self.actor is None else human_principal()


class _RecordingRoleAdmission:
    def __init__(self) -> None:
        self.required_roles: list[frozenset[ControlPlaneRole]] = []

    def admit(
        self,
        principal: ControlPlanePrincipal,
        required_roles: frozenset[ControlPlaneRole],
    ) -> RoleAdmission:
        self.required_roles.append(required_roles)
        return StaticRoleAdmission().admit(principal, required_roles)


class _UseCase:
    def __init__(
        self,
        registration: ConfigRegistrationOutcome,
        activation: ConfigActivationOutcome,
        rollback: ConfigActivationOutcome | None = None,
    ) -> None:
        self.registration = registration
        self.activation = activation
        self.rollback_outcome = activation if rollback is None else rollback
        self.register_commands: list[RegisterConfigEpoch] = []
        self.activate_commands: list[ActivateConfigEpoch] = []
        self.rollback_commands: list[RollbackConfigEpoch] = []

    async def register(self, command: RegisterConfigEpoch) -> ConfigRegistrationOutcome:
        self.register_commands.append(command)
        return self.registration

    async def activate(self, command: ActivateConfigEpoch) -> ConfigActivationOutcome:
        self.activate_commands.append(command)
        return self.activation

    async def rollback(self, command: RollbackConfigEpoch) -> ConfigActivationOutcome:
        self.rollback_commands.append(command)
        return self.rollback_outcome


class _FailingRollbackUseCase(_UseCase):
    async def rollback(self, command: RollbackConfigEpoch) -> ConfigActivationOutcome:
        raise RuntimeError("sensitive rollback failure: " + command.operation_id)


def test_membership_failure_preserves_the_config_error_contract() -> None:
    class UnavailableAccess(_FailingRollbackUseCase):
        async def rollback(self, command: RollbackConfigEpoch) -> ConfigActivationOutcome:
            raise RepositoryAccessUnavailable("private provider diagnostic")

    client = _client(
        "operator",
        UnavailableAccess(
            ConfigRegistrationOutcome("unavailable"),
            ConfigActivationOutcome("unavailable"),
        ),
    )
    response = client.post("/api/v1/config/rollbacks", json=_rollback_body())
    assert (response.status_code, response.json()) == (
        503,
        {"ok": False, "error": "unavailable", "diagnostics": []},
    )
    assert response.headers["cache-control"] == "no-store"


class _Admission:
    def __init__(self, result: ConfigAdmissionResult | None = None) -> None:
        self.result = result or ConfigAdmissionUnavailable()
        self.calls: list[tuple[str, bytes, PolicySourceFormat]] = []

    async def admit(
        self,
        *,
        actor: str,
        source: bytes,
        source_format: PolicySourceFormat,
    ) -> ConfigAdmissionResult:
        self.calls.append((actor, source, source_format))
        return self.result


class _Queries:
    def __init__(
        self,
        *,
        status_result: ConfigStatusResult | None = None,
        source_result: ConfigSourceResult | None = None,
    ) -> None:
        self.status_result = status_result or ConfigQueryUnavailable()
        self.source_result = source_result or ConfigQueryUnavailable()
        self.status_calls: list[tuple[str, RepositoryScope, str | None, int]] = []
        self.source_calls: list[tuple[str, RepositoryScope, str]] = []

    async def status(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigStatusResult:
        self.status_calls.append((actor, scope, after_epoch_id, limit))
        return self.status_result

    async def source(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ConfigSourceResult:
        self.source_calls.append((actor, scope, epoch_id))
        return self.source_result


def test_config_mutations_are_authenticated_stable_control_plane_routes() -> None:
    use_case = _UseCase(
        ConfigRegistrationOutcome("created", epoch_id="a" * 64),
        ConfigActivationOutcome("applied", epoch_id="a" * 64, revision=1),
        ConfigActivationOutcome("applied", epoch_id="b" * 64, revision=2),
    )
    client = _client("operator", use_case)

    registered = client.post(
        "/api/v1/config/epochs",
        json={
            "schemaVersion": "ci-config-epoch-registration/v1",
            "sourceFormat": "json",
            "source": "{}",
            "operationId": "register-1",
        },
    )
    activated = client.post(
        "/api/v1/config/activations",
        json={
            "schemaVersion": "ci-config-epoch-activation/v1",
            "installationId": 1,
            "repositoryId": 2,
            "targetEpochId": "a" * 64,
            "proposalManifestId": "proposal:" + "b" * 32,
            "expectedRevision": None,
            "operationId": "activate-1",
        },
    )
    rolled_back = client.post("/api/v1/config/rollbacks", json=_rollback_body())

    assert (registered.status_code, registered.json()) == (
        201,
        {
            "schemaVersion": "ci-config-epoch-registration-result/v1",
            "ok": True,
            "epochId": "a" * 64,
            "duplicate": False,
        },
    )
    assert (activated.status_code, activated.json()) == (
        200,
        {
            "schemaVersion": "ci-config-epoch-activation-result/v1",
            "ok": True,
            "epochId": "a" * 64,
            "duplicate": False,
            "revision": 1,
        },
    )
    assert (rolled_back.status_code, rolled_back.json()) == (
        200,
        {
            "schemaVersion": "ci-config-epoch-activation-result/v1",
            "ok": True,
            "epochId": "b" * 64,
            "duplicate": False,
            "revision": 2,
        },
    )
    assert {
        registered.headers["cache-control"],
        activated.headers["cache-control"],
        rolled_back.headers["cache-control"],
    } == {"no-store"}
    assert use_case.register_commands[0].source == b"{}"
    assert use_case.register_commands[0].operation_id == "register-1"
    assert use_case.activate_commands[0].actor == ACTOR
    assert use_case.rollback_commands == [
        RollbackConfigEpoch(
            actor=ACTOR,
            scope=use_case.activate_commands[0].scope,
            target_epoch_id="b" * 64,
            expected_revision=1,
            operation_id="rollback-1",
            reason="restore conservative validation",
        )
    ]


def test_rollback_preserves_duplicate_and_conflict_outcomes() -> None:
    duplicate = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
        ConfigActivationOutcome("duplicate", epoch_id="b" * 64, revision=2),
    )
    conflict = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
        ConfigActivationOutcome("revision_conflict"),
    )
    operation_conflict = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
        ConfigActivationOutcome("operation_conflict"),
    )

    duplicate_response = _client("operator", duplicate).post(
        "/api/v1/config/rollbacks", json=_rollback_body()
    )
    conflict_response = _client("operator", conflict).post(
        "/api/v1/config/rollbacks", json=_rollback_body()
    )
    operation_conflict_response = _client("operator", operation_conflict).post(
        "/api/v1/config/rollbacks", json=_rollback_body()
    )

    assert (duplicate_response.status_code, duplicate_response.json()) == (
        200,
        {
            "schemaVersion": "ci-config-epoch-activation-result/v1",
            "ok": True,
            "epochId": "b" * 64,
            "duplicate": True,
            "revision": 2,
        },
    )
    assert (conflict_response.status_code, conflict_response.json()) == (
        409,
        {"ok": False, "error": "revision_conflict", "diagnostics": []},
    )
    assert (operation_conflict_response.status_code, operation_conflict_response.json()) == (
        409,
        {"ok": False, "error": "conflict", "diagnostics": []},
    )


def test_activation_rejects_non_exact_or_unpersistable_request_facts() -> None:
    use_case = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
    )
    client = _client("operator", use_case)
    valid = {
        "schemaVersion": "ci-config-epoch-activation/v1",
        "installationId": 1,
        "repositoryId": 2,
        "targetEpochId": "a" * 64,
        "proposalManifestId": "proposal:" + "b" * 32,
        "expectedRevision": 1,
        "operationId": "activate-1",
    }

    responses = tuple(
        client.post("/api/v1/config/activations", json={**valid, field: value})
        for field, value in (
            ("installationId", "1"),
            ("repositoryId", 9_007_199_254_740_992),
            ("expectedRevision", 9_007_199_254_740_992),
            ("operationId", "\u00e9" * 129),
            ("operationId", "activate\u0000one"),
        )
    )

    assert [(response.status_code, response.json()) for response in responses] == [
        (422, {"code": "invalid_request"}),
    ] * 5
    assert {response.headers["cache-control"] for response in responses} == {"no-store"}
    assert use_case.activate_commands == []


def test_rollback_projects_coverage_rejections_without_dispatch_success() -> None:
    reducing = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
        ConfigActivationOutcome("coverage_reducing"),
    )
    unproven = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
        ConfigActivationOutcome("coverage_unproven"),
    )

    reducing_response = _client("operator", reducing).post(
        "/api/v1/config/rollbacks", json=_rollback_body()
    )
    unproven_response = _client("operator", unproven).post(
        "/api/v1/config/rollbacks", json=_rollback_body()
    )

    assert (reducing_response.status_code, reducing_response.json()) == (
        409,
        {"ok": False, "error": "coverage_reducing", "diagnostics": []},
    )
    assert (unproven_response.status_code, unproven_response.json()) == (
        409,
        {"ok": False, "error": "coverage_unproven", "diagnostics": []},
    )


def test_rollback_rejects_non_cas_or_unpersistable_request_facts() -> None:
    use_case = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
    )
    client = _client("operator", use_case)
    missing = _rollback_body()
    del missing["expectedRevision"]
    null = _rollback_body()
    null["expectedRevision"] = None
    unsafe_revision = _rollback_body()
    unsafe_revision["expectedRevision"] = 9_007_199_254_740_992
    oversized_operation_id = _rollback_body()
    oversized_operation_id["operationId"] = "\u00e9" * 256
    missing_reason = _rollback_body()
    del missing_reason["reason"]
    empty_reason = _rollback_body()
    empty_reason["reason"] = ""
    oversized_reason = _rollback_body()
    oversized_reason["reason"] = "\u00e9" * 257

    responses = (
        client.post("/api/v1/config/rollbacks", json=missing),
        client.post("/api/v1/config/rollbacks", json=null),
        client.post("/api/v1/config/rollbacks", json=unsafe_revision),
        client.post("/api/v1/config/rollbacks", json=oversized_operation_id),
        client.post("/api/v1/config/rollbacks", json=missing_reason),
        client.post("/api/v1/config/rollbacks", json=empty_reason),
        client.post("/api/v1/config/rollbacks", json=oversized_reason),
    )

    assert [(response.status_code, response.json()) for response in responses] == [
        (422, {"code": "invalid_request"}),
        (422, {"code": "invalid_request"}),
        (422, {"code": "invalid_request"}),
        (422, {"code": "invalid_request"}),
        (422, {"code": "invalid_request"}),
        (422, {"code": "invalid_request"}),
        (422, {"code": "invalid_request"}),
    ]
    assert {response.headers["cache-control"] for response in responses} == {"no-store"}
    assert use_case.rollback_commands == []


def test_invalid_config_diagnostics_are_redacted_to_stable_identifiers() -> None:
    diagnostic = PolicyDiagnostic(
        code="structure.invalid",
        phase="structure",
        rule_id="schema:required",
        instance_pointer="/repository",
        parameters={"secret": "must-not-escape"},
    )
    client = _client(
        "operator",
        _UseCase(
            ConfigRegistrationOutcome("invalid", diagnostics=(diagnostic,)),
            ConfigActivationOutcome("target_unavailable"),
        ),
    )

    response = client.post(
        "/api/v1/config/epochs",
        json={
            "schemaVersion": "ci-config-epoch-registration/v1",
            "sourceFormat": "yaml-1.2",
            "source": "invalid",
            "operationId": "register-invalid",
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "ok": False,
        "error": "invalid_config",
        "diagnostics": [
            {
                "code": "structure.invalid",
                "phase": "structure",
                "ruleId": "schema:required",
                "instancePointer": "/repository",
            }
        ],
    }
    assert "must-not-escape" not in response.text


def test_unauthenticated_config_requests_do_not_reach_the_use_case() -> None:
    use_case = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
    )
    client = _client(None, use_case)

    response = client.post("/api/v1/config/rollbacks", json=_rollback_body())

    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"
    assert response.headers["www-authenticate"] == "Bearer"
    assert use_case.register_commands == []
    assert use_case.rollback_commands == []


def test_config_registration_transport_covers_the_domain_byte_contract() -> None:
    use_case = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
    )
    source = "\x00" * MAX_POLICY_SOURCE_BYTES
    operation_id = "\x01" * 256

    response = _client("operator", use_case).post(
        "/api/v1/config/epochs",
        json={
            "schemaVersion": "ci-config-epoch-registration/v1",
            "sourceFormat": "json",
            "source": source,
            "operationId": operation_id,
        },
    )

    assert response.status_code == 503
    assert len(response.request.content) <= MAX_CONFIG_REGISTRATION_BODY_BYTES
    assert use_case.register_commands[0].source == source.encode()
    assert use_case.register_commands[0].operation_id == operation_id


def test_config_registration_rejects_source_beyond_the_domain_byte_contract() -> None:
    use_case = _UseCase(
        ConfigRegistrationOutcome("created", epoch_id="a" * 64),
        ConfigActivationOutcome("unavailable"),
    )

    response = _client("operator", use_case).post(
        "/api/v1/config/epochs",
        json={
            "schemaVersion": "ci-config-epoch-registration/v1",
            "sourceFormat": "json",
            "source": "x" * (MAX_POLICY_SOURCE_BYTES + 1),
            "operationId": "register-too-large",
        },
    )

    assert (response.status_code, response.json()) == (422, {"code": "invalid_request"})
    assert response.headers["cache-control"] == "no-store"
    assert use_case.register_commands == []


def test_pure_validation_projects_the_exact_admitted_identity_without_dispatch() -> None:
    draft = _admitted_draft()
    admission = _Admission(ConfigAdmissionAccepted(draft))
    use_case = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
    )

    response = _client("operator", use_case, admission=admission).post(
        "/api/v1/config/validations",
        json={
            "schemaVersion": "ci-config-epoch-validation/v1",
            "sourceFormat": "json",
            "source": draft.source_bytes.decode("utf-8"),
        },
    )

    assert (response.status_code, response.json()) == (
        200,
        {
            "schemaVersion": "ci-config-epoch-validation-result/v1",
            "ok": True,
            "installationId": 1,
            "repositoryId": 2,
            "epochId": draft.epoch_id,
            "sourceHash": draft.source_hash,
            "documentHash": draft.document_hash,
            "epochHash": draft.epoch_hash,
            "documentSchemaId": draft.document_schema_id,
            "documentProfileId": draft.document_profile_id,
            "semanticProfileId": draft.semantic_profile_id,
            "compiledSchemaId": draft.compiled_schema_id,
        },
    )
    assert admission.calls == [(ACTOR, draft.source_bytes, "json")]
    assert use_case.register_commands == []
    assert response.headers["cache-control"] == "no-store"


def test_status_is_scope_bound_and_projects_one_bounded_page() -> None:
    draft = _admitted_draft()
    scope = draft.scope
    status_value = ConfigEpochStatus(
        scope=scope,
        active=ActiveConfigEpoch(scope, draft.epoch_id, 3),
        epochs=ConfigEpochPage(
            items=(
                ConfigEpochSummary(
                    epoch_id=draft.epoch_id,
                    source_format=draft.source_format,
                    source_hash=draft.source_hash,
                    document_hash=draft.document_hash,
                    epoch_hash=draft.epoch_hash,
                    source_byte_count=len(draft.source_bytes),
                ),
            ),
            next_cursor=draft.epoch_id,
        ),
    )
    queries = _Queries(status_result=ConfigStatusAvailable(status_value))

    response = _client(
        "operator",
        _UseCase(
            ConfigRegistrationOutcome("unavailable"),
            ConfigActivationOutcome("unavailable"),
        ),
        queries=queries,
    ).get(
        "/api/v1/config/repositories/1/2/status",
        params={"afterEpochId": "0" * 64, "limit": 1},
    )

    assert (response.status_code, response.json()) == (
        200,
        {
            "schemaVersion": "ci-config-epoch-status/v1",
            "installationId": 1,
            "repositoryId": 2,
            "active": {"epochId": draft.epoch_id, "revision": 3},
            "epochs": [
                {
                    "epochId": draft.epoch_id,
                    "sourceFormat": "json",
                    "sourceHash": draft.source_hash,
                    "documentHash": draft.document_hash,
                    "epochHash": draft.epoch_hash,
                    "sourceByteCount": len(draft.source_bytes),
                }
            ],
            "nextCursor": draft.epoch_id,
        },
    )
    assert queries.status_calls == [(ACTOR, scope, "0" * 64, 1)]
    assert response.headers["cache-control"] == "no-store"


def test_source_export_emits_exact_retained_bytes_and_digest_headers() -> None:
    draft = _admitted_draft()
    queries = _Queries(source_result=ConfigSourceAvailable(draft))

    response = _client(
        "operator",
        _UseCase(
            ConfigRegistrationOutcome("unavailable"),
            ConfigActivationOutcome("unavailable"),
        ),
        queries=queries,
    ).get(f"/api/v1/config/repositories/1/2/epochs/{draft.epoch_id}/source")

    digest = sha256(draft.source_bytes).digest()
    assert digest.hex() != draft.source_hash
    assert response.status_code == 200
    assert response.content == draft.source_bytes
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-digest"] == f"sha-256=:{b64encode(digest).decode('ascii')}:"
    assert response.headers["etag"] == f'"{draft.source_hash}"'
    assert response.headers["x-ci-config-epoch-id"] == draft.epoch_id
    assert response.headers["cache-control"] == "no-store"
    assert queries.source_calls == [(ACTOR, draft.scope, draft.epoch_id)]


def test_config_lifecycle_routes_enforce_the_profile_role_partition() -> None:
    draft = _admitted_draft()
    role_admission = _RecordingRoleAdmission()
    client = _client(
        "operator",
        _UseCase(
            ConfigRegistrationOutcome("created", epoch_id=draft.epoch_id),
            ConfigActivationOutcome("applied", epoch_id=draft.epoch_id, revision=1),
        ),
        admission=_Admission(ConfigAdmissionAccepted(draft)),
        queries=_Queries(
            status_result=ConfigStatusAvailable(
                ConfigEpochStatus(
                    scope=draft.scope,
                    active=None,
                    epochs=ConfigEpochPage(items=(), next_cursor=None),
                )
            ),
            source_result=ConfigSourceAvailable(draft),
        ),
        role_admission=role_admission,
    )

    responses = (
        client.post(
            "/api/v1/config/validations",
            json={
                "schemaVersion": "ci-config-epoch-validation/v1",
                "sourceFormat": "json",
                "source": draft.source_bytes.decode("utf-8"),
            },
        ),
        client.post(
            "/api/v1/config/epochs",
            json={
                "schemaVersion": "ci-config-epoch-registration/v1",
                "sourceFormat": "json",
                "source": draft.source_bytes.decode("utf-8"),
                "operationId": "register-role-witness",
            },
        ),
        client.get("/api/v1/config/repositories/1/2/status"),
        client.get(f"/api/v1/config/repositories/1/2/epochs/{draft.epoch_id}/source"),
        client.post(
            "/api/v1/config/activations",
            json={
                "schemaVersion": "ci-config-epoch-activation/v1",
                "installationId": 1,
                "repositoryId": 2,
                "targetEpochId": draft.epoch_id,
                "proposalManifestId": "proposal:" + "b" * 32,
                "expectedRevision": None,
                "operationId": "activate-role-witness",
            },
        ),
        client.post("/api/v1/config/rollbacks", json=_rollback_body()),
    )

    assert tuple(response.status_code for response in responses) == (200, 201, 200, 200, 200, 200)
    assert role_admission.required_roles == [
        frozenset({"configure"}),
        frozenset({"configure"}),
        frozenset({"configure"}),
        frozenset({"configure"}),
        frozenset({"activate"}),
        frozenset({"activate"}),
    ]


def test_config_openapi_declares_security_success_and_error_algebras() -> None:
    app = create_app(
        HttpRouteDependencies(
            config_management=ConfigManagementRouteDependencies(
                authenticator=_Authenticator("operator"),
                role_admission=StaticRoleAdmission(),
                mutation_admission=StaticMutationAdmission(),
                admission=_Admission(),
                queries=_Queries(),
                use_case=_UseCase(
                    ConfigRegistrationOutcome("unavailable"),
                    ConfigActivationOutcome("unavailable"),
                ),
            )
        )
    )
    schema = app.openapi()

    routes = (
        (
            "/api/v1/config/validations",
            "post",
            {"200", "400", "401", "403", "413", "422", "500", "503"},
        ),
        (
            "/api/v1/config/epochs",
            "post",
            {"200", "201", "400", "401", "403", "409", "413", "422", "500", "503"},
        ),
        (
            "/api/v1/config/activations",
            "post",
            {"200", "400", "401", "403", "404", "409", "413", "422", "500", "503"},
        ),
        (
            "/api/v1/config/rollbacks",
            "post",
            {"200", "400", "401", "403", "404", "409", "413", "422", "500", "503"},
        ),
        (
            "/api/v1/config/repositories/{installation_id}/{repository_id}/status",
            "get",
            {"200", "401", "403", "422", "500", "503"},
        ),
        (
            "/api/v1/config/repositories/{installation_id}/{repository_id}/epochs/"
            "{epoch_id}/source",
            "get",
            {"200", "401", "403", "404", "422", "500", "503"},
        ),
    )

    for path, method, expected_responses in routes:
        operation = schema["paths"][path][method]
        assert operation["security"] == [
            {"ControlPlaneBearer": []},
            {"ControlPlaneSession": []},
        ]
        assert expected_responses.issubset(operation["responses"])


def test_rollback_body_is_bounded_before_authentication_or_dispatch() -> None:
    use_case = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
    )

    authenticator = _Authenticator("operator")
    response = _client("operator", use_case, authenticator=authenticator).post(
        "/api/v1/config/rollbacks",
        content=b"x" * (MAX_CONFIG_COMMAND_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )

    assert (response.status_code, response.json()) == (
        413,
        {"ok": False, "error": "invalid_config", "diagnostics": []},
    )
    assert authenticator.calls == 0
    assert use_case.rollback_commands == []


def test_unexpected_rollback_failure_is_redacted() -> None:
    use_case = _FailingRollbackUseCase(
        ConfigRegistrationOutcome("unavailable"),
        ConfigActivationOutcome("unavailable"),
    )

    response = _client("operator", use_case).post("/api/v1/config/rollbacks", json=_rollback_body())

    assert (response.status_code, response.json()) == (
        500,
        {"code": "internal_error"},
    )
    assert "rollback-1" not in response.text


def test_unknown_registration_outcome_is_not_misclassified_as_unavailable() -> None:
    use_case = _UseCase(
        cast(ConfigRegistrationOutcome, SimpleNamespace(state="future_state")),
        ConfigActivationOutcome("unavailable"),
    )

    response = _client("operator", use_case).post(
        "/api/v1/config/epochs",
        json={
            "schemaVersion": "ci-config-epoch-registration/v1",
            "sourceFormat": "json",
            "source": "{}",
            "operationId": "register-unknown",
        },
    )

    assert (response.status_code, response.json()) == (500, {"code": "internal_error"})
    assert response.headers["cache-control"] == "no-store"


def test_unknown_activation_outcome_is_not_misclassified_as_unavailable() -> None:
    use_case = _UseCase(
        ConfigRegistrationOutcome("unavailable"),
        cast(ConfigActivationOutcome, SimpleNamespace(state="future_state")),
    )

    response = _client("operator", use_case).post(
        "/api/v1/config/activations",
        json={
            "schemaVersion": "ci-config-epoch-activation/v1",
            "installationId": 1,
            "repositoryId": 2,
            "targetEpochId": "a" * 64,
            "proposalManifestId": "proposal:" + "b" * 32,
            "expectedRevision": None,
            "operationId": "activate-1",
        },
    )

    assert (response.status_code, response.json()) == (500, {"code": "internal_error"})
    assert response.headers["cache-control"] == "no-store"


def _client(
    actor: str | None,
    use_case: _UseCase,
    *,
    authenticator: _Authenticator | None = None,
    admission: _Admission | None = None,
    queries: _Queries | None = None,
    role_admission: StaticRoleAdmission | _RecordingRoleAdmission | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                config_management=ConfigManagementRouteDependencies(
                    authenticator=_Authenticator(actor) if authenticator is None else authenticator,
                    role_admission=(
                        StaticRoleAdmission() if role_admission is None else role_admission
                    ),
                    mutation_admission=StaticMutationAdmission(),
                    admission=_Admission() if admission is None else admission,
                    queries=_Queries() if queries is None else queries,
                    use_case=use_case,
                )
            )
        ),
        raise_server_exceptions=False,
    )


def _rollback_body() -> dict[str, object]:
    return {
        "schemaVersion": "ci-config-epoch-rollback/v1",
        "installationId": 1,
        "repositoryId": 2,
        "targetEpochId": "b" * 64,
        "expectedRevision": 1,
        "operationId": "rollback-1",
        "reason": "restore conservative validation",
    }


def _admitted_draft() -> ValidatedEpochDraft:
    admitted = admitted_config_epoch()
    assert admitted.source_bytes == CONFIG_SOURCE
    return admitted
