from __future__ import annotations

import asyncio
import json
import logging
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from fastapi.testclient import TestClient

from ci_coordinator.api.http import app as http_app
from ci_coordinator.api.http.dependencies import HttpRouteDependencies
from ci_coordinator.observability import StructuredEventLogger
from ci_coordinator.runtime import application as runtime_application
from ci_coordinator.runtime import composition as runtime_composition
from ci_coordinator.runtime import event_logging
from ci_coordinator.runtime.application import (
    RuntimeApplication,
    RuntimeCompositionRejection,
    compose_runtime_application,
)
from ci_coordinator.runtime_settings import (
    BuildIdentity,
    DisabledRuntimeSettings,
    EnforcingRuntimeSettings,
    NonEnforcingRuntimeSettings,
    admit_runtime_settings,
)


@pytest.mark.parametrize("connected", [False, True], ids=["disabled", "connected"])
def test_composed_request_logger_retains_packaged_build_identity(
    monkeypatch: pytest.MonkeyPatch, connected: bool
) -> None:
    output = StringIO()
    sink = logging.Logger("composed-build-test")
    sink.addHandler(logging.StreamHandler(output))

    def factory(
        *, source_commit: str | None = None, release_identity: str | None = None
    ) -> StructuredEventLogger:
        return StructuredEventLogger(
            sink, source_commit=source_commit, release_identity=release_identity
        )

    monkeypatch.setattr(
        event_logging,
        "load_bundled_build_identity",
        lambda: BuildIdentity("b" * 64, "a" * 40, True),
    )
    monkeypatch.setattr(event_logging, "default_structured_event_logger", factory)
    app_factory = Mock(wraps=http_app.create_app)
    owner = runtime_composition if connected else runtime_application
    monkeypatch.setattr(owner, "create_app", app_factory)
    if connected:
        _, resources = runtime_composition.compose_non_enforcing_dependencies(
            _non_enforcing_settings()
        )
        asyncio.run(resources.aclose())
    else:
        assert isinstance(compose_runtime_application(_disabled_settings()), RuntimeApplication)
    app_factory.assert_called_once()
    routes = app_factory.call_args.args[0]
    assert isinstance(routes, HttpRouteDependencies)
    assert routes.observability is not None
    assert routes.observability.request_logger is not None
    routes.observability.request_logger.emit({"event": "composition-probe"})
    record = json.loads(output.getvalue().splitlines()[-1])
    assert record["sourceCommit"] == "a" * 40
    assert record["releaseIdentity"] == "b" * 64


def test_disabled_runtime_exposes_only_operability_surfaces() -> None:
    result = compose_runtime_application(_disabled_settings())

    assert isinstance(result, RuntimeApplication)
    with TestClient(result.app) as client:
        assert client.get("/healthz").json() == {"ok": True, "status": "alive"}
        assert client.get("/api/v1/health").json() == {"ok": True, "status": "alive"}
        readiness = client.get("/readyz")
        assert readiness.status_code == 503
        assert readiness.json() == {
            "ok": False,
            "status": "not_ready",
        }
        versioned_readiness = client.get("/api/v1/ready")
        assert versioned_readiness.status_code == 503
        assert versioned_readiness.json() == readiness.json()
        assert client.get("/workbench").status_code == 404
        assert client.get("/assets/unknown.js").status_code == 404
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert metrics.headers["content-type"].startswith("text/plain; version=1.0.0")
        assert "ci_coordinator_http_requests_total" in metrics.text
        assert client.post("/api/v1/dynamic-ci/plan").status_code == 404
        assert client.post("/webhooks/github").status_code == 404
        assert client.post("/api/v1/overrides/full-ci").status_code == 404


def test_disabled_runtime_does_not_load_connected_webhook_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_policy_load() -> None:
        raise AssertionError("disabled runtime loaded connected webhook policy")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(http_app, "github_webhook_body_limit", unexpected_policy_load)

    assert isinstance(compose_runtime_application(_disabled_settings()), RuntimeApplication)


def test_non_enforcing_runtime_mounts_the_complete_non_enforcing_http_surface() -> None:
    result = compose_runtime_application(_non_enforcing_settings())

    assert isinstance(result, RuntimeApplication)
    paths = set(result.app.openapi()["paths"])
    assert {
        "/api/v1/dynamic-ci/plan",
        "/webhooks/github",
        "/api/v1/overrides/full-ci",
        "/healthz",
        "/readyz",
        "/metrics",
    }.issubset(paths)
    assert result.settings.mode == "non_enforcing"
    assert "database-dsn" not in repr(result)
    assert "o" * 32 not in repr(result)


def test_non_enforcing_runtime_does_not_hide_programming_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_: NonEnforcingRuntimeSettings) -> None:
        raise RuntimeError("composition invariant failed")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_application, "compose_non_enforcing_dependencies", fail)

    with pytest.raises(RuntimeError, match="composition invariant failed"):
        compose_runtime_application(_non_enforcing_settings())


def test_enforcing_runtime_reports_unavailable_authority_without_allocating_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _enforcing_settings(Path("/missing/production-admission.json"))

    def fail(_: EnforcingRuntimeSettings) -> None:
        raise runtime_composition.ProductionAdmissionConfigurationError("invalid receipt")

    monkeypatch.setattr(runtime_application, "compose_enforcing_dependencies", fail)

    assert compose_runtime_application(settings) == RuntimeCompositionRejection(
        "production_admission_unavailable",
        ("production_admission",),
    )


def test_runtime_rejects_an_unavailable_bundled_caller_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_inventory() -> None:
        raise ValueError("malformed installed resource")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_application, "load_bundled_caller_inventory", unavailable_inventory)

    result = compose_runtime_application(_disabled_settings())

    assert result == RuntimeCompositionRejection(
        "runtime_caller_inventory_unavailable",
        ("runtime_caller_inventory",),
    )


def test_runtime_rejects_an_unavailable_entrypoint_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_disposition(_: object) -> None:
        raise ValueError("malformed installed resource")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_application,
        "load_bundled_entrypoint_disposition",
        unavailable_disposition,
    )

    result = compose_runtime_application(_disabled_settings())

    assert result == RuntimeCompositionRejection(
        "runtime_entrypoint_disposition_unavailable",
        ("runtime_entrypoint_disposition",),
    )


def _disabled_settings() -> DisabledRuntimeSettings:
    result = admit_runtime_settings(
        {
            "CI_COORDINATOR_RUNTIME_MODE": "disabled",
            "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
            "CI_COORDINATOR_BIND_PORT": "8080",
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        }
    )
    assert isinstance(result, DisabledRuntimeSettings)
    return result


def _non_enforcing_settings(
    *, github_private_key: str | None = None
) -> NonEnforcingRuntimeSettings:
    github_key = github_private_key or _private_key_pem(rsa.generate_private_key(65537, 2048))
    signing_key = _private_key_pem(ed25519.Ed25519PrivateKey.generate())
    result = admit_runtime_settings(
        {
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
            "CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID": "a" * 64,
            "CI_COORDINATOR_DATABASE_DSN": (
                "postgresql+psycopg://ci_coordinator:secret@127.0.0.1:5432/ci_coordinator"
            ),
            "CI_COORDINATOR_WEBHOOK_SECRET": "w" * 32,
            "CI_COORDINATOR_GITHUB_APP_ID": "1234",
            "CI_COORDINATOR_GITHUB_PRIVATE_KEY": github_key,
            "CI_COORDINATOR_PLAN_SIGNING_KEY_ID": "plan-key",
            "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY": signing_key,
            "CI_COORDINATOR_OIDC_AUDIENCE": "ci-coordinator",
            "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS": "workflow-a,workflow-b",
            "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS": "job-workflow-a",
            "CI_COORDINATOR_BREAK_GLASS_ACTOR_ID": "break-glass:v1:release-engineer",
            "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN": "o" * 32,
            "CI_COORDINATOR_METRICS_BEARER_TOKEN": "m" * 32,
            "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST": "100:200",
        }
    )
    assert isinstance(result, NonEnforcingRuntimeSettings)
    return result


def _private_key_pem(key: rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey) -> str:
    return key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("ascii")


def _enforcing_settings(receipt_path: Path) -> EnforcingRuntimeSettings:
    non_enforcing = _non_enforcing_settings()
    result = admit_runtime_settings(
        {
            "CI_COORDINATOR_RUNTIME_MODE": "enforcing",
            "CI_COORDINATOR_BIND_HOST": non_enforcing.bind_host,
            "CI_COORDINATOR_BIND_PORT": str(non_enforcing.bind_port),
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": str(non_enforcing.shutdown_timeout_seconds),
            "CI_COORDINATOR_REQUEST_TIMEOUT_SECONDS": str(non_enforcing.request_timeout_seconds),
            "CI_COORDINATOR_MAXIMUM_RETAINED_BODY_BYTES": str(
                non_enforcing.maximum_retained_body_bytes
            ),
            "CI_COORDINATOR_DATABASE_POOL_SIZE": str(non_enforcing.database_pool_size),
            "CI_COORDINATOR_DATABASE_POOL_TIMEOUT_SECONDS": str(
                non_enforcing.database_pool_timeout_seconds
            ),
            "CI_COORDINATOR_PLAN_TTL_SECONDS": str(non_enforcing.plan_ttl_seconds),
            "CI_COORDINATOR_RECONCILIATION_INTERVAL_SECONDS": str(
                non_enforcing.reconciliation_interval_seconds
            ),
            "CI_COORDINATOR_RECONCILIATION_STARTUP_TIMEOUT_SECONDS": str(
                non_enforcing.reconciliation_startup_timeout_seconds
            ),
            "CI_COORDINATOR_RECONCILIATION_SCAN_LIMIT": str(
                non_enforcing.reconciliation_scan_limit
            ),
            "CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID": (non_enforcing.shadow_rollout_profile_id),
            "CI_COORDINATOR_DATABASE_DSN": non_enforcing.database_dsn.reveal_for_composition(),
            "CI_COORDINATOR_WEBHOOK_SECRET": (
                non_enforcing.webhook_secret.reveal_for_composition()
            ),
            "CI_COORDINATOR_GITHUB_APP_ID": non_enforcing.github_app_id,
            "CI_COORDINATOR_GITHUB_PRIVATE_KEY": (
                non_enforcing.github_private_key.reveal_for_composition()
            ),
            "CI_COORDINATOR_PLAN_SIGNING_KEY_ID": non_enforcing.plan_signing_key_id,
            "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY": (
                non_enforcing.plan_signing_private_key.reveal_for_composition()
            ),
            "CI_COORDINATOR_OIDC_AUDIENCE": non_enforcing.oidc_audience,
            "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS": ",".join(
                sorted(non_enforcing.oidc_allowed_workflow_refs)
            ),
            "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS": ",".join(
                sorted(non_enforcing.oidc_allowed_job_workflow_refs)
            ),
            "CI_COORDINATOR_BREAK_GLASS_ACTOR_ID": non_enforcing.break_glass_actor_id,
            "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN": (
                non_enforcing.break_glass_bearer_token.reveal_for_composition()
            ),
            "CI_COORDINATOR_METRICS_BEARER_TOKEN": (
                non_enforcing.metrics_bearer_token.reveal_for_composition()
            ),
            "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST": "100:200",
            "CI_COORDINATOR_PRODUCTION_ADMISSION_RECEIPT_PATH": str(receipt_path),
            "CI_COORDINATOR_PRODUCTION_ADMISSION_KEY_ID": "production-key",
            "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM": "public-key",
            "CI_COORDINATOR_DEPLOYED_ARTIFACT_DIGEST": "sha256:" + "b" * 64,
            "CI_COORDINATOR_ENVIRONMENT_ID": "production",
            "CI_COORDINATOR_ENFORCEMENT_SCOPE_ALLOWLIST": "100:200",
        }
    )
    assert isinstance(result, EnforcingRuntimeSettings)
    return result
