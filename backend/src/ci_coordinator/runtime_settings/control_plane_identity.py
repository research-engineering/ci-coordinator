"""Bounded process-input admission for the organization control plane."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ci_coordinator.kernel import hash_object
from ci_coordinator.runtime_settings._rejection import SettingsRejected
from ci_coordinator.runtime_settings.contracts import (
    ControlPlaneIdentitySettings,
    RuntimeSettingsRejection,
    SecretValue,
    is_authentication_secret_text,
    is_browser_public_origin,
    is_browser_session_key,
    is_oidc_issuer_url,
)

CONTROL_PLANE_BROWSER_CLIENT_ID = "ci-coordinator-admin-ui"
CONTROL_PLANE_API_CLIENT_ID = "ci-coordinator-admin-api"
CONTROL_PLANE_SIGNING_ALGORITHMS = ("RS256",)
_PREFIX = "CI_COORDINATOR_"
_MODE = f"{_PREFIX}CONTROL_PLANE_AUTH_MODE"
_ISSUER = f"{_PREFIX}KEYCLOAK_ISSUER"
_BROWSER_CLIENT_ID = f"{_PREFIX}KEYCLOAK_BROWSER_CLIENT_ID"
_BROWSER_CLIENT_SECRET = f"{_PREFIX}KEYCLOAK_BROWSER_CLIENT_SECRET"
_API_CLIENT_ID = f"{_PREFIX}KEYCLOAK_API_CLIENT_ID"
_PUBLIC_ORIGIN = f"{_PREFIX}PUBLIC_ORIGIN"
_SESSION_KEY = f"{_PREFIX}CONTROL_PLANE_SESSION_KEY"
_SESSION_MAXIMUM = f"{_PREFIX}CONTROL_PLANE_SESSION_MAXIMUM_SECONDS"
_WORKLOAD_CLIENTS = f"{_PREFIX}KEYCLOAK_WORKLOAD_CLIENT_ALLOWLIST"
_GITHUB_REVIEWER_CLIENT_ID = f"{_PREFIX}GITHUB_APP_CLIENT_ID"
_GITHUB_REVIEWER_CLIENT_SECRET = f"{_PREFIX}GITHUB_APP_CLIENT_SECRET"
CONTROL_PLANE_IDENTITY_FIELDS = frozenset(
    {
        _MODE,
        _ISSUER,
        _BROWSER_CLIENT_ID,
        _BROWSER_CLIENT_SECRET,
        _API_CLIENT_ID,
        _PUBLIC_ORIGIN,
        _SESSION_KEY,
        _SESSION_MAXIMUM,
        _WORKLOAD_CLIENTS,
        _GITHUB_REVIEWER_CLIENT_ID,
        _GITHUB_REVIEWER_CLIENT_SECRET,
    }
)
_IDENTIFIER = re.compile("[A-Za-z0-9._-]{1,255}")
_MAX_WORKLOAD_CLIENTS = 128


def admit_control_plane_identity_settings(
    mapping: Mapping[str, str],
) -> ControlPlaneIdentitySettings | RuntimeSettingsRejection | None:
    try:
        return _admit_control_plane_identity_settings(mapping)
    except SettingsRejected as failure:
        return failure.rejection


def _admit_control_plane_identity_settings(
    mapping: Mapping[str, str],
) -> ControlPlaneIdentitySettings | None:
    mode = mapping.get(_MODE)
    configured = tuple(name for name in CONTROL_PLANE_IDENTITY_FIELDS - {_MODE} if name in mapping)
    if mode is None:
        if configured:
            raise SettingsRejected(
                RuntimeSettingsRejection("missing_required_setting", _MODE)
            ) from None
        return None
    if mode == "disabled":
        if configured:
            raise SettingsRejected(
                RuntimeSettingsRejection("invalid_setting_value", _MODE)
            ) from None
        return None
    if mode != "keycloak":
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", _MODE)) from None
    issuer = _required(mapping, _ISSUER, maximum_bytes=512)
    if not is_oidc_issuer_url(issuer):
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", _ISSUER)) from None
    browser_client_id = _exact_required(
        mapping, _BROWSER_CLIENT_ID, CONTROL_PLANE_BROWSER_CLIENT_ID
    )
    api_client_id = _exact_required(mapping, _API_CLIENT_ID, CONTROL_PLANE_API_CLIENT_ID)
    reviewer_client_id = _required(mapping, _GITHUB_REVIEWER_CLIENT_ID, maximum_bytes=255)
    if _IDENTIFIER.fullmatch(reviewer_client_id) is None:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _GITHUB_REVIEWER_CLIENT_ID)
        ) from None
    public_origin = _required(mapping, _PUBLIC_ORIGIN, maximum_bytes=512)
    if not is_browser_public_origin(public_origin):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _PUBLIC_ORIGIN)
        ) from None
    session_maximum = _positive_integer(mapping, _SESSION_MAXIMUM, maximum=900)
    if session_maximum < 60:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _SESSION_MAXIMUM)
        ) from None
    workload_clients = _workload_clients(mapping)
    browser_secret = _secret(mapping, _BROWSER_CLIENT_SECRET, maximum_bytes=4096)
    session_key = _secret(mapping, _SESSION_KEY, maximum_bytes=64)
    if not is_browser_session_key(session_key):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _SESSION_KEY)
        ) from None
    reviewer_secret = _secret(mapping, _GITHUB_REVIEWER_CLIENT_SECRET, maximum_bytes=4096)
    if len({browser_secret, session_key, reviewer_secret}) != 3:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _SESSION_KEY)
        ) from None
    profile_digest = hash_object(
        {
            "apiClientId": api_client_id,
            "browserClientId": browser_client_id,
            "githubReviewerClientId": reviewer_client_id,
            "issuer": issuer,
            "maximumSessionSeconds": session_maximum,
            "publicOrigin": public_origin,
            "signingAlgorithms": list(CONTROL_PLANE_SIGNING_ALGORITHMS),
            "workloadClientIds": sorted(workload_clients),
        }
    )
    return ControlPlaneIdentitySettings(
        issuer=issuer,
        browser_client_id=browser_client_id,
        browser_client_secret=SecretValue(browser_secret),
        api_client_id=api_client_id,
        public_origin=public_origin,
        session_key=SecretValue(session_key),
        maximum_session_seconds=session_maximum,
        signing_algorithms=CONTROL_PLANE_SIGNING_ALGORITHMS,
        workload_client_ids=workload_clients,
        github_reviewer_client_id=reviewer_client_id,
        github_reviewer_client_secret=SecretValue(reviewer_secret),
        profile_digest=profile_digest,
    )


def _exact_required(mapping: Mapping[str, str], name: str, expected: str) -> str:
    value = _required(mapping, name, maximum_bytes=512)
    if value != expected:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _required(mapping: Mapping[str, str], name: str, *, maximum_bytes: int) -> str:
    value = mapping.get(name)
    if type(value) is not str or not value or value.isspace():
        raise SettingsRejected(RuntimeSettingsRejection("missing_required_setting", name)) from None
    if value != value.strip():
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    if len(encoded) > maximum_bytes or b"\x00" in encoded:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _secret(mapping: Mapping[str, str], name: str, *, maximum_bytes: int) -> str:
    value = _required(mapping, name, maximum_bytes=maximum_bytes)
    if not is_authentication_secret_text(value, maximum_utf8_bytes=maximum_bytes):
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _positive_integer(mapping: Mapping[str, str], name: str, *, maximum: int) -> int:
    value = _required(mapping, name, maximum_bytes=16)
    if not value.isascii() or not value.isdecimal() or value.startswith("0"):
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    parsed = int(value)
    if not 1 <= parsed <= maximum:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return parsed


def _workload_clients(mapping: Mapping[str, str]) -> frozenset[str]:
    value = mapping.get(_WORKLOAD_CLIENTS)
    if value is None or value == "":
        return frozenset()
    if type(value) is not str or value != value.strip():
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _WORKLOAD_CLIENTS)
        ) from None
    items = value.split(",")
    if (
        not 1 <= len(items) <= _MAX_WORKLOAD_CLIENTS
        or len(set(items)) != len(items)
        or items != sorted(items)
        or any(_IDENTIFIER.fullmatch(item) is None for item in items)
    ):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _WORKLOAD_CLIENTS)
        ) from None
    return frozenset(items)
