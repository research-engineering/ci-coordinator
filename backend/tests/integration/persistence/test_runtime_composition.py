from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from production_admission_support import (
    ARTIFACT_DIGEST,
    PRODUCTION_KEY_ID,
    RELEASE_IDENTITY,
    ROLLOUT_PROFILE_ID,
    SOURCE_COMMIT,
    make_production_admission_fixture,
)
from sqlalchemy import func, select

from ci_coordinator.api.http.control_plane_authentication import (
    BreakGlassBearerAuthenticator,
    ControlPlaneRequestAuthenticator,
    ControlPlaneRoleAuthorizer,
)
from ci_coordinator.api.http.control_plane_security import ControlPlaneMutationGuard
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import (
    BrowserIdentityService,
    ControlPlaneIdentityCrypto,
    IdentityRejected,
    KeycloakWorkloadPrincipal,
)
from ci_coordinator.identity_admission import ActionsOidcJwkSet
from ci_coordinator.integrations.github.provider_inventory import GitHubProviderInventory
from ci_coordinator.integrations.github.repository_membership import GitHubRepositoryAccess
from ci_coordinator.integrations.keycloak import KeycloakIntegration
from ci_coordinator.kernel import SystemClock
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    production_admission_authorities,
    production_admission_scope_bindings,
    production_scope_states,
    production_staged_grants,
)
from ci_coordinator.production_admission import ProductionScopeSubject
from ci_coordinator.provider_inventory import (
    InstallationSummary,
    ProviderInstallationPage,
    ProviderRepositoryPage,
    RepositorySummary,
)
from ci_coordinator.runtime import composition as runtime_composition
from ci_coordinator.runtime.application import (
    RuntimeApplication,
    RuntimeCompositionRejection,
    compose_runtime_application,
)
from ci_coordinator.runtime.control_plane_composition import ControlPlaneRuntimeDependencies
from ci_coordinator.runtime_settings import (
    BuildIdentity,
    EnforcingRuntimeSettings,
    NonEnforcingRuntimeSettings,
    admit_runtime_settings,
)
from ci_coordinator.runtime_settings.control_plane_identity import (
    CONTROL_PLANE_API_CLIENT_ID,
    CONTROL_PLANE_BROWSER_CLIENT_ID,
)

pytestmark = pytest.mark.persistence

EXAMPLE_KEYCLOAK_ISSUER = "https://auth.example.test/realms/coordinator"
GITHUB_REVIEWER_CLIENT_ID = "Iv1SyntheticClient01"

_WORKFLOW_REF = "example/ci/.github/workflows/dynamic-ci.yml@refs/heads/master"
_JOB_WORKFLOW_REF = "example/ci/.github/workflows/reusable-ci.yml@refs/heads/master"
_MACHINE_TOKEN = "a" * 32


class _AvailableJwksProvider:
    def __init__(self, key_set: ActionsOidcJwkSet) -> None:
        self._key_set = key_set

    async def get_key_set(self, _: str) -> ActionsOidcJwkSet:
        return self._key_set

    async def probe(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class _MachineIdentity:
    async def authenticate(
        self,
        token: str | None,
    ) -> KeycloakWorkloadPrincipal | IdentityRejected:
        if token != _MACHINE_TOKEN:
            return IdentityRejected("invalid_machine_token")
        now = datetime.now(UTC)
        return KeycloakWorkloadPrincipal(
            issuer=EXAMPLE_KEYCLOAK_ISSUER,
            authorized_party="runtime-integration-test",
            subject="service-account-runtime-integration-test",
            roles=frozenset({"activate", "configure", "read"}),
            issued_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=5),
            authority_profile_digest="f" * 64,
        )


class _PreparedIdentityProvider:
    async def prepare(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


@pytest.mark.parametrize("inventory_only,app_scope", [(False, False), (True, False), (True, True)])
def test_complete_runtime_issues_durable_fallback_and_enforces_config_scope(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    inventory_only: bool,
    app_scope: bool,
) -> None:
    oidc_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = _AvailableJwksProvider(_key_set(oidc_private_key))

    def available_jwks_provider(_: object, **__: object) -> _AvailableJwksProvider:
        return jwks

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "GitHubActionsJwksProvider",
        available_jwks_provider,
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "compose_control_plane_runtime_dependencies",
        lambda **_: _control_plane_dependencies(),
    )
    installation_pages: list[tuple[int, int]] = []

    async def installations(
        _self: GitHubProviderInventory, *, page: int, per_page: int
    ) -> ProviderInstallationPage:
        installation_pages.append((page, per_page))
        return ProviderInstallationPage(
            page,
            per_page,
            False,
            (InstallationSummary(1001, 3003, "example", "Organization", "selected", "active"),),
        )

    monkeypatch.setattr(GitHubProviderInventory, "list_installations", installations)
    repository_pages: list[tuple[int, int, int]] = []

    async def installation(_self: object, installation_id: int) -> InstallationSummary:
        assert installation_id == 1001
        return InstallationSummary(1001, 3003, "example", "Organization", "selected", "active")

    async def repositories(
        _self: object, installation_id: int, *, page: int, per_page: int
    ) -> ProviderRepositoryPage:
        repository_pages.append((installation_id, page, per_page))
        return ProviderRepositoryPage(
            page,
            per_page,
            3,
            False,
            tuple(
                RepositorySummary(
                    scope=RepositoryScope(1001, identity),
                    node_id=f"R_{identity}",
                    owner_id=3003,
                    owner_login="example",
                    name=f"repo-{identity}",
                    full_name=f"example/repo-{identity}",
                    visibility="private",
                    default_branch="master",
                    archived=False,
                    disabled=identity == 2004,
                    fork=False,
                )
                for identity in (2002, 2003, 2004)
            ),
        )

    monkeypatch.setattr(GitHubProviderInventory, "get_installation", installation)
    monkeypatch.setattr(GitHubProviderInventory, "list_repositories", repositories)
    settings = _settings(
        runtime_postgres_database_url,
        control_plane_identity=True,
        inventory_only=inventory_only,
    )
    assert bool(settings.control_plane_scope_allowlist) is not inventory_only
    membership_calls: list[RepositoryScope] = []

    async def membership(_self: object, scope: RepositoryScope) -> bool:
        membership_calls.append(scope)
        return True

    monkeypatch.setattr(GitHubRepositoryAccess, "allows_repository", membership)
    if app_scope:
        settings = replace(settings, control_plane_scope_mode="app")
    runtime = compose_runtime_application(settings)
    assert isinstance(runtime, RuntimeApplication)

    request_body = _plan_request_body()
    token = _token(oidc_private_key, request_body)
    with TestClient(runtime.app) as client:
        readiness = client.get("/readyz")
        if inventory_only:
            denied = client.get(
                "/api/v1/workbench/installations",
                headers={"authorization": f"Bearer {'o' * 32}"},
            )
            assert denied.status_code == 403
            assert installation_pages == []
            catalog = client.get(
                "/api/v1/workbench/installations",
                headers={"authorization": f"Bearer {_MACHINE_TOKEN}"},
            )
            assert catalog.status_code == 200
            assert [item["installationId"] for item in catalog.json()["installations"]] == [1001]
            assert installation_pages == [(1, 30)]
            repository_catalog = client.get(
                "/api/v1/workbench/installations/1001/repositories",
                headers={"authorization": f"Bearer {_MACHINE_TOKEN}"},
            )
            assert repository_catalog.status_code == 200
            assert [
                (item["scope"]["repositoryId"], item["workbenchAuthorized"])
                for item in repository_catalog.json()["repositories"]
            ] == [(2002, app_scope), (2003, app_scope), (2004, False)]
            assert repository_pages == [(1001, 1, 100)]
            assert membership_calls == []
        first = client.post(
            "/api/v1/dynamic-ci/plan",
            json=request_body,
            headers={"authorization": f"Bearer {token}"},
        )
        replay = client.post(
            "/api/v1/dynamic-ci/plan",
            json=request_body,
            headers={"authorization": f"Bearer {token}"},
        )
        registration = client.post(
            "/api/v1/config/epochs",
            json={
                "schemaVersion": "ci-config-epoch-registration/v1",
                "sourceFormat": "json",
                "source": _policy_source(),
                "operationId": "runtime-composition-registration",
            },
            headers={"authorization": f"Bearer {_MACHINE_TOKEN}"},
        )
        assert "/api/v1/production/stages" not in runtime.app.openapi()["paths"]

    assert (readiness.status_code, readiness.json()["status"]) == (200, "ready")
    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    payload = first.json()["payload"]
    assert payload["verifiedPlanId"] is None
    assert payload["fallbackReason"] == "verified_plan_unavailable"
    assert payload["execution"] == {
        "mode": "full-ci",
        "reason": "verified_plan_unavailable",
    }
    assert payload["authenticatedRun"]["workflowRef"] == _WORKFLOW_REF
    assert payload["authenticatedRun"]["jobWorkflowRef"] == _JOB_WORKFLOW_REF
    assert bool(membership_calls) is app_scope
    if inventory_only and not app_scope:
        assert registration.status_code == 403
        return
    assert registration.status_code == 201
    registration_body = registration.json()
    assert type(registration_body["epochId"]) is str
    assert len(registration_body["epochId"]) == 64
    assert registration.json() == {
        "schemaVersion": "ci-config-epoch-registration-result/v1",
        "ok": True,
        "epochId": registration_body["epochId"],
        "duplicate": False,
    }


def _control_plane_dependencies() -> ControlPlaneRuntimeDependencies:
    clock = SystemClock()
    return ControlPlaneRuntimeDependencies(
        authenticator=ControlPlaneRequestAuthenticator(
            session_cookie_name=None,
            human=None,
            machine=_MachineIdentity(),
            break_glass=BreakGlassBearerAuthenticator(
                actor_id="break-glass:v1:release-engineer",
                bearer_token="o" * 32,
            ),
        ),
        role_admission=ControlPlaneRoleAuthorizer(clock),
        mutation_admission=ControlPlaneMutationGuard(human=None, public_origin=None),
        browser_identity=cast(BrowserIdentityService, object()),
        identity_crypto=ControlPlaneIdentityCrypto(b"c" * 32),
        keycloak=cast(KeycloakIntegration, _PreparedIdentityProvider()),
    )


@pytest.mark.parametrize("control_plane_identity", [False, True])
def test_enforcing_runtime_registers_authority_without_staging_or_activating_it(
    runtime_postgres_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_plane_identity: bool,
) -> None:
    now = datetime.now(UTC)
    subject = ProductionScopeSubject(
        scope=RepositoryScope(1001, 2002),
        config_epoch_id="1" * 64,
        compiled_policy_hash="2" * 64,
        policy_hash="3" * 64,
        catalog_hash="4" * 64,
        target_registry_hash="5" * 64,
        workflow_refs=(_WORKFLOW_REF,),
        job_workflow_refs=(_JOB_WORKFLOW_REF,),
    )
    admission = make_production_admission_fixture(
        subject,
        now=now,
        minimum_remaining_seconds=80,
    )
    receipt_path = tmp_path / "production-admission.json"
    receipt_path.write_bytes(admission.content)
    oidc_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = _AvailableJwksProvider(_key_set(oidc_private_key))

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "GitHubActionsJwksProvider",
        lambda _, **__: jwks,
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "load_bundled_build_identity",
        lambda: BuildIdentity(RELEASE_IDENTITY, SOURCE_COMMIT, True),
    )
    if control_plane_identity:
        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(
            runtime_composition,
            "compose_control_plane_runtime_dependencies",
            lambda **_: _control_plane_dependencies(),
        )
    settings = _enforcing_settings(
        runtime_postgres_database_url,
        receipt_path=receipt_path,
        public_key_pem=admission.public_key_pem,
        control_plane_identity=control_plane_identity,
    )

    runtime = compose_runtime_application(settings)

    assert isinstance(runtime, RuntimeApplication)
    with TestClient(runtime.app) as client:
        readiness = client.get("/readyz")
        stored = asyncio.run(
            _stored_production_admission(
                runtime_postgres_database_url,
                admission.grant.authority_id,
            )
        )
        paths = runtime.app.openapi()["paths"]
        assert ("/api/v1/production/stages" in paths) is control_plane_identity
        if control_plane_identity:
            response = client.get("/api/v1/production/scopes/1001/2002")
            assert response.status_code == 401

    assert (readiness.status_code, readiness.json()["status"]) == (200, "ready")
    registration = admission.grant.registration
    assert stored[:2] == (
        (
            registration.authority_id,
            registration.key_id,
            registration.public_key_spki_der,
            registration.envelope_canonical_json,
            registration.issued_at,
            registration.expires_at,
        ),
        (
            (
                registration.authority_id,
                subject.scope.installation_id,
                subject.scope.repository_id,
                registration.scope_bindings[0].admission_subject_digest,
                subject.config_epoch_id,
                subject.target_registry_hash,
            ),
        ),
    )
    assert stored[2] == (0, 0)


def test_invalid_production_receipt_is_rejected_before_resources_are_allocated(
    runtime_postgres_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    subject = ProductionScopeSubject(
        scope=RepositoryScope(1001, 2002),
        config_epoch_id="1" * 64,
        compiled_policy_hash="2" * 64,
        policy_hash="3" * 64,
        catalog_hash="4" * 64,
        target_registry_hash="5" * 64,
        workflow_refs=(_WORKFLOW_REF,),
        job_workflow_refs=(_JOB_WORKFLOW_REF,),
    )
    admission = make_production_admission_fixture(subject, now=now)
    receipt_path = tmp_path / "tampered-production-admission.json"
    receipt_path.write_bytes(admission.content.replace(b'"signature":"', b'"signature":"A', 1))

    def unexpected_allocation(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("resource allocated before production admission completed")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "load_bundled_build_identity",
        lambda: BuildIdentity(RELEASE_IDENTITY, SOURCE_COMMIT, True),
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "create_postgres_engine", unexpected_allocation)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubAppTransportFactory", unexpected_allocation)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubActionsJwksProvider", unexpected_allocation)
    settings = _enforcing_settings(
        runtime_postgres_database_url,
        receipt_path=receipt_path,
        public_key_pem=admission.public_key_pem,
    )

    assert compose_runtime_application(settings) == RuntimeCompositionRejection(
        "production_admission_unavailable",
        ("production_admission",),
    )


def _settings(
    database_dsn: str,
    *,
    control_plane_identity: bool = False,
    inventory_only: bool = False,
) -> NonEnforcingRuntimeSettings:
    mapping = _settings_mapping(database_dsn)
    if control_plane_identity:
        mapping.update(_identity_mapping())
    if inventory_only:
        mapping.pop("CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST")
        mapping["CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE"] = "app"
    admitted = admit_runtime_settings(mapping)
    assert isinstance(admitted, NonEnforcingRuntimeSettings)
    return admitted


def _enforcing_settings(
    database_dsn: str,
    *,
    receipt_path: Path,
    public_key_pem: bytes,
    control_plane_identity: bool = False,
) -> EnforcingRuntimeSettings:
    mapping = _settings_mapping(database_dsn)
    if control_plane_identity:
        mapping.update(_identity_mapping())
    mapping.update(
        {
            "CI_COORDINATOR_RUNTIME_MODE": "enforcing",
            "CI_COORDINATOR_PRODUCTION_ADMISSION_RECEIPT_PATH": str(receipt_path),
            "CI_COORDINATOR_PRODUCTION_ADMISSION_KEY_ID": PRODUCTION_KEY_ID,
            "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM": public_key_pem.decode("ascii"),
            "CI_COORDINATOR_DEPLOYED_ARTIFACT_DIGEST": ARTIFACT_DIGEST,
            "CI_COORDINATOR_ENVIRONMENT_ID": "production",
            "CI_COORDINATOR_ENFORCEMENT_SCOPE_ALLOWLIST": "1001:2002",
        }
    )
    admitted = admit_runtime_settings(mapping)
    assert isinstance(admitted, EnforcingRuntimeSettings)
    return admitted


def _identity_mapping() -> dict[str, str]:
    return {
        "CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE": "keycloak",
        "CI_COORDINATOR_KEYCLOAK_ISSUER": EXAMPLE_KEYCLOAK_ISSUER,
        "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID": CONTROL_PLANE_BROWSER_CLIENT_ID,
        "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET": "k" * 32,
        "CI_COORDINATOR_KEYCLOAK_API_CLIENT_ID": CONTROL_PLANE_API_CLIENT_ID,
        "CI_COORDINATOR_PUBLIC_ORIGIN": "https://coordinator.example.test",
        "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY": "A" * 43,
        "CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS": "900",
        "CI_COORDINATOR_KEYCLOAK_WORKLOAD_CLIENT_ALLOWLIST": "runtime-integration-test",
        "CI_COORDINATOR_GITHUB_APP_CLIENT_ID": GITHUB_REVIEWER_CLIENT_ID,
        "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET": "r" * 32,
    }


def _settings_mapping(database_dsn: str) -> dict[str, str]:
    github_key = _private_key_pem(rsa.generate_private_key(65537, 2048))
    signing_key = _private_key_pem(ed25519.Ed25519PrivateKey.generate())
    return {
        "CI_COORDINATOR_RUNTIME_MODE": "non_enforcing",
        "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
        "CI_COORDINATOR_BIND_PORT": "8080",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        "CI_COORDINATOR_REQUEST_TIMEOUT_SECONDS": "20",
        "CI_COORDINATOR_MAXIMUM_RETAINED_BODY_BYTES": "33554432",
        "CI_COORDINATOR_DATABASE_POOL_SIZE": "16",
        "CI_COORDINATOR_DATABASE_POOL_TIMEOUT_SECONDS": "5",
        "CI_COORDINATOR_PLAN_TTL_SECONDS": "60",
        "CI_COORDINATOR_RECONCILIATION_INTERVAL_SECONDS": "30",
        "CI_COORDINATOR_RECONCILIATION_STARTUP_TIMEOUT_SECONDS": "30",
        "CI_COORDINATOR_RECONCILIATION_SCAN_LIMIT": "100",
        "CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID": ROLLOUT_PROFILE_ID,
        "CI_COORDINATOR_DATABASE_DSN": database_dsn,
        "CI_COORDINATOR_WEBHOOK_SECRET": "w" * 32,
        "CI_COORDINATOR_GITHUB_APP_ID": "1234",
        "CI_COORDINATOR_GITHUB_PRIVATE_KEY": github_key,
        "CI_COORDINATOR_PLAN_SIGNING_KEY_ID": "plan-key",
        "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY": signing_key,
        "CI_COORDINATOR_OIDC_AUDIENCE": "ci-coordinator",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS": _WORKFLOW_REF,
        "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS": _JOB_WORKFLOW_REF,
        "CI_COORDINATOR_BREAK_GLASS_ACTOR_ID": "break-glass:v1:release-engineer",
        "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN": "o" * 32,
        "CI_COORDINATOR_METRICS_BEARER_TOKEN": "m" * 32,
        "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST": "1001:2002",
    }


async def _stored_production_admission(
    database_dsn: str,
    authority_id: str,
) -> tuple[tuple[object, ...], tuple[tuple[object, ...], ...], tuple[int, int]]:
    engine = create_postgres_engine(database_dsn)
    try:
        async with engine.connect() as connection:
            authority = (
                await connection.execute(
                    select(
                        production_admission_authorities.c.authority_id,
                        production_admission_authorities.c.key_id,
                        production_admission_authorities.c.public_key_spki_der,
                        production_admission_authorities.c.envelope_canonical_json,
                        production_admission_authorities.c.issued_at,
                        production_admission_authorities.c.expires_at,
                    ).where(production_admission_authorities.c.authority_id == authority_id)
                )
            ).one()
            bindings = tuple(
                tuple(row)
                for row in await connection.execute(
                    select(
                        production_admission_scope_bindings.c.authority_id,
                        production_admission_scope_bindings.c.installation_id,
                        production_admission_scope_bindings.c.repository_id,
                        production_admission_scope_bindings.c.admission_subject_digest,
                        production_admission_scope_bindings.c.config_epoch_id,
                        production_admission_scope_bindings.c.target_registry_hash,
                    )
                    .where(production_admission_scope_bindings.c.authority_id == authority_id)
                    .order_by(
                        production_admission_scope_bindings.c.installation_id,
                        production_admission_scope_bindings.c.repository_id,
                    )
                )
            )
            return (
                (
                    authority.authority_id,
                    authority.key_id,
                    bytes(authority.public_key_spki_der),
                    bytes(authority.envelope_canonical_json),
                    authority.issued_at,
                    authority.expires_at,
                ),
                bindings,
                (
                    (
                        await connection.scalar(
                            select(func.count()).select_from(production_scope_states)
                        )
                    )
                    or 0,
                    (
                        await connection.scalar(
                            select(func.count()).select_from(production_staged_grants)
                        )
                    )
                    or 0,
                ),
            )
    finally:
        await engine.dispose()


def _plan_request_body() -> dict[str, object]:
    return {
        "schemaVersion": "dynamic-ci-plan-request/v2",
        "requestId": "runtime-request-1",
        "installationId": 1001,
        "repositoryId": 2002,
        "owner": "example",
        "repository": "ci",
        "eventName": "pull_request",
        "ref": "refs/pull/42/merge",
        "baseSha": "a" * 40,
        "headSha": "b" * 40,
        "executionSha": "c" * 40,
        "workflowRunId": 7001,
        "runAttempt": 1,
        "pullRequestNumber": 42,
        "mergeGroupHeadRef": None,
    }


def _token(private_key: rsa.RSAPrivateKey, request: dict[str, object]) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "ci-coordinator",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "nbf": int((now - timedelta(minutes=1)).timestamp()),
            "repository": f"{request['owner']}/{request['repository']}",
            "repository_id": str(request["repositoryId"]),
            "ref": request["ref"],
            "run_id": str(request["workflowRunId"]),
            "run_attempt": str(request["runAttempt"]),
            "event_name": request["eventName"],
            "sha": request["executionSha"],
            "workflow_ref": _WORKFLOW_REF,
            "workflow_sha": "b" * 40,
            "job_workflow_ref": _JOB_WORKFLOW_REF,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _key_set(private_key: rsa.RSAPrivateKey) -> ActionsOidcJwkSet:
    public = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    assert type(public) is dict
    return ActionsOidcJwkSet(({**public, "kid": "test-key", "use": "sig", "alg": "RS256"},))


def _private_key_pem(key: rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey) -> str:
    return key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("ascii")


def _policy_source() -> str:
    return json.dumps(
        {
            "schemaVersion": "ci-repository-policy/v1",
            "repository": {
                "installationId": 1001,
                "repositoryId": 2002,
                "owner": "example",
                "name": "ci",
                "defaultBranch": "master",
                "rules": [
                    {
                        "name": "master",
                        "on": {"event": "push", "branches": ["master"]},
                        "mode": "observe",
                        "expectedSignals": [
                            {
                                "kind": "workflow",
                                "name": "CI",
                                "workflowFile": "ci.yml",
                                "source": "native",
                                "requiredConclusion": "success",
                                "required": True,
                            }
                        ],
                    }
                ],
                "dynamicCi": None,
            },
        },
        separators=(",", ":"),
    )
