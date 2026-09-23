from __future__ import annotations

import pytest

from ci_coordinator.runtime_settings.contracts import (
    ControlPlaneIdentitySettings,
    RuntimeSettingsRejection,
    is_oidc_issuer_url,
)
from ci_coordinator.runtime_settings.control_plane_identity import (
    CONTROL_PLANE_API_CLIENT_ID,
    CONTROL_PLANE_BROWSER_CLIENT_ID,
    admit_control_plane_identity_settings,
)

_TEST_ISSUER = "https://auth.example.test/realms/coordinator"
_TEST_GITHUB_CLIENT_ID = "Iv1SyntheticClient01"


def test_configured_control_plane_profile_is_admitted_without_exposing_secrets() -> None:
    result = admit_control_plane_identity_settings(_identity_mapping())

    assert isinstance(result, ControlPlaneIdentitySettings)
    assert result.issuer == _TEST_ISSUER
    assert result.github_reviewer_client_id == _TEST_GITHUB_CLIENT_ID
    assert result.browser_client_id == CONTROL_PLANE_BROWSER_CLIENT_ID
    assert result.api_client_id == CONTROL_PLANE_API_CLIENT_ID
    assert result.signing_algorithms == ("RS256",)
    assert result.callback_uri == "https://ci.example.test/api/v1/auth/keycloak/callback"
    assert result.workload_client_ids == frozenset({"review-bot", "release-automation"})
    assert "browser-secret" not in repr(result)
    assert "reviewer-secret" not in repr(result)


@pytest.mark.parametrize(
    "issuer",
    [
        "https://identity.example.test",
        "https://identity.example.test/realms/Team-A",
        "https://identity.example.test:8443/realms/coordinator",
        "https://identity.example.test/" + "r" * (512 - len("https://identity.example.test/")),
    ],
)
def test_portable_issuer_is_preserved_and_bound_to_profile(issuer: str) -> None:
    mapping = _identity_mapping()
    original = admit_control_plane_identity_settings(mapping)
    mapping["CI_COORDINATOR_KEYCLOAK_ISSUER"] = issuer

    result = admit_control_plane_identity_settings(mapping)

    assert isinstance(original, ControlPlaneIdentitySettings)
    assert isinstance(result, ControlPlaneIdentitySettings)
    assert result.issuer == issuer
    assert result.github_reviewer_client_id == original.github_reviewer_client_id
    assert result.profile_digest != original.profile_digest


@pytest.mark.parametrize("client_id", ["a", "deployment.client-01_OSS", "A" * 255])
def test_portable_reviewer_client_id_is_preserved_and_bound_to_profile(client_id: str) -> None:
    mapping = _identity_mapping()
    original = admit_control_plane_identity_settings(mapping)
    mapping["CI_COORDINATOR_GITHUB_APP_CLIENT_ID"] = client_id

    result = admit_control_plane_identity_settings(mapping)

    assert isinstance(original, ControlPlaneIdentitySettings)
    assert isinstance(result, ControlPlaneIdentitySettings)
    assert result.github_reviewer_client_id == client_id
    assert result.issuer == original.issuer
    assert result.profile_digest != original.profile_digest


@pytest.mark.parametrize(
    "field_name",
    ["CI_COORDINATOR_KEYCLOAK_ISSUER", "CI_COORDINATOR_GITHUB_APP_CLIENT_ID"],
)
@pytest.mark.parametrize("value", [None, "", " ", "\t"])
def test_portable_identity_requires_explicit_deployment_values(
    field_name: str, value: str | None
) -> None:
    mapping = _identity_mapping()
    if value is None:
        del mapping[field_name]
    else:
        mapping[field_name] = value

    assert admit_control_plane_identity_settings(mapping) == RuntimeSettingsRejection(
        "missing_required_setting", field_name
    )


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE": "disabled"},
    ],
)
def test_absent_or_explicitly_disabled_profile_has_no_identity_authority(
    mapping: dict[str, str],
) -> None:
    assert admit_control_plane_identity_settings(mapping) is None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE", "github"),
        ("CI_COORDINATOR_KEYCLOAK_ISSUER", "http://auth.example.test/realms/coordinator"),
        ("CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID", "other-ui"),
        ("CI_COORDINATOR_KEYCLOAK_API_CLIENT_ID", "other-api"),
        ("CI_COORDINATOR_GITHUB_APP_CLIENT_ID", "invalid/github-app"),
        ("CI_COORDINATOR_PUBLIC_ORIGIN", "https://ci.example.test/with-path"),
        ("CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS", "901"),
        ("CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS", "059"),
        ("CI_COORDINATOR_KEYCLOAK_WORKLOAD_CLIENT_ALLOWLIST", "review-bot,review-bot"),
        (
            "CI_COORDINATOR_KEYCLOAK_WORKLOAD_CLIENT_ALLOWLIST",
            "review-bot,release-automation",
        ),
        ("CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET", "too-short"),
        ("CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET", "too-short"),
        ("CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY", "A" * 42),
    ],
)
def test_each_profile_coordinate_fails_closed(field_name: str, value: str) -> None:
    mapping = _identity_mapping()
    mapping[field_name] = value

    result = admit_control_plane_identity_settings(mapping)

    assert result == RuntimeSettingsRejection("invalid_setting_value", field_name)


@pytest.mark.parametrize(
    "issuer",
    [
        "http://localhost/realms/coordinator",
        "https://AUTH.example.test/realms/coordinator",
        "https://auth.example.test:443/realms/coordinator",
        "https://auth.example.test:0/realms/coordinator",
        "https://auth.example.test:65536/realms/coordinator",
        "https://user@auth.example.test/realms/coordinator",
        "https://user:password@auth.example.test/realms/coordinator",
        _TEST_ISSUER + "/",
        _TEST_ISSUER + "?tenant=other",
        _TEST_ISSUER + "?",
        _TEST_ISSUER + "#fragment",
        _TEST_ISSUER + "#",
        " " + _TEST_ISSUER,
        _TEST_ISSUER + "\n",
        _TEST_ISSUER + "/some realm",
        _TEST_ISSUER + "/some\trealm",
        _TEST_ISSUER + "/some\x00realm",
        "https://auth.example.test/realms/\u00e9quipe",
        "https://auth.example.test/" + "r" * 512,
    ],
)
def test_portable_issuer_rejects_noncanonical_or_unsafe_urls(issuer: str) -> None:
    mapping = _identity_mapping()
    mapping["CI_COORDINATOR_KEYCLOAK_ISSUER"] = issuer

    assert admit_control_plane_identity_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value", "CI_COORDINATOR_KEYCLOAK_ISSUER"
    )


@pytest.mark.parametrize(
    "client_id",
    [
        " " + _TEST_GITHUB_CLIENT_ID,
        _TEST_GITHUB_CLIENT_ID + "\n",
        "deployment client",
        "deployment\tclient",
        "deployment\x00client",
        "deployment/client",
        "deployment?client",
        "deployment\u00e9client",
        "A" * 256,
    ],
)
def test_portable_reviewer_client_id_rejects_invalid_identifiers(client_id: str) -> None:
    mapping = _identity_mapping()
    mapping["CI_COORDINATOR_GITHUB_APP_CLIENT_ID"] = client_id

    assert admit_control_plane_identity_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value", "CI_COORDINATOR_GITHUB_APP_CLIENT_ID"
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET",
        "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET",
    ],
)
def test_credentials_cannot_reuse_the_session_key(field_name: str) -> None:
    mapping = _identity_mapping()
    mapping[field_name] = mapping["CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY"]

    assert admit_control_plane_identity_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value",
        "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY",
    )


def test_partial_profile_requires_an_explicit_authentication_mode() -> None:
    assert admit_control_plane_identity_settings(
        {"CI_COORDINATOR_KEYCLOAK_ISSUER": _TEST_ISSUER}
    ) == RuntimeSettingsRejection(
        "missing_required_setting",
        "CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE",
    )


def test_identity_admission_preserves_first_failure_and_unexpected_exception_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ci_coordinator.runtime_settings.control_plane_identity as admission

    mapping = _identity_mapping()
    mapping["CI_COORDINATOR_KEYCLOAK_ISSUER"] = "wrong issuer"
    mapping["CI_COORDINATOR_PUBLIC_ORIGIN"] = "wrong origin"
    assert admit_control_plane_identity_settings(mapping) == RuntimeSettingsRejection(
        "invalid_setting_value", "CI_COORDINATOR_KEYCLOAK_ISSUER"
    )
    unexpected = ValueError("unexpected digest failure")

    def fail(_value: object) -> str:
        raise unexpected

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(admission, "hash_object", fail)
    with pytest.raises(ValueError) as caught:
        admit_control_plane_identity_settings(_identity_mapping())
    assert caught.value is unexpected


@pytest.mark.parametrize(
    ("value", "admitted"),
    [
        (_TEST_ISSUER, True),
        ("https://auth.example.test", True),
        ("https://auth.example.test/realms/coordinator/", False),
        ("https://user@auth.example.test/realms/coordinator", False),
        ("https://AUTH.example.test/realms/coordinator", False),
        ("http://auth.example.test/realms/coordinator", False),
        ("https://auth.example.test:443/realms/coordinator", False),
        ("https://auth.example.test/realms/coordinator?tenant=other", False),
    ],
)
def test_oidc_issuer_is_an_exact_canonical_https_identifier(value: str, admitted: bool) -> None:
    assert is_oidc_issuer_url(value) is admitted


def _identity_mapping() -> dict[str, str]:
    return {
        "CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE": "keycloak",
        "CI_COORDINATOR_KEYCLOAK_ISSUER": _TEST_ISSUER,
        "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID": CONTROL_PLANE_BROWSER_CLIENT_ID,
        "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET": "browser-secret-" + "b" * 32,
        "CI_COORDINATOR_KEYCLOAK_API_CLIENT_ID": CONTROL_PLANE_API_CLIENT_ID,
        "CI_COORDINATOR_PUBLIC_ORIGIN": "https://ci.example.test",
        "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY": "A" * 43,
        "CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS": "900",
        "CI_COORDINATOR_KEYCLOAK_WORKLOAD_CLIENT_ALLOWLIST": ("release-automation,review-bot"),
        "CI_COORDINATOR_GITHUB_APP_CLIENT_ID": _TEST_GITHUB_CLIENT_ID,
        "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET": "reviewer-secret-" + "r" * 32,
    }
