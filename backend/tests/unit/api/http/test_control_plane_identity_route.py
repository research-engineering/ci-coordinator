from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from control_plane_http_support import (
    NOW,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    ControlPlaneIdentityRouteDependencies,
    HttpRouteDependencies,
)
from ci_coordinator.api.http.routers.control_plane_identity import (
    CONTROL_PLANE_SESSION_PATH,
    KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
    KEYCLOAK_LOGIN_CALLBACK_PATH,
    KEYCLOAK_LOGIN_START_PATH,
    KEYCLOAK_LOGOUT_PATH,
)
from ci_coordinator.control_plane_identity import (
    CONTROL_PLANE_ROLES,
    BackChannelLogoutCompleted,
    BackChannelLogoutEvidence,
    BackChannelLogoutResult,
    BackChannelLogoutTarget,
    BackChannelLogoutTokenVerifier,
    BrowserAuthenticationResult,
    BrowserIdentityUseCase,
    BrowserLoginCompleted,
    BrowserLoginResult,
    BrowserLoginStart,
    BrowserLogoutCompleted,
    BrowserLogoutResult,
    IdentityRejected,
    IdentityUnavailable,
    KeycloakEvidenceRejected,
    KeycloakHumanPrincipal,
    KeycloakUnavailable,
)
from ci_coordinator.control_plane_identity.activity import (
    ActivityPrincipal,
    IdentityActivityObserver,
)

_PUBLIC_ORIGIN = "https://ci.example.test"
_ISSUER = "https://auth.example.test/realms/coordinator"
_SESSION_COOKIE = "__Host-ci_coordinator_session"
_LOGIN_COOKIE = "__Secure-ci_coordinator_login"
_CSRF = "c" * 43
_PRINCIPAL = human_principal()


def _logout_evidence() -> BackChannelLogoutEvidence:
    return BackChannelLogoutEvidence(
        issuer="https://auth.example.test/realms/coordinator",
        token_id="logout-17",
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        target=BackChannelLogoutTarget(subject="test-operator"),
    )


class _Identity:
    def __init__(self) -> None:
        self.start_result: BrowserLoginStart | IdentityUnavailable = BrowserLoginStart(
            "https://auth.example.test/authorize?state=opaque",
            "sealed-login",
            NOW + timedelta(minutes=5),
            300,
        )
        self.login_result: BrowserLoginResult = BrowserLoginCompleted(_PRINCIPAL)
        self.authentication_result: BrowserAuthenticationResult = _PRINCIPAL
        self.logout_result: BrowserLogoutResult = BrowserLogoutCompleted(
            "https://auth.example.test/logout"
        )
        self.back_channel_result: BackChannelLogoutResult = BackChannelLogoutCompleted(1)
        self.calls: list[tuple[object, ...]] = []

    def start_login(self) -> BrowserLoginStart | IdentityUnavailable:
        self.calls.append(("start",))
        return self.start_result

    async def complete_login(
        self,
        *,
        code: str | None,
        state: str | None,
        transaction_cookie: str | None,
        previous_session_handle: str | None,
    ) -> BrowserLoginResult:
        self.calls.append(("complete", code, state, transaction_cookie, previous_session_handle))
        return self.login_result

    async def authenticate(self, session_handle: str | None) -> BrowserAuthenticationResult:
        self.calls.append(("authenticate", session_handle))
        return self.authentication_result

    async def logout(self, session_handle: str | None) -> BrowserLogoutResult:
        self.calls.append(("logout", session_handle))
        return self.logout_result

    async def apply_back_channel_logout(
        self,
        evidence: BackChannelLogoutEvidence,
    ) -> BackChannelLogoutResult:
        self.calls.append(("back-channel", evidence))
        return self.back_channel_result

    def csrf_token(self, principal: KeycloakHumanPrincipal) -> str:
        assert principal is _PRINCIPAL
        return _CSRF

    def csrf_matches(self, principal: KeycloakHumanPrincipal, candidate: str | None) -> bool:
        return principal is _PRINCIPAL and candidate == _CSRF


class _FailingLoginIdentity(_Identity):
    async def complete_login(
        self,
        *,
        code: str | None,
        state: str | None,
        transaction_cookie: str | None,
        previous_session_handle: str | None,
    ) -> BrowserLoginResult:
        del code, state, transaction_cookie, previous_session_handle
        raise RuntimeError("unexpected login completion defect")


@dataclass
class _LogoutVerifier:
    result: BackChannelLogoutEvidence | Exception = field(
        default_factory=lambda: _logout_evidence()
    )
    calls: list[str] = field(default_factory=list)

    async def verify_back_channel_logout(self, token: str) -> BackChannelLogoutEvidence:
        self.calls.append(token)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("session_state", (None, "provider-session", "s" * 512))
@pytest.mark.parametrize("code_length", (1, 1_024))
def test_login_and_callback_project_only_opaque_cookie_authority(
    session_state: str | None,
    code_length: int,
) -> None:
    identity = _Identity()
    client = _client(identity)
    params = {"code": "c" * code_length, "state": "s" * 43, "iss": _ISSUER}
    if session_state is not None:
        params["session_state"] = session_state

    started = client.get(KEYCLOAK_LOGIN_START_PATH, follow_redirects=False)
    completed = client.get(
        KEYCLOAK_LOGIN_CALLBACK_PATH,
        params=params,
        follow_redirects=False,
    )

    assert (started.status_code, started.headers["location"]) == (
        302,
        "https://auth.example.test/authorize?state=opaque",
    )
    assert started.headers["cache-control"] == "no-store"
    assert all(
        marker in started.headers["set-cookie"]
        for marker in (
            f"{_LOGIN_COOKIE}=sealed-login",
            "HttpOnly",
            "Max-Age=300",
            f"Path={KEYCLOAK_LOGIN_CALLBACK_PATH}",
            "SameSite=lax",
            "Secure",
        )
    )
    assert (completed.status_code, completed.headers["location"]) == (302, "/workbench")
    assert identity.calls[-1] == (
        "complete",
        "c" * code_length,
        "s" * 43,
        "sealed-login",
        None,
    )
    cookies = completed.headers.get_list("set-cookie")
    assert any(f"{_SESSION_COOKIE}={_PRINCIPAL.session_handle}" in value for value in cookies)
    assert any(f"{_LOGIN_COOKIE}=" in value and "Max-Age=0" in value for value in cookies)
    assert all("provider" not in value.lower() for value in cookies)


def test_login_callback_clears_transaction_cookie_on_redacted_internal_error() -> None:
    client = _client(_FailingLoginIdentity())
    started = client.get(KEYCLOAK_LOGIN_START_PATH, follow_redirects=False)
    assert started.status_code == 302

    response = client.get(
        KEYCLOAK_LOGIN_CALLBACK_PATH,
        params={"code": "provider-code", "state": "s" * 43, "iss": _ISSUER},
        follow_redirects=False,
    )

    assert (response.status_code, response.json()) == (500, {"code": "internal_error"})
    assert response.headers["cache-control"] == "no-store"
    assert f"{_LOGIN_COOKIE}=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_session_and_logout_preserve_identity_roles_and_request_integrity() -> None:
    identity = _Identity()
    client = _client(identity)
    client.cookies.set(_SESSION_COOKIE, _PRINCIPAL.session_handle)

    session = client.get(CONTROL_PLANE_SESSION_PATH)
    logout = client.post(
        KEYCLOAK_LOGOUT_PATH,
        json={},
        headers={"Origin": _PUBLIC_ORIGIN, "X-CSRF-Token": _CSRF},
    )

    assert session.status_code == 200
    assert session.json() == {
        "ok": True,
        "user": {
            "actorId": _PRINCIPAL.actor_id,
            "preferredUsername": "operator",
            "displayName": "Test Operator",
        },
        "roles": sorted(_PRINCIPAL.roles),
        "csrfToken": _CSRF,
        "expiresAt": "2026-09-02T13:00:00Z",
    }
    assert session.headers["cache-control"] == "no-store"
    assert (logout.status_code, logout.json()) == (
        200,
        {"ok": True, "redirectUrl": "https://auth.example.test/logout"},
    )
    assert identity.calls[-1] == ("logout", _PRINCIPAL.session_handle)
    assert "Max-Age=0" in logout.headers["set-cookie"]


@pytest.mark.parametrize(
    ("authentication", "status_code", "error"),
    [
        (IdentityRejected("unauthenticated"), 401, "unauthenticated"),
        (IdentityRejected("overloaded"), 503, "overloaded"),
        (IdentityUnavailable("session_store_unavailable"), 503, "unavailable"),
    ],
    ids=("invalid", "overloaded", "unavailable"),
)
def test_session_failure_algebra_is_exact(
    authentication: BrowserAuthenticationResult,
    status_code: int,
    error: str,
) -> None:
    identity = _Identity()
    identity.authentication_result = authentication
    client = _client(identity)
    client.cookies.set(_SESSION_COOKIE, "opaque-session")

    response = client.get(CONTROL_PLANE_SESSION_PATH)

    assert (response.status_code, response.json()) == (
        status_code,
        {"ok": False, "error": error},
    )
    assert response.headers["cache-control"] == "no-store"


def test_identity_routes_reject_ambiguous_credentials_and_callback_queries() -> None:
    identity = _Identity()
    client = _client(identity)
    client.cookies.set(_SESSION_COOKIE, _PRINCIPAL.session_handle)
    client.cookies.set(_LOGIN_COOKIE, "sealed-login")

    dual = client.get(
        CONTROL_PLANE_SESSION_PATH,
        headers={"Authorization": "Bearer " + "x" * 32},
    )
    duplicate = client.get(
        KEYCLOAK_LOGIN_CALLBACK_PATH + "?code=one&code=two&state=" + "s" * 43,
        follow_redirects=False,
    )
    unexpected = client.get(
        KEYCLOAK_LOGIN_START_PATH,
        params={"return": "/workbench"},
        follow_redirects=False,
    )

    assert (dual.status_code, dual.json()) == (401, {"ok": False, "error": "unauthenticated"})
    assert (duplicate.status_code, duplicate.json()) == (
        400,
        {"ok": False, "error": "invalid_login"},
    )
    assert "Max-Age=0" in duplicate.headers["set-cookie"]
    assert (unexpected.status_code, unexpected.json()) == (
        400,
        {"ok": False, "error": "invalid_login"},
    )
    assert not any(call[0] == "complete" for call in identity.calls)


def test_logout_rejects_invalid_integrity_before_effect() -> None:
    identity = _Identity()
    client = _client(identity, mutation_admitted=False)
    client.cookies.set(_SESSION_COOKIE, _PRINCIPAL.session_handle)

    response = client.post(
        KEYCLOAK_LOGOUT_PATH,
        json={},
        headers={"Origin": _PUBLIC_ORIGIN, "X-CSRF-Token": _CSRF},
    )

    assert (response.status_code, response.json()) == (
        403,
        {"ok": False, "error": "forbidden"},
    )
    assert not any(call[0] == "logout" for call in identity.calls)


@pytest.mark.parametrize(
    ("verifier_result", "identity_result", "status_code", "error"),
    [
        (KeycloakEvidenceRejected("invalid"), None, 400, "invalid_logout"),
        (KeycloakUnavailable("provider"), None, 503, "unavailable"),
        (_logout_evidence(), IdentityRejected("logout_replayed"), 400, "invalid_logout"),
        (
            _logout_evidence(),
            IdentityUnavailable("logout_store_unavailable"),
            503,
            "unavailable",
        ),
    ],
    ids=("invalid-token", "provider-unavailable", "replayed", "store-unavailable"),
)
def test_back_channel_logout_preserves_failure_algebra(
    verifier_result: BackChannelLogoutEvidence | Exception,
    identity_result: BackChannelLogoutResult | None,
    status_code: int,
    error: str,
) -> None:
    identity = _Identity()
    if identity_result is not None:
        identity.back_channel_result = identity_result
    verifier = _LogoutVerifier(verifier_result)
    response = _client(identity, verifier=verifier).post(
        KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
        data={"logout_token": "signed-token"},
    )

    assert (response.status_code, response.json()) == (
        status_code,
        {"ok": False, "error": error},
    )
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "content_type",
    [
        "application/x-www-form-urlencoded",
        "APPLICATION/X-WWW-FORM-URLENCODED",
    ],
)
def test_back_channel_logout_accepts_only_the_exact_form_boundary(content_type: str) -> None:
    identity = _Identity()
    verifier = _LogoutVerifier()
    client = _client(identity, verifier=verifier)

    accepted = client.post(
        KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
        content=b"logout_token=signed-token",
        headers={"Content-Type": content_type},
    )
    rejected = (
        client.post(
            KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
            json={"logout_token": "signed-token"},
        ),
        client.post(
            KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
            content=b"logout_token=signed-token&unexpected=value",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ),
        client.post(
            KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
            content=b"logout_token=first&logout_token=second",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ),
        client.post(
            KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
            content=b"logout_token=",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ),
    )

    assert accepted.status_code == 204
    assert accepted.headers["cache-control"] == "no-store"
    assert verifier.calls == ["signed-token"]
    assert identity.calls == [("back-channel", _logout_evidence())]
    assert all(
        (response.status_code, response.json()) == (400, {"ok": False, "error": "invalid_logout"})
        for response in rejected
    )


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")],
        [("Content-Type", 'application/x-www-form-urlencoded; charset="UTF-8"')],
        [("Content-Type", "application/x-www-form-urlencoded; charset=ISO-8859-1")],
        [("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8; charset=UTF-8")],
        [("Content-Type", "application/x-www-form-urlencoded; charset=")],
        [("Content-Type", "application/x-www-form-urlencoded; unknown=value")],
        [("Content-Type", "application/x-www-form-urlencoded, application/x-www-form-urlencoded")],
        [
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Content-Type", "application/x-www-form-urlencoded"),
        ],
    ],
)
def test_back_channel_logout_unadmitted_media_type_stops_before_verification(
    headers: list[tuple[str, str]],
) -> None:
    identity, verifier = _Identity(), _LogoutVerifier()
    response = _client(identity, verifier=verifier).post(
        KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
        content=b"logout_token=signed-token",
        headers=headers,
    )
    assert (response.status_code, response.json()) == (
        400,
        {"ok": False, "error": "invalid_logout"},
    )
    assert response.headers["cache-control"] == "no-store"
    assert verifier.calls == []
    assert identity.calls == []


@pytest.mark.parametrize(
    "header",
    [
        ("Authorization", "Bearer signed-token"),
        ("Cookie", "session=value"),
        ("Origin", _PUBLIC_ORIGIN),
    ],
)
def test_back_channel_logout_form_never_admits_ambiguous_credentials(
    header: tuple[str, str],
) -> None:
    identity, verifier = _Identity(), _LogoutVerifier()
    response = _client(identity, verifier=verifier).post(
        KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
        content=b"logout_token=signed-token",
        headers=[("Content-Type", "application/x-www-form-urlencoded"), header],
    )
    assert response.status_code == 400
    assert verifier.calls == []
    assert identity.calls == []


def _client(
    identity: _Identity,
    *,
    verifier: _LogoutVerifier | None = None,
    mutation_admitted: bool = True,
    ui_directory: Path | None = None,
    activity: IdentityActivityObserver | None = None,
) -> TestClient:
    dependencies = ControlPlaneIdentityRouteDependencies(
        activity=activity,
        identity=cast(BrowserIdentityUseCase, identity),
        back_channel_logout_tokens=cast(
            BackChannelLogoutTokenVerifier,
            verifier or _LogoutVerifier(),
        ),
        mutation_admission=StaticMutationAdmission(mutation_admitted),
        role_admission=StaticRoleAdmission(),
        issuer=_ISSUER,
        public_origin=_PUBLIC_ORIGIN,
        session_cookie_name=_SESSION_COOKIE,
        transaction_cookie_name=_LOGIN_COOKIE,
        secure_cookies=True,
    )
    return TestClient(
        create_app(
            HttpRouteDependencies(control_plane_identity=dependencies),
            operator_ui_directory=ui_directory,
        ),
        base_url=_PUBLIC_ORIGIN,
    )


@dataclass
class _ActivityObserver:
    failed: bool = False
    events: list[tuple[str, str, str | None]] = field(default_factory=list)

    async def login_diagnostic(self, action: str) -> None:
        self.events.append(("login", action, None))
        if self.failed:
            raise RuntimeError("credential-marker")

    async def principal_diagnostic(self, principal: ActivityPrincipal, action: str) -> None:
        self.events.append(("principal", action, principal.actor_id))


@pytest.mark.parametrize("failed", [False, True])
def test_rejected_callback_records_one_anonymous_diagnostic_before_service(failed: bool) -> None:
    identity, observer = _Identity(), _ActivityObserver(failed)
    client = _client(identity, activity=observer)
    response = client.get(
        KEYCLOAK_LOGIN_CALLBACK_PATH,
        params={"code": "credential-marker", "iss": "foreign"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert "credential-marker" not in response.text
    assert observer.events == [("login", "login_rejected", None)]
    assert identity.calls == []


def test_login_start_unavailability_diagnostic_does_not_change_result() -> None:
    identity, observer = _Identity(), _ActivityObserver(True)
    identity.start_result = IdentityUnavailable("identity_provider_unavailable")
    response = _client(identity, activity=observer).get(
        KEYCLOAK_LOGIN_START_PATH, follow_redirects=False
    )
    assert response.status_code == 503
    assert observer.events == [("login", "login_unavailable", None)]


@pytest.mark.parametrize("field_name", ("code", "state", "iss"))
def test_callback_requires_each_authority_field_before_exchange(field_name: str) -> None:
    params = {"code": "code", "state": "s" * 43, "iss": _ISSUER}
    del params[field_name]
    _assert_callback_rejected(list(params.items()))


@pytest.mark.parametrize("field_name", ("code", "state", "iss", "session_state"))
def test_callback_rejects_duplicate_raw_keys_before_mapping(field_name: str) -> None:
    params = [("code", "code"), ("state", "s" * 43), ("iss", _ISSUER)]
    if field_name == "session_state":
        params.append((field_name, "one"))
    value = dict(params)[field_name]
    _assert_callback_rejected([*params, (field_name, value)])


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("code", ""),
        ("code", "c" * 1_025),
        ("state", ""),
        ("state", "s" * 42),
        ("state", "s" * 44),
        ("state", "!" * 43),
        ("iss", ""),
        ("iss", "https://other.example/realms/coordinator"),
        ("iss", _ISSUER + "/"),
        ("iss", "https://" + "x" * 2_049),
        ("session_state", ""),
        ("session_state", "s" * 513),
        ("session_state", "control\nvalue"),
        ("session_state", "non-ascii-\u00e9"),
        ("redirect_uri", "https://outside.invalid"),
        ("error", "access_denied"),
    ],
)
def test_callback_rejects_each_invalid_field_before_exchange(
    field_name: str,
    value: str,
) -> None:
    params = {"code": "code", "state": "s" * 43, "iss": _ISSUER, field_name: value}
    _assert_callback_rejected(list(params.items()))


def _assert_callback_rejected(params: list[tuple[str, str]]) -> None:
    identity = _Identity()
    client = _client(identity)
    client.get(KEYCLOAK_LOGIN_START_PATH, follow_redirects=False)
    response = client.get(
        KEYCLOAK_LOGIN_CALLBACK_PATH, params=tuple(params), follow_redirects=False
    )
    assert (response.status_code, response.json()) == (400, {"ok": False, "error": "invalid_login"})
    assert response.headers["cache-control"] == "no-store"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert identity.calls == [("start",)]


def test_callback_openapi_is_the_runtime_field_projection() -> None:
    client = _client(_Identity())
    parameters = {
        entry["name"]: entry
        for entry in cast(FastAPI, client.app).openapi()["paths"][KEYCLOAK_LOGIN_CALLBACK_PATH][
            "get"
        ]["parameters"]
    }
    assert set(parameters) == {"code", "state", "iss", "session_state"}
    assert {name for name, entry in parameters.items() if entry["required"]} == {
        "code",
        "state",
        "iss",
    }
    assert parameters["code"]["schema"]["maxLength"] == 1_024
    assert parameters["iss"]["schema"]["maxLength"] == 2_048
    assert parameters["session_state"]["schema"]["anyOf"][0]["maxLength"] == 512


@pytest.fixture
def browser_ui_bundle(tmp_path: Path) -> Path:
    files = {"index.html": b"<main>private workbench</main>", "assets/runtime.js": b"export {}"}
    (tmp_path / "assets").mkdir()
    entries = []
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
        entries.append(
            {
                "path": name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "sizeBytes": len(content),
                "contentType": "text/html; charset=utf-8"
                if name == "index.html"
                else "text/javascript; charset=utf-8",
            }
        )
    (tmp_path / "asset-manifest.v1.json").write_text(
        json.dumps(
            {
                "schemaVersion": "ci-coordinator-operator-ui-assets/v1",
                "files": sorted(entries, key=lambda entry: str(entry["path"])),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="ascii",
    )
    return tmp_path


@pytest.mark.parametrize("path", ("/workbench", "/workbench/"))
@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_PRINCIPAL, 200),
        (human_principal(roles=frozenset({"read"})), 200),
        (human_principal(roles=CONTROL_PLANE_ROLES - {"read"}), 403),
        (human_principal(roles=frozenset()), 403),
        (replace(_PRINCIPAL, expires_at=NOW), 307),
        (IdentityRejected("unauthenticated"), 307),
        (IdentityRejected("authority_profile_changed"), 307),
        (IdentityRejected("overloaded"), 503),
        (IdentityUnavailable("session_store_unavailable"), 503),
    ],
    ids=(
        "valid",
        "read-only-role",
        "other-roles-without-read",
        "no-roles",
        "expired-at-role-admission",
        "revoked",
        "changed-profile",
        "overloaded",
        "store-unavailable",
    ),
)
def test_workbench_checks_current_human_read_authority_before_html(
    browser_ui_bundle: Path,
    path: str,
    method: str,
    result: BrowserAuthenticationResult,
    expected: int,
) -> None:
    identity = _Identity()
    identity.authentication_result = result
    client = _client(identity, ui_directory=browser_ui_bundle)
    client.cookies.set(_SESSION_COOKIE, _PRINCIPAL.session_handle)
    response = client.request(
        method, path + "?next=https://outside.invalid", follow_redirects=False
    )
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert identity.calls == [("authenticate", _PRINCIPAL.session_handle)]
    assert "set-cookie" not in response.headers
    if expected == 307:
        assert response.headers["location"] == KEYCLOAK_LOGIN_START_PATH
    else:
        assert "location" not in response.headers
    if expected != 200 or method == "HEAD":
        assert response.content == b""
    else:
        assert "private workbench" in response.text


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, 307),
        ({"Authorization": "Bearer " + "x" * 32}, 403),
        ({"Cookie": f"{_SESSION_COOKIE}=one; {_SESSION_COOKIE}=two"}, 403),
        ({"Cookie": f"{_SESSION_COOKIE}=one", "Authorization": "Bearer " + "x" * 32}, 403),
    ],
)
def test_workbench_anonymous_and_ambiguous_credentials_never_deliver_html(
    browser_ui_bundle: Path,
    method: str,
    headers: dict[str, str],
    expected: int,
) -> None:
    identity = _Identity()
    client = _client(identity, ui_directory=browser_ui_bundle)
    response = client.request(method, "/workbench", headers=headers, follow_redirects=False)
    assert response.status_code == expected
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert identity.calls == []
    if expected == 307:
        assert response.headers["location"] == KEYCLOAK_LOGIN_START_PATH
    else:
        assert "location" not in response.headers
    assert client.get("/assets/runtime.js").status_code == 200
    assert identity.calls == []


@pytest.mark.parametrize("result", (IdentityRejected("forbidden"), object()))
def test_workbench_rejects_unknown_identity_outcomes_without_a_login_loop(
    browser_ui_bundle: Path,
    result: object,
) -> None:
    identity = _Identity()
    identity.authentication_result = cast(BrowserAuthenticationResult, result)
    client = _client(identity, ui_directory=browser_ui_bundle)
    client.cookies.set(_SESSION_COOKIE, _PRINCIPAL.session_handle)
    response = client.get("/workbench", follow_redirects=False)
    assert (response.status_code, response.json()) == (500, {"code": "internal_error"})
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert "private workbench" not in response.text
