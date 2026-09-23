from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from ci_coordinator.api.http.control_plane_authentication import (
    BreakGlassBearerAuthenticator,
    ControlPlaneAdmissionDependencies,
    ControlPlaneRequestAuthenticator,
    control_plane_admission,
)
from ci_coordinator.api.http.control_plane_security import mutation_request_is_admitted
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    InvalidCredential,
)
from ci_coordinator.control_plane_identity import (
    BreakGlassPrincipal,
    BrowserIdentityUseCase,
    IdentityRejected,
    IdentityUnavailable,
    KeycloakHumanPrincipal,
    KeycloakWorkloadPrincipal,
    MachineIdentityUseCase,
)

_ISSUER = "https://auth.example.test/realms/coordinator"
_PROFILE = "a" * 64
_NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
_SESSION = "A" * 43
_COOKIE = "__Host-ci_coordinator_session"


def test_admission_factory_preserves_metadata_only_router_construction() -> None:
    admit = control_plane_admission(
        cast(ControlPlaneAdmissionDependencies, object()),
        error=lambda status, code: JSONResponse({"code": code}, status_code=status),
    )

    assert callable(admit)


@dataclass(slots=True)
class _HumanAuthenticator:
    result: KeycloakHumanPrincipal | IdentityRejected | IdentityUnavailable

    async def authenticate(
        self,
        _session_handle: str | None,
    ) -> KeycloakHumanPrincipal | IdentityRejected | IdentityUnavailable:
        return self.result

    def csrf_matches(self, principal: KeycloakHumanPrincipal, candidate: str | None) -> bool:
        return principal.session_handle == _SESSION and candidate == "csrf"


@dataclass(slots=True)
class _MachineAuthenticator:
    result: KeycloakWorkloadPrincipal | IdentityRejected | IdentityUnavailable
    calls: list[str | None] = field(default_factory=list)

    async def authenticate(
        self,
        token: str | None,
    ) -> KeycloakWorkloadPrincipal | IdentityRejected | IdentityUnavailable:
        self.calls.append(token)
        return self.result


def test_authenticator_preserves_disjoint_credential_planes() -> None:
    human = _HumanAuthenticator(_human())
    machine = _MachineAuthenticator(_workload())
    authenticator = _authenticator(human=human, machine=machine)

    session_result = asyncio.run(
        authenticator.authenticate(_request((b"cookie", f"{_COOKIE}={_SESSION}".encode())))
    )
    machine_result = asyncio.run(
        authenticator.authenticate(_request((b"authorization", b"Bearer " + b"m" * 32)))
    )
    break_glass_result = asyncio.run(
        authenticator.authenticate(_request((b"authorization", b"Bearer " + b"b" * 32)))
    )

    assert isinstance(session_result, KeycloakHumanPrincipal)
    assert isinstance(machine_result, KeycloakWorkloadPrincipal)
    assert break_glass_result == BreakGlassPrincipal("dev1")
    assert machine.calls == ["m" * 32]


@pytest.mark.parametrize(
    "headers",
    [
        (
            (b"authorization", b"Bearer " + b"b" * 32),
            (b"cookie", f"{_COOKIE}={_SESSION}".encode()),
        ),
        ((b"cookie", f"{_COOKIE}={_SESSION}; {_COOKIE}={_SESSION}".encode()),),
        (
            (b"authorization", b"Bearer token-a"),
            (b"authorization", b"Bearer token-b"),
        ),
        ((b"authorization", b"Basic credentials"),),
        ((b"authorization", b"Bearer \xff"),),
        (),
    ],
    ids=("dual", "duplicate-session", "duplicate-bearer", "wrong-scheme", "non-ascii", "absent"),
)
def test_authenticator_rejects_ambiguous_or_malformed_credentials(
    headers: tuple[tuple[bytes, bytes], ...],
) -> None:
    result = asyncio.run(
        _authenticator(
            human=_HumanAuthenticator(_human()),
            machine=_MachineAuthenticator(_workload()),
        ).authenticate(_request(*headers))
    )

    assert isinstance(result, InvalidCredential)


@pytest.mark.parametrize("plane", ["human", "machine"])
def test_authenticator_preserves_dependency_unavailability(plane: str) -> None:
    human_result = IdentityUnavailable("session_store_unavailable")
    machine_result = IdentityUnavailable("machine_verifier_unavailable")
    request = (
        _request((b"cookie", f"{_COOKIE}={_SESSION}".encode()))
        if plane == "human"
        else _request((b"authorization", b"Bearer " + b"m" * 32))
    )

    result = asyncio.run(
        _authenticator(
            human=_HumanAuthenticator(human_result),
            machine=_MachineAuthenticator(machine_result),
        ).authenticate(request)
    )

    assert isinstance(result, AuthenticationDependencyUnavailable)


@pytest.mark.parametrize("plane", ["human", "machine"])
def test_authenticator_projects_capacity_rejection_as_retryable_unavailability(plane: str) -> None:
    request = (
        _request((b"cookie", f"{_COOKIE}={_SESSION}".encode()))
        if plane == "human"
        else _request((b"authorization", b"Bearer " + b"m" * 32))
    )

    result = asyncio.run(
        _authenticator(
            human=_HumanAuthenticator(IdentityRejected("overloaded")),
            machine=_MachineAuthenticator(IdentityRejected("overloaded")),
        ).authenticate(request)
    )

    assert isinstance(result, AuthenticationDependencyUnavailable)


def test_break_glass_language_is_disjoint_from_compact_jwt_serialization() -> None:
    with pytest.raises(ValueError, match="break-glass bearer"):
        BreakGlassBearerAuthenticator(
            actor_id="break-glass:v1:dev1",
            bearer_token=f"{'a' * 32}.{'b' * 32}.{'c' * 32}",
        )


def test_mutation_integrity_is_credential_plane_specific() -> None:
    human = _HumanAuthenticator(_human())
    human_request = _request(
        (b"content-type", b"application/json"),
        (b"origin", b"https://ci.example.test"),
        (b"x-csrf-token", b"csrf"),
    )
    machine_request = _request(
        (b"content-type", b"application/json"),
        (b"authorization", b"Bearer " + b"m" * 32),
    )

    assert mutation_request_is_admitted(
        human_request,
        principal=_human(),
        human=cast(BrowserIdentityUseCase, human),
        public_origin="https://ci.example.test",
    )
    assert mutation_request_is_admitted(
        machine_request,
        principal=_workload(),
        human=cast(BrowserIdentityUseCase, human),
        public_origin="https://ci.example.test",
    )
    assert not mutation_request_is_admitted(
        _request(
            (b"content-type", b"application/json"),
            (b"origin", b"https://ci.example.test"),
        ),
        principal=_workload(),
        human=cast(BrowserIdentityUseCase, human),
        public_origin="https://ci.example.test",
    )


def _authenticator(
    *,
    human: _HumanAuthenticator,
    machine: _MachineAuthenticator,
) -> ControlPlaneRequestAuthenticator:
    return ControlPlaneRequestAuthenticator(
        session_cookie_name=_COOKIE,
        human=cast(BrowserIdentityUseCase, human),
        machine=cast(MachineIdentityUseCase, machine),
        break_glass=BreakGlassBearerAuthenticator(
            actor_id="break-glass:v1:dev1",
            bearer_token="b" * 32,
        ),
    )


def _human() -> KeycloakHumanPrincipal:
    return KeycloakHumanPrincipal(
        issuer=_ISSUER,
        subject="human-subject",
        keycloak_session_id="session-id",
        roles=frozenset({"read", "configure"}),
        session_handle=_SESSION,
        expires_at=_NOW + timedelta(minutes=5),
        authority_profile_digest=_PROFILE,
    )


def _workload() -> KeycloakWorkloadPrincipal:
    return KeycloakWorkloadPrincipal(
        issuer=_ISSUER,
        authorized_party="review-bot",
        subject="service-account-review-bot",
        roles=frozenset({"read"}),
        issued_at=_NOW,
        expires_at=_NOW + timedelta(minutes=1),
        authority_profile_digest=_PROFILE,
    )


def _request(*headers: tuple[bytes, bytes]) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": None,
            "server": ("testserver", 443),
        }
    )
