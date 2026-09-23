from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from typing import cast

import pytest

from ci_coordinator.runtime_settings import (
    DisabledRuntimeSettings,
    EnforcingRuntimeSettings,
    NonEnforcingRuntimeSettings,
    RuntimeSettingsRejection,
    SecretValue,
    admit_runtime_settings,
    decode_control_plane_session_key,
    redacted_settings_projection,
)


def test_non_enforcing_settings_are_immutable_and_redacted() -> None:
    settings = admit_runtime_settings(_non_enforcing_mapping())

    assert isinstance(settings, NonEnforcingRuntimeSettings)
    projection = redacted_settings_projection(settings)
    assert projection.mode == "non_enforcing"
    assert projection.has_webhook_secret is True
    assert projection.has_break_glass_bearer_token is True
    assert projection.has_metrics_bearer_token is True
    assert projection.maximum_retained_body_bytes == 33_554_432
    assert projection.database_pool_size == 16
    assert projection.database_pool_timeout_seconds == 5
    assert projection.control_plane_scope_count == 2
    assert projection.control_plane_scope_mode == "restricted"
    assert projection.control_plane_inventory_installation_count == 0
    assert projection.oidc_allowed_workflow_ref_count == 2
    assert projection.oidc_allowed_job_workflow_ref_count == 1
    assert projection.oidc_allowed_workflow_path_count == 0
    assert projection.oidc_allowed_job_workflow_path_count == 0
    assert projection.shadow_rollout_profile_id == "a" * 64
    assert projection.reconciliation_startup_timeout_seconds == 30
    assert projection.control_plane_identity_mode == "disabled"
    assert projection.has_control_plane_browser_client_secret is False
    assert projection.has_control_plane_session_key is False
    assert projection.has_github_reviewer_client_secret is False
    assert projection.outbound_proxy_configured is False
    assert settings.outbound_proxy_url is None
    assert "w" * 32 not in repr(settings)
    assert "github-private-key" not in repr(settings)
    assert "database-dsn" not in repr(projection)
    assert "b" * 32 not in repr(settings)


@pytest.mark.parametrize(
    ("public_origin", "secure_cookies", "session_cookie_name"),
    [
        (
            "https://ci.example.test",
            True,
            "__Host-ci_coordinator_session",
        ),
        (
            "http://localhost:8080",
            False,
            "ci_coordinator_dev_session",
        ),
        (
            "http://[::1]:8080",
            False,
            "ci_coordinator_dev_session",
        ),
    ],
)
def test_control_plane_identity_is_explicit_bounded_and_redacted(
    public_origin: str,
    secure_cookies: bool,
    session_cookie_name: str,
) -> None:
    mapping = _non_enforcing_mapping()
    mapping.update(_control_plane_identity_mapping(public_origin))

    settings = admit_runtime_settings(mapping)

    assert isinstance(settings, NonEnforcingRuntimeSettings)
    identity = settings.control_plane_identity
    assert identity is not None
    assert identity.callback_uri == f"{public_origin}/api/v1/auth/keycloak/callback"
    assert identity.secure_cookies is secure_cookies
    assert identity.session_cookie_name == session_cookie_name
    assert decode_control_plane_session_key(identity) == bytes(32)
    projection = redacted_settings_projection(settings)
    assert projection.control_plane_identity_mode == "keycloak"
    assert projection.control_plane_public_origin == public_origin
    assert projection.control_plane_session_maximum_seconds == 900
    assert projection.control_plane_profile_digest == identity.profile_digest
    assert projection.has_control_plane_browser_client_secret is True
    assert projection.has_control_plane_session_key is True
    assert projection.has_github_reviewer_client_secret is True
    assert "k" * 32 not in repr(settings)
    assert "r" * 32 not in repr(settings)
    assert "A" * 43 not in repr(settings)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE", "other"),
        ("CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID", "invalid-client"),
        ("CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET", ""),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "http://example.com"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://example.com/path"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://EXAMPLE.com"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://example.com:443"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://exam\nple.com"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://exam\tple.com"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://example.com\\evil"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://[::1"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://exa_mple.com"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://-bad.example"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://bad-.example"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://127.0.0.01"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://example.com:0"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://0.0.0.0"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://[::]"),
        ("CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY", "A" * 42),
        ("CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS", "59"),
    ],
)
def test_control_plane_identity_rejects_noncanonical_configuration(
    field_name: str,
    value: str,
) -> None:
    mapping = _non_enforcing_mapping()
    mapping.update(_control_plane_identity_mapping("https://ci.example.test"))
    mapping[field_name] = value

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "missing_required_setting" if value == "" else "invalid_setting_value",
        field_name,
    )


def test_control_plane_identity_rejects_partial_and_disabled_configuration() -> None:
    partial = _non_enforcing_mapping()
    partial["CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID"] = "ci-coordinator-admin-ui"
    disabled = _non_enforcing_mapping()
    disabled.update(_control_plane_identity_mapping("https://ci.example.test"))
    disabled["CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE"] = "disabled"

    assert admit_runtime_settings(partial) == RuntimeSettingsRejection(
        "missing_required_setting",
        "CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE",
    )
    assert admit_runtime_settings(disabled) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE",
    )


@pytest.mark.parametrize(
    "runtime_mode",
    ["non_enforcing", "enforcing"],
)
@pytest.mark.parametrize(
    ("proxy_url", "expected_url"),
    [
        ("http://proxy.example.test", "http://proxy.example.test"),
        ("http://proxy.example.test/", "http://proxy.example.test"),
        ("http://proxy.example.test:3128", "http://proxy.example.test:3128"),
        ("http://proxy.example.test:3128/", "http://proxy.example.test:3128"),
        ("http://127.0.0.1:3128", "http://127.0.0.1:3128"),
        ("http://[::1]:3128", "http://[::1]:3128"),
    ],
)
def test_outbound_proxy_is_explicit_canonical_and_redacted(
    runtime_mode: str,
    proxy_url: str,
    expected_url: str,
) -> None:
    mapping = _enforcing_mapping() if runtime_mode == "enforcing" else _non_enforcing_mapping()
    mapping["CI_COORDINATOR_OUTBOUND_PROXY_URL"] = proxy_url

    settings = admit_runtime_settings(mapping)

    assert isinstance(settings, NonEnforcingRuntimeSettings | EnforcingRuntimeSettings)
    assert settings.outbound_proxy_url == expected_url
    projection = redacted_settings_projection(settings)
    assert projection.outbound_proxy_configured is True
    assert proxy_url not in repr(projection)


@pytest.mark.parametrize(
    "proxy_url",
    [
        "",
        "ftp://proxy.example.test",
        "https://proxy.example.test:8443",
        "http://user:secret@proxy.example.test:3128",
        "http://proxy.example.test/path",
        "http://proxy.example.test?route=github",
        "http://proxy.example.test#fragment",
        "HTTP://proxy.example.test:3128",
        "http://PROXY.example.test:3128",
        "http://proxy.example.test:80",
        "https://proxy.example.test:443",
        "http://proxy.example.test:0",
        "http://proxy_example.test:3128",
        "http://127.0.0.01:3128",
        "http://0.0.0.0:3128",
        "http://[::]:3128",
        "http://proxy.example.test:3128\n",
        "http://proxy.example.test\\evil",
    ],
)
def test_outbound_proxy_rejects_ambiguous_or_credential_bearing_urls(
    proxy_url: str,
) -> None:
    mapping = _non_enforcing_mapping()
    mapping["CI_COORDINATOR_OUTBOUND_PROXY_URL"] = proxy_url

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_OUTBOUND_PROXY_URL",
    )


def test_disabled_mode_rejects_unused_outbound_proxy_wiring() -> None:
    result = admit_runtime_settings(
        {
            "CI_COORDINATOR_RUNTIME_MODE": "disabled",
            "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
            "CI_COORDINATOR_BIND_PORT": "8080",
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
            "CI_COORDINATOR_OUTBOUND_PROXY_URL": "http://proxy.example.test:3128",
        }
    )

    assert result == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_OUTBOUND_PROXY_URL",
    )


def test_partial_signing_configuration_is_rejected_before_composition() -> None:
    mapping = _non_enforcing_mapping()
    del mapping["CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY"]

    result = admit_runtime_settings(mapping)

    assert result == RuntimeSettingsRejection(
        "incomplete_signing_configuration",
        "CI_COORDINATOR_PLAN_SIGNING_KEY_ID",
    )


def test_enforcing_settings_require_explicit_bounded_production_authority() -> None:
    settings = admit_runtime_settings(_enforcing_mapping())

    assert isinstance(settings, EnforcingRuntimeSettings)
    assert settings.enforcement_scope_allowlist == frozenset({"100:200"})
    projection = redacted_settings_projection(settings)
    assert projection.mode == "enforcing"
    assert projection.production_admission_key_id == "production-key"
    assert projection.deployed_artifact_digest == "sha256:" + "b" * 64
    assert projection.environment_id == "production"
    assert projection.enforcement_scope_count == 1
    assert projection.has_production_admission_receipt is True
    assert projection.has_production_admission_public_key is True
    assert "production-public-key" not in repr(settings)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("CI_COORDINATOR_PRODUCTION_ADMISSION_RECEIPT_PATH", "relative/receipt.json"),
        ("CI_COORDINATOR_PRODUCTION_ADMISSION_KEY_ID", "k" * 129),
        ("CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM", ""),
        ("CI_COORDINATOR_DEPLOYED_ARTIFACT_DIGEST", "b" * 64),
        ("CI_COORDINATOR_ENVIRONMENT_ID", "Production"),
        ("CI_COORDINATOR_ENFORCEMENT_SCOPE_ALLOWLIST", "999:999"),
    ],
)
def test_enforcing_rejections_name_the_exact_invalid_field(
    field_name: str,
    value: str,
) -> None:
    mapping = _enforcing_mapping()
    mapping[field_name] = value

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value" if value else "missing_required_setting",
        field_name,
    )


def test_disabled_mode_rejects_an_orphan_signing_setting() -> None:
    result = admit_runtime_settings(
        {
            "CI_COORDINATOR_RUNTIME_MODE": "disabled",
            "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
            "CI_COORDINATOR_BIND_PORT": "8080",
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
            "CI_COORDINATOR_PLAN_SIGNING_KEY_ID": "plan-key",
        }
    )

    assert result == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_PLAN_SIGNING_KEY_ID",
    )


def test_runtime_settings_reject_raw_secrets_and_invalid_optional_scalars() -> None:
    settings = admit_runtime_settings(_non_enforcing_mapping())
    assert isinstance(settings, NonEnforcingRuntimeSettings)
    with pytest.raises(ValueError, match="webhook secret"):
        replace(
            settings,
            webhook_secret="not-a-secret-value",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="webhook secret"):
        replace(settings, webhook_secret=SecretValue("short"))
    with pytest.raises(ValueError, match="break-glass bearer token"):
        replace(settings, break_glass_bearer_token=SecretValue("short"))
    with pytest.raises(ValueError, match="break-glass bearer token"):
        replace(settings, break_glass_bearer_token=SecretValue("x" * 31 + "\n"))
    with pytest.raises(ValueError, match="distinct from every credential secret"):
        replace(settings, metrics_bearer_token=settings.break_glass_bearer_token)
    for github_app_id in ("", " app-id "):
        with pytest.raises(ValueError, match="GitHub app id"):
            replace(settings, github_app_id=github_app_id)
    with pytest.raises(ValueError, match="outbound proxy URL"):
        replace(settings, outbound_proxy_url="http://proxy.example.test:3128/")
    with pytest.raises(ValueError, match="OIDC workflow"):
        replace(
            settings,
            oidc_allowed_workflow_refs=("workflow",),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="OIDC workflow"):
        replace(
            settings,
            oidc_allowed_job_workflow_refs=("job-workflow",),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="OIDC audience"):
        replace(settings, oidc_audience=" ci-coordinator ")
    with pytest.raises(ValueError, match="OIDC workflow"):
        replace(settings, oidc_allowed_workflow_refs=frozenset({" workflow "}))
    with pytest.raises(ValueError, match="OIDC workflow paths"):
        replace(
            settings,
            oidc_allowed_workflow_paths=frozenset(
                {"example/sample-service/.github/workflows/nested/full-check.yml"}
            ),
        )
    with pytest.raises(ValueError, match="at least one OIDC workflow"):
        replace(
            settings,
            oidc_allowed_workflow_refs=frozenset(),
            oidc_allowed_job_workflow_refs=frozenset(),
        )
    with pytest.raises(ValueError, match="control-plane scopes"):
        replace(settings, control_plane_scope_allowlist=frozenset())
    with pytest.raises(ValueError, match="break-glass bearer token"):
        replace(
            settings,
            break_glass_bearer_token=SecretValue("x" * 4_097),
        )
    with pytest.raises(ValueError, match="secret value"):
        SecretValue("secret\x00value")


@pytest.mark.parametrize(
    "field_name",
    [
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS",
        "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS",
    ],
)
def test_non_enforcing_mode_rejects_empty_oidc_csv_members(field_name: str) -> None:
    mapping = _non_enforcing_mapping()
    mapping[field_name] = "workflow-a,,workflow-b"

    result = admit_runtime_settings(mapping)

    assert result == RuntimeSettingsRejection(
        "invalid_setting_value",
        field_name,
    )


def test_non_enforcing_mode_admits_either_oidc_claim_namespace_without_merging_them() -> None:
    mapping = _non_enforcing_mapping()
    del mapping["CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS"]

    result = admit_runtime_settings(mapping)

    assert isinstance(result, NonEnforcingRuntimeSettings)
    assert result.oidc_allowed_workflow_refs == frozenset()
    assert result.oidc_allowed_job_workflow_refs == frozenset({"job-workflow-a"})


def test_non_enforcing_mode_admits_empty_optional_oidc_namespaces() -> None:
    mapping = _non_enforcing_mapping()
    mapping["CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_PATHS"] = ""
    mapping["CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_PATHS"] = ""

    result = admit_runtime_settings(mapping)

    assert isinstance(result, NonEnforcingRuntimeSettings)
    assert result.oidc_allowed_workflow_paths == frozenset()
    assert result.oidc_allowed_job_workflow_paths == frozenset()


def test_non_enforcing_mode_rejects_four_empty_oidc_namespaces() -> None:
    mapping = _non_enforcing_mapping()
    for field_name in (
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS",
        "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_PATHS",
        "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_PATHS",
    ):
        mapping[field_name] = ""

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "missing_required_setting",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS",
    )


def test_non_enforcing_mode_admits_an_exact_workflow_path_without_a_fixed_ref() -> None:
    mapping = _non_enforcing_mapping()
    del mapping["CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS"]
    del mapping["CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS"]
    mapping["CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_PATHS"] = (
        "example/sample-service/.github/workflows/full-check.yml"
    )

    result = admit_runtime_settings(mapping)

    assert isinstance(result, NonEnforcingRuntimeSettings)
    assert result.oidc_allowed_workflow_paths == frozenset(
        {"example/sample-service/.github/workflows/full-check.yml"}
    )
    assert result.oidc_allowed_job_workflow_paths == frozenset()


@pytest.mark.parametrize(
    "value",
    [
        "example/sample-service/.github/workflows/nested/full-check.yml",
        "example/sample-service/workflows/full-check.yml",
        "example@evil/sample-service/.github/workflows/full-check.yml",
        "example/sample-service/.github/workflows/full-check.txt",
    ],
    ids=["nested", "wrong-root", "invalid-owner", "wrong-extension"],
)
def test_non_enforcing_mode_rejects_invalid_workflow_path_identity(value: str) -> None:
    mapping = _non_enforcing_mapping()
    mapping["CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_PATHS"] = value

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_PATHS",
    )


def test_non_enforcing_mode_requires_at_least_one_oidc_claim_namespace() -> None:
    mapping = _non_enforcing_mapping()
    del mapping["CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS"]
    del mapping["CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS"]

    result = admit_runtime_settings(mapping)

    assert result == RuntimeSettingsRejection(
        "missing_required_setting",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS",
    )


def test_non_enforcing_mode_requires_an_oidc_audience() -> None:
    mapping = _non_enforcing_mapping()
    del mapping["CI_COORDINATOR_OIDC_AUDIENCE"]

    result = admit_runtime_settings(mapping)

    assert result == RuntimeSettingsRejection(
        "missing_required_setting",
        "CI_COORDINATOR_OIDC_AUDIENCE",
    )


@pytest.mark.parametrize("scopes", [None, "", "101:501"])
def test_app_inventory_bootstraps_without_inferred_command_grants(scopes: str | None) -> None:
    mapping = _non_enforcing_mapping()
    mapping["CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE"] = "app"
    if scopes is None:
        mapping.pop("CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST")
    else:
        mapping["CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST"] = scopes
    result = admit_runtime_settings(mapping)
    assert isinstance(result, NonEnforcingRuntimeSettings)
    assert result.control_plane_inventory_mode == "app"
    assert result.control_plane_scope_mode == "restricted"
    assert result.control_plane_scope_allowlist == (frozenset({scopes}) if scopes else frozenset())
    assert redacted_settings_projection(result).control_plane_inventory_mode == "app"


@pytest.mark.parametrize("runtime_mode", ["non_enforcing", "enforcing"])
@pytest.mark.parametrize("inventory_mode", ["restricted", "app"])
@pytest.mark.parametrize("scope_mode", ["restricted", "app", "automatic", ""])
def test_scope_mode_is_explicit_and_requires_matching_inventory(
    runtime_mode: str, inventory_mode: str, scope_mode: str
) -> None:
    mapping = _non_enforcing_mapping() if runtime_mode == "non_enforcing" else _enforcing_mapping()
    mapping["CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE"] = inventory_mode
    mapping["CI_COORDINATOR_CONTROL_PLANE_SCOPE_MODE"] = scope_mode
    result = admit_runtime_settings(mapping)
    if scope_mode == "restricted" or (scope_mode == "app" and inventory_mode == "app"):
        assert isinstance(result, NonEnforcingRuntimeSettings | EnforcingRuntimeSettings)
        assert result.control_plane_scope_mode == scope_mode
        assert redacted_settings_projection(result).control_plane_scope_mode == scope_mode
    else:
        assert isinstance(result, RuntimeSettingsRejection)
        assert result.field_name == "CI_COORDINATOR_CONTROL_PLANE_SCOPE_MODE"


def test_direct_settings_cannot_bypass_scope_mode_admission() -> None:
    settings = admit_runtime_settings(_non_enforcing_mapping())
    assert isinstance(settings, NonEnforcingRuntimeSettings)
    with pytest.raises(ValueError):
        replace(settings, control_plane_scope_mode="app")


@pytest.mark.parametrize("mode,ids", [("automatic", ""), ("app", "101")])
def test_inventory_mode_cannot_silently_broaden_restricted_authority(mode: str, ids: str) -> None:
    mapping = _non_enforcing_mapping()
    mapping["CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE"] = mode
    mapping["CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST"] = ids
    assert isinstance(admit_runtime_settings(mapping), RuntimeSettingsRejection)


def test_provider_inventory_installation_grant_is_optional_bounded_and_redacted() -> None:
    mapping = _non_enforcing_mapping()
    mapping["CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST"] = "101,202"

    result = admit_runtime_settings(mapping)

    assert isinstance(result, NonEnforcingRuntimeSettings)
    assert result.control_plane_inventory_installation_allowlist == frozenset({101, 202})
    assert redacted_settings_projection(result).control_plane_inventory_installation_count == 2


@pytest.mark.parametrize("mode", ["app", "restricted"])
@pytest.mark.parametrize("scopes", [None, ""])
def test_enforcing_inventory_never_bootstraps_without_command_scopes(
    mode: str, scopes: str | None
) -> None:
    mapping = _enforcing_mapping()
    mapping["CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE"] = mode
    if scopes is None:
        mapping.pop("CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST")
    else:
        mapping["CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST"] = scopes
    result = admit_runtime_settings(mapping)
    assert isinstance(result, RuntimeSettingsRejection)
    assert result.field_name == "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST"


@pytest.mark.parametrize("profile_id", ["A" * 64, "a" * 63, "g" * 64])
def test_non_enforcing_mode_requires_a_canonical_rollout_profile_id(
    profile_id: str,
) -> None:
    mapping = _non_enforcing_mapping()
    mapping["CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID"] = profile_id

    result = admit_runtime_settings(mapping)

    assert result == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID",
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("CI_COORDINATOR_BIND_PORT", "0"),
        ("CI_COORDINATOR_BIND_PORT", "65536"),
        ("CI_COORDINATOR_BIND_PORT", "\u0661"),
        ("CI_COORDINATOR_BIND_PORT", "9" * 10_000),
        ("CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS", "3601"),
    ],
)
def test_common_setting_rejections_name_the_exact_invalid_field(
    field_name: str,
    value: str,
) -> None:
    mapping = {
        "CI_COORDINATOR_RUNTIME_MODE": "disabled",
        "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
        "CI_COORDINATOR_BIND_PORT": "8080",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
    }
    mapping[field_name] = value

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value",
        field_name,
    )


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",  # noqa: S104 - explicit wildcard listener admission
        "127.0.0.1",
        "::",
        "::1",
        "2001:db8::1",
        "localhost",
        "api.example.test",
    ],
)
def test_bind_host_admits_only_canonical_listener_identities(host: str) -> None:
    mapping = {
        "CI_COORDINATOR_RUNTIME_MODE": "disabled",
        "CI_COORDINATOR_BIND_HOST": host,
        "CI_COORDINATOR_BIND_PORT": "8080",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
    }

    result = admit_runtime_settings(mapping)

    assert isinstance(result, DisabledRuntimeSettings)
    assert result.bind_host == host


@pytest.mark.parametrize(
    "host",
    [
        "bad host",
        "https://example.test",
        "[::1]",
        "fe80::1%eth0",
        "UPPER.example.test",
        "under_score.example.test",
        "example.test.",
        "127.0.0.01",
        "example.test\n",
    ],
)
def test_bind_host_rejects_ambiguous_or_noncanonical_values(host: str) -> None:
    mapping = {
        "CI_COORDINATOR_RUNTIME_MODE": "disabled",
        "CI_COORDINATOR_BIND_HOST": host,
        "CI_COORDINATOR_BIND_PORT": "8080",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
    }

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_BIND_HOST",
    )


def test_settings_constructor_rejects_the_normalizer_failure_sentinel() -> None:
    with pytest.raises(ValueError, match="canonical IP address or DNS name"):
        DisabledRuntimeSettings(
            bind_host=cast(str, None),
            bind_port=8080,
            shutdown_timeout_seconds=30,
        )


@pytest.mark.parametrize(
    ("mode", "field_name"),
    [
        ("disabled", "CI_COORDINATOR_DATABASE_DSN"),
        ("non_enforcing", "CI_COORDINATOR_ENVIRONMENT_ID"),
        ("enforcing", "CI_COORDINATOR_DATABASE_DSNN"),
    ],
)
def test_runtime_modes_reject_irrelevant_or_unknown_owned_settings(
    mode: str,
    field_name: str,
) -> None:
    if mode == "disabled":
        mapping = {
            "CI_COORDINATOR_RUNTIME_MODE": "disabled",
            "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
            "CI_COORDINATOR_BIND_PORT": "8080",
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        }
    elif mode == "non_enforcing":
        mapping = _non_enforcing_mapping()
    else:
        mapping = _enforcing_mapping()
    mapping[field_name] = "supplied"

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value",
        field_name,
    )


def test_runtime_admission_ignores_foreign_environment_keys() -> None:
    mapping = {
        "CI_COORDINATOR_RUNTIME_MODE": "disabled",
        "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
        "CI_COORDINATOR_BIND_PORT": "8080",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        "PATH": "/usr/bin",
    }

    assert isinstance(admit_runtime_settings(mapping), DisabledRuntimeSettings)


def test_runtime_admission_snapshots_each_owned_value_once() -> None:
    class SingleReadMapping(Mapping[str, str]):
        def __init__(self) -> None:
            self.data = {
                "CI_COORDINATOR_RUNTIME_MODE": "disabled",
                "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
                "CI_COORDINATOR_BIND_PORT": "8080",
                "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
            }
            self.reads: dict[str, int] = {}

        def __getitem__(self, key: str) -> str:
            self.reads[key] = self.reads.get(key, 0) + 1
            if self.reads[key] > 1:
                raise AssertionError(f"runtime setting was read twice: {key}")
            return self.data[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self.data)

        def __len__(self) -> int:
            return len(self.data)

    mapping = SingleReadMapping()

    assert isinstance(admit_runtime_settings(mapping), DisabledRuntimeSettings)
    assert mapping.reads == {name: 1 for name in mapping.data}


def test_admission_failure_preserves_first_field_and_does_not_swallow_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ci_coordinator.runtime_settings.admission as admission

    mapping = _non_enforcing_mapping()
    mapping["CI_COORDINATOR_BIND_HOST"] = "invalid host"
    mapping["CI_COORDINATOR_BIND_PORT"] = "invalid port"
    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value", "CI_COORDINATOR_BIND_HOST"
    )
    unexpected = ValueError("unexpected normalization failure")

    def fail(_value: str) -> str | None:
        raise unexpected

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(admission, "normalize_bind_host", fail)
    with pytest.raises(ValueError) as caught:
        admit_runtime_settings(_non_enforcing_mapping())
    assert caught.value is unexpected


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("CI_COORDINATOR_REQUEST_TIMEOUT_SECONDS", "3601"),
        ("CI_COORDINATOR_MAXIMUM_RETAINED_BODY_BYTES", "1073741825"),
        ("CI_COORDINATOR_DATABASE_POOL_SIZE", "129"),
        ("CI_COORDINATOR_DATABASE_POOL_TIMEOUT_SECONDS", "3601"),
        ("CI_COORDINATOR_RECONCILIATION_SCAN_LIMIT", "1001"),
        ("CI_COORDINATOR_GITHUB_APP_ID", " app-id "),
        ("CI_COORDINATOR_PLAN_SIGNING_KEY_ID", "k" * 256),
        ("CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS", "w" * 513),
        ("CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS", "w" * 513),
        ("CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST", "0:1"),
        ("CI_COORDINATOR_WEBHOOK_SECRET", "s" * 65_537),
        ("CI_COORDINATOR_WEBHOOK_SECRET", "s" * 32 + "\x00"),
        ("CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN", "s" * 4_097),
        ("CI_COORDINATOR_METRICS_BEARER_TOKEN", "s" * 31),
        ("CI_COORDINATOR_METRICS_BEARER_TOKEN", "s" * 31 + "\n"),
        ("CI_COORDINATOR_METRICS_BEARER_TOKEN", "s" * 4_097),
        ("CI_COORDINATOR_WEBHOOK_SECRET", "\ud800"),
        ("CI_COORDINATOR_BREAK_GLASS_ACTOR_ID", "\ud800"),
        ("CI_COORDINATOR_BREAK_GLASS_ACTOR_ID", "github-user-id:123"),
        ("CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS", ",".join(["workflow"] * 65)),
        ("CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS", ",".join(["workflow"] * 65)),
        ("CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST", ",".join(["1:2"] * 1_025)),
        ("CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST", "0"),
        ("CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST", "01"),
        ("CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST", "1,1"),
        ("CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST", "1,01"),
        (
            "CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST",
            ",".join(str(value) for value in range(1, 66)),
        ),
    ],
)
def test_non_enforcing_rejections_name_the_exact_invalid_field(
    field_name: str,
    value: str,
) -> None:
    mapping = _non_enforcing_mapping()
    mapping[field_name] = value

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value",
        field_name,
    )


@pytest.mark.parametrize(
    "reused_field",
    [
        "CI_COORDINATOR_DATABASE_DSN",
        "CI_COORDINATOR_GITHUB_PRIVATE_KEY",
        "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN",
        "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY",
        "CI_COORDINATOR_WEBHOOK_SECRET",
        "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET",
        "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY",
        "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET",
    ],
)
def test_metrics_bearer_cannot_reuse_an_interactive_credential(reused_field: str) -> None:
    mapping = _non_enforcing_mapping()
    mapping.update(_control_plane_identity_mapping("https://ci.example.test"))
    reused_value = (
        "A" * 43 if reused_field == "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY" else "m" * 32
    )
    mapping[reused_field] = reused_value
    mapping["CI_COORDINATOR_METRICS_BEARER_TOKEN"] = reused_value

    assert admit_runtime_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_METRICS_BEARER_TOKEN",
    )


@pytest.mark.parametrize(
    ("field_name", "value", "admitted"),
    [
        ("CI_COORDINATOR_WEBHOOK_SECRET", "s" * 31, False),
        ("CI_COORDINATOR_WEBHOOK_SECRET", "s" * 32, True),
        ("CI_COORDINATOR_WEBHOOK_SECRET", "\u00e9" * 16, True),
        ("CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN", "s" * 31, False),
        ("CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN", "s" * 32, True),
        ("CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN", "\u00e9" * 16, False),
    ],
)
def test_authentication_secrets_follow_owner_specific_byte_profiles(
    field_name: str,
    value: str,
    admitted: bool,
) -> None:
    mapping = _non_enforcing_mapping()
    mapping[field_name] = value

    result = admit_runtime_settings(mapping)

    if admitted:
        assert isinstance(result, NonEnforcingRuntimeSettings)
    else:
        assert result == RuntimeSettingsRejection("invalid_setting_value", field_name)


def test_disabled_mode_is_health_only_and_does_not_need_external_dependencies() -> None:
    result = admit_runtime_settings(
        {
            "CI_COORDINATOR_RUNTIME_MODE": "disabled",
            "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
            "CI_COORDINATOR_BIND_PORT": "8080",
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        }
    )

    assert isinstance(result, DisabledRuntimeSettings)
    assert not hasattr(result, "database_dsn")
    assert not hasattr(result, "plan_signing_private_key")
    assert redacted_settings_projection(result).control_plane_identity_mode == "disabled"


def _non_enforcing_mapping() -> dict[str, str]:
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
        "CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID": "a" * 64,
        "CI_COORDINATOR_DATABASE_DSN": "database-dsn",
        "CI_COORDINATOR_WEBHOOK_SECRET": "w" * 32,
        "CI_COORDINATOR_GITHUB_APP_ID": "1234",
        "CI_COORDINATOR_GITHUB_PRIVATE_KEY": "github-private-key",
        "CI_COORDINATOR_PLAN_SIGNING_KEY_ID": "plan-key",
        "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY": "plan-private-key",
        "CI_COORDINATOR_OIDC_AUDIENCE": "ci-coordinator",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS": "workflow-a,workflow-b",
        "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS": "job-workflow-a",
        "CI_COORDINATOR_BREAK_GLASS_ACTOR_ID": "break-glass:v1:local-development",
        "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN": "b" * 32,
        "CI_COORDINATOR_METRICS_BEARER_TOKEN": "m" * 32,
        "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST": "100:200,100:201",
    }


def _enforcing_mapping() -> dict[str, str]:
    mapping = _non_enforcing_mapping()
    mapping.update(
        {
            "CI_COORDINATOR_RUNTIME_MODE": "enforcing",
            "CI_COORDINATOR_PRODUCTION_ADMISSION_RECEIPT_PATH": (
                "/var/run/ci-coordinator/production-admission.json"
            ),
            "CI_COORDINATOR_PRODUCTION_ADMISSION_KEY_ID": "production-key",
            "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM": "production-public-key",
            "CI_COORDINATOR_DEPLOYED_ARTIFACT_DIGEST": "sha256:" + "b" * 64,
            "CI_COORDINATOR_ENVIRONMENT_ID": "production",
            "CI_COORDINATOR_ENFORCEMENT_SCOPE_ALLOWLIST": "100:200",
        }
    )
    return mapping


def _control_plane_identity_mapping(public_origin: str) -> dict[str, str]:
    return {
        "CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE": "keycloak",
        "CI_COORDINATOR_KEYCLOAK_ISSUER": ("https://auth.example.test/realms/coordinator"),
        "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID": "ci-coordinator-admin-ui",
        "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET": "k" * 32,
        "CI_COORDINATOR_KEYCLOAK_API_CLIENT_ID": "ci-coordinator-admin-api",
        "CI_COORDINATOR_PUBLIC_ORIGIN": public_origin,
        "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY": "A" * 43,
        "CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS": "900",
        "CI_COORDINATOR_GITHUB_APP_CLIENT_ID": "Iv1SyntheticClient01",
        "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET": "r" * 32,
    }
