from __future__ import annotations

import os
from pathlib import Path

import pytest

from ci_coordinator.runtime import environment as runtime_environment
from ci_coordinator.runtime.environment import load_runtime_settings_from_environment
from ci_coordinator.runtime_settings import (
    EnforcingRuntimeSettings,
    NonEnforcingRuntimeSettings,
    RuntimeSettingsRejection,
)


@pytest.mark.parametrize(
    ("direct_name", "file_name", "mode"),
    [
        ("CI_COORDINATOR_DATABASE_DSN", "CI_COORDINATOR_DATABASE_DSN_FILE", "non_enforcing"),
        ("CI_COORDINATOR_WEBHOOK_SECRET", "CI_COORDINATOR_WEBHOOK_SECRET_FILE", "non_enforcing"),
        (
            "CI_COORDINATOR_GITHUB_PRIVATE_KEY",
            "CI_COORDINATOR_GITHUB_PRIVATE_KEY_FILE",
            "non_enforcing",
        ),
        (
            "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY",
            "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY_FILE",
            "non_enforcing",
        ),
        (
            "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN",
            "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN_FILE",
            "non_enforcing",
        ),
        (
            "CI_COORDINATOR_METRICS_BEARER_TOKEN",
            "CI_COORDINATOR_METRICS_BEARER_TOKEN_FILE",
            "non_enforcing",
        ),
        (
            "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET",
            "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET_FILE",
            "control_plane",
        ),
        (
            "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY",
            "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY_FILE",
            "control_plane",
        ),
        (
            "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET",
            "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET_FILE",
            "control_plane",
        ),
        (
            "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM",
            "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM_FILE",
            "enforcing",
        ),
    ],
)
def test_runtime_secrets_admit_exactly_one_file_backed_form(
    tmp_path: Path,
    direct_name: str,
    file_name: str,
    mode: str,
) -> None:
    environment = _environment(mode)
    value = environment.pop(direct_name)
    secret_path = tmp_path / direct_name.lower()
    secret_path.write_text(value + "\n", encoding="utf-8")
    environment[file_name] = str(secret_path)

    result = load_runtime_settings_from_environment(environment)

    if mode == "enforcing":
        assert isinstance(result, EnforcingRuntimeSettings)
    else:
        assert isinstance(result, NonEnforcingRuntimeSettings)
    assert value not in repr(result)


def test_runtime_secret_rejects_direct_and_file_forms_together(tmp_path: Path) -> None:
    environment = _environment("non_enforcing")
    path = tmp_path / "database-dsn"
    path.write_text(environment["CI_COORDINATOR_DATABASE_DSN"], encoding="utf-8")
    environment["CI_COORDINATOR_DATABASE_DSN_FILE"] = str(path)

    assert load_runtime_settings_from_environment(environment) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_DATABASE_DSN_FILE",
    )


@pytest.mark.parametrize(
    ("mode", "file_name"),
    [
        ("disabled", "CI_COORDINATOR_DATABASE_DSN_FILE"),
        ("non_enforcing", "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM_FILE"),
    ],
)
def test_irrelevant_file_secret_is_rejected_before_file_access(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    file_name: str,
) -> None:
    environment = (
        {
            "CI_COORDINATOR_RUNTIME_MODE": "disabled",
            "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
            "CI_COORDINATOR_BIND_PORT": "8080",
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        }
        if mode == "disabled"
        else _environment(mode)
    )
    environment[file_name] = "/must-not-be-read"
    if mode == "non_enforcing":
        del environment["CI_COORDINATOR_DATABASE_DSN"]
        environment["CI_COORDINATOR_DATABASE_DSN_FILE"] = "/otherwise-valid-secret"
    monkeypatch.setattr(
        runtime_environment,
        "_read_secret_file",
        lambda *_args: pytest.fail("irrelevant secret files must not be read"),
    )

    assert load_runtime_settings_from_environment(environment) == RuntimeSettingsRejection(
        "invalid_setting_value",
        file_name,
    )


@pytest.mark.parametrize(
    "case",
    [
        "relative",
        "writable",
        "non_regular",
        "oversized",
        "invalid_utf8",
        "invalid_path_unicode",
        "nul",
    ],
)
def test_runtime_secret_file_rejects_unsafe_or_unbounded_inputs(
    tmp_path: Path,
    case: str,
) -> None:
    environment = _environment("non_enforcing")
    del environment["CI_COORDINATOR_DATABASE_DSN"]
    path = tmp_path / "database-dsn"
    if case == "relative":
        path_text = "database-dsn"
    elif case == "invalid_path_unicode":
        path_text = f"{tmp_path}/\ud800"
    elif case == "non_regular":
        os.mkfifo(path)
        path_text = str(path)
    elif case == "oversized":
        path.write_bytes(b"x" * 65_537)
        path_text = str(path)
    elif case == "invalid_utf8":
        path.write_bytes(b"\xff")
        path_text = str(path)
    elif case == "nul":
        path.write_bytes(b"database\x00dsn")
        path_text = str(path)
    else:
        path.write_text("database-dsn", encoding="utf-8")
        path.chmod(0o666)
        path_text = str(path)
    environment["CI_COORDINATOR_DATABASE_DSN_FILE"] = path_text

    assert load_runtime_settings_from_environment(environment) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_DATABASE_DSN_FILE",
    )


def test_runtime_secret_file_allows_one_framing_lf_beyond_the_value_bound(
    tmp_path: Path,
) -> None:
    environment = _environment("non_enforcing")
    del environment["CI_COORDINATOR_DATABASE_DSN"]
    path = tmp_path / "database-dsn"
    path.write_bytes(b"d" * 65_536 + b"\n")
    environment["CI_COORDINATOR_DATABASE_DSN_FILE"] = str(path)

    assert isinstance(
        load_runtime_settings_from_environment(environment),
        NonEnforcingRuntimeSettings,
    )


def test_runtime_secret_file_maps_descriptor_close_failure_to_typed_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment("non_enforcing")
    del environment["CI_COORDINATOR_DATABASE_DSN"]
    path = tmp_path / "database-dsn"
    path.write_text("database-dsn", encoding="utf-8")
    environment["CI_COORDINATOR_DATABASE_DSN_FILE"] = str(path)
    close_descriptor = os.close

    def fail_after_close(descriptor: int) -> None:
        close_descriptor(descriptor)
        raise OSError("simulated close failure")

    monkeypatch.setattr(os, "close", fail_after_close)

    assert load_runtime_settings_from_environment(environment) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_DATABASE_DSN_FILE",
    )


def _environment(mode: str) -> dict[str, str]:
    environment = {
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
        "CI_COORDINATOR_DATABASE_DSN": "database-dsn",
        "CI_COORDINATOR_WEBHOOK_SECRET": "w" * 32,
        "CI_COORDINATOR_GITHUB_APP_ID": "1234",
        "CI_COORDINATOR_GITHUB_PRIVATE_KEY": "github-private-key",
        "CI_COORDINATOR_PLAN_SIGNING_KEY_ID": "plan-key",
        "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY": "plan-private-key",
        "CI_COORDINATOR_OIDC_AUDIENCE": "ci-coordinator",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS": "workflow-a",
        "CI_COORDINATOR_BREAK_GLASS_ACTOR_ID": "break-glass:v1:local-development",
        "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN": "b" * 32,
        "CI_COORDINATOR_METRICS_BEARER_TOKEN": "m" * 32,
        "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST": "100:200",
    }
    if mode == "control_plane":
        environment.update(
            {
                "CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE": "keycloak",
                "CI_COORDINATOR_KEYCLOAK_ISSUER": ("https://auth.example.test/realms/coordinator"),
                "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID": "ci-coordinator-admin-ui",
                "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET": "k" * 32,
                "CI_COORDINATOR_KEYCLOAK_API_CLIENT_ID": "ci-coordinator-admin-api",
                "CI_COORDINATOR_PUBLIC_ORIGIN": "https://ci.example.test",
                "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY": "A" * 43,
                "CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS": "900",
                "CI_COORDINATOR_GITHUB_APP_CLIENT_ID": "Iv1SyntheticClient01",
                "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET": "r" * 32,
            }
        )
    if mode == "enforcing":
        environment.update(
            {
                "CI_COORDINATOR_RUNTIME_MODE": "enforcing",
                "CI_COORDINATOR_PRODUCTION_ADMISSION_RECEIPT_PATH": "/run/receipt.json",
                "CI_COORDINATOR_PRODUCTION_ADMISSION_KEY_ID": "production-key",
                "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM": "production-public-key",
                "CI_COORDINATOR_DEPLOYED_ARTIFACT_DIGEST": "sha256:" + "b" * 64,
                "CI_COORDINATOR_ENVIRONMENT_ID": "production",
                "CI_COORDINATOR_ENFORCEMENT_SCOPE_ALLOWLIST": "100:200",
            }
        )
    return environment
