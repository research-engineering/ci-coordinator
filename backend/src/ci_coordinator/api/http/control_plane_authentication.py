"""Mutually exclusive control-plane credential admission."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Callable, Coroutine
from hashlib import sha256
from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse

from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CiEconomicsBudgetRouteDependencies,
    CiHistoryRouteDependencies,
    CiObservationRouteDependencies,
    ControlPlaneAuthenticationResult,
    ControlPlaneAuthenticator,
    ForbiddenIdentity,
    InvalidCredential,
    PurposeSettingsRouteDependencies,
)
from ci_coordinator.control_plane_identity import (
    BreakGlassPrincipal,
    BrowserIdentityUseCase,
    ControlPlanePrincipal,
    ControlPlaneRole,
    ControlPlaneRoleAdmission,
    IdentityRejected,
    IdentityUnavailable,
    MachineIdentityUseCase,
    RoleAdmission,
    RoleAdmissionGranted,
    admit_control_plane_roles,
)
from ci_coordinator.control_plane_identity.activity import (
    IdentityActivityObserver,
    observe_principal,
)
from ci_coordinator.control_plane_identity.model import (
    KeycloakHumanPrincipal,
    KeycloakWorkloadPrincipal,
)
from ci_coordinator.kernel import Clock
from ci_coordinator.runtime_settings import is_break_glass_bearer_token


class BreakGlassBearerAuthenticator:
    """Map one deployment credential to the emergency-safety plane."""

    def __init__(self, *, actor_id: str, bearer_token: str) -> None:
        prefix = "break-glass:v1:"
        if type(actor_id) is not str or not actor_id.startswith(prefix):
            raise ValueError("break-glass actor id must use its authority namespace")
        self._principal = BreakGlassPrincipal(actor_id.removeprefix(prefix))
        if self._principal.actor_id != actor_id:
            raise ValueError("break-glass actor id is not canonical")
        if not is_break_glass_bearer_token(bearer_token):
            raise ValueError("break-glass bearer must be bounded ASCII authentication text")
        self._bearer_digest = sha256(bearer_token.encode("ascii")).digest()

    def authenticate(self, token: bytes) -> BreakGlassPrincipal | None:
        supplied_digest = sha256(token).digest()
        return (
            self._principal if hmac.compare_digest(supplied_digest, self._bearer_digest) else None
        )


type AuthorizedControlPlaneRequest = (
    RoleAdmissionGranted
    | InvalidCredential
    | ForbiddenIdentity
    | AuthenticationDependencyUnavailable
)


class ControlPlaneRequestAuthenticator:
    def __init__(
        self,
        *,
        session_cookie_name: str | None,
        human: BrowserIdentityUseCase | None,
        machine: MachineIdentityUseCase | None,
        break_glass: BreakGlassBearerAuthenticator,
    ) -> None:
        if (session_cookie_name is None) != (human is None):
            raise ValueError("human identity and session cookie must be configured together")
        if session_cookie_name is not None and (
            not session_cookie_name
            or len(session_cookie_name.encode("ascii", errors="ignore")) != len(session_cookie_name)
            or any(character.isspace() for character in session_cookie_name)
        ):
            raise ValueError("control-plane session cookie name is invalid")
        self._session_cookie_name = session_cookie_name
        self._human = human
        self._machine = machine
        self._break_glass = break_glass

    async def authenticate(self, request: Request) -> ControlPlaneAuthenticationResult:
        authorization = _header_values(request, b"authorization")
        sessions = cookie_values(request, self._session_cookie_name)
        if len(authorization) > 1 or len(sessions) > 1 or (authorization and sessions):
            return InvalidCredential()
        if sessions:
            if self._human is None:
                return InvalidCredential()
            return await _authenticate_human(self._human, sessions[0])
        if not authorization:
            return InvalidCredential()
        token = _bearer_token(authorization[0])
        if token is None:
            return InvalidCredential()
        break_glass = self._break_glass.authenticate(token)
        if break_glass is not None:
            return break_glass
        if self._machine is None:
            return InvalidCredential()
        return await _authenticate_machine(self._machine, token.decode("ascii"))


class ControlPlaneRoleAuthorizer:
    def __init__(self, clock: Clock, activity: IdentityActivityObserver | None = None) -> None:
        self._clock = clock
        self._activity = activity

    async def record_denial(self, principal: ControlPlanePrincipal) -> None:
        if isinstance(principal, KeycloakHumanPrincipal | KeycloakWorkloadPrincipal):
            await observe_principal(self._activity, principal, "role_denied")

    def admit(
        self,
        principal: ControlPlanePrincipal,
        required_roles: frozenset[ControlPlaneRole],
    ) -> RoleAdmission:
        return admit_control_plane_roles(
            principal,
            required_roles,
            at=self._clock.now(),
        )


async def authenticate_and_admit_roles(
    request: Request,
    *,
    authenticator: ControlPlaneAuthenticator,
    role_admission: ControlPlaneRoleAdmission,
    required_roles: frozenset[ControlPlaneRole],
) -> AuthorizedControlPlaneRequest:
    authentication = await authenticator.authenticate(request)
    if isinstance(authentication, InvalidCredential | AuthenticationDependencyUnavailable):
        return authentication
    admission = role_admission.admit(authentication, required_roles)
    if isinstance(admission, RoleAdmissionGranted):
        return admission
    if admission.code == "forbidden" and isinstance(role_admission, ControlPlaneRoleAuthorizer):
        await role_admission.record_denial(authentication)
    return InvalidCredential() if admission.code == "unauthenticated" else ForbiddenIdentity()


type ControlPlaneAdmissionDependencies = (
    CiEconomicsBudgetRouteDependencies
    | CiHistoryRouteDependencies
    | CiObservationRouteDependencies
    | PurposeSettingsRouteDependencies
)


def control_plane_admission(
    dependencies: ControlPlaneAdmissionDependencies,
    *,
    error: Callable[[int, Literal["unauthenticated", "unavailable", "forbidden"]], JSONResponse],
) -> Callable[
    [Request, ControlPlaneRole], Coroutine[object, object, RoleAdmissionGranted | JSONResponse]
]:
    async def admit(
        request: Request, role: ControlPlaneRole
    ) -> RoleAdmissionGranted | JSONResponse:
        admission = await authenticate_and_admit_roles(
            request,
            authenticator=dependencies.authenticator,
            role_admission=dependencies.role_admission,
            required_roles=frozenset({role}),
        )
        if isinstance(admission, InvalidCredential):
            return error(401, "unauthenticated")
        if isinstance(admission, AuthenticationDependencyUnavailable):
            return error(503, "unavailable")
        if isinstance(admission, ForbiddenIdentity) or (
            role == "configure"
            and not dependencies.mutation_admission.admits(request, admission.principal)
        ):
            return error(403, "forbidden")
        return admission

    return admit


def cookie_values(request: Request, name: str | None) -> tuple[str, ...]:
    if name is None:
        return ()
    values: list[str] = []
    for raw_header in _header_values(request, b"cookie"):
        header = raw_header.decode("latin-1")
        for member in header.split(";"):
            candidate_name, separator, candidate_value = member.strip().partition("=")
            if separator and candidate_name == name:
                values.append(candidate_value)
    return tuple(values)


def _header_values(request: Request, name: bytes) -> tuple[bytes, ...]:
    return tuple(
        value for candidate, value in request.scope.get("headers", ()) if candidate.lower() == name
    )


def _bearer_token(authorization: bytes) -> bytes | None:
    if not 7 + 32 <= len(authorization) <= 7 + 16_384:
        return None
    if authorization[:6].lower() != b"bearer" or authorization[6:7] != b" ":
        return None
    token = authorization[7:]
    return token if all(0x21 <= character <= 0x7E for character in token) else None


async def _authenticate_human(
    authenticator: BrowserIdentityUseCase,
    session_handle: str,
) -> ControlPlaneAuthenticationResult:
    try:
        result = await authenticator.authenticate(session_handle)
    except asyncio.CancelledError:
        raise
    if isinstance(result, IdentityUnavailable):
        return AuthenticationDependencyUnavailable()
    if isinstance(result, IdentityRejected):
        return (
            AuthenticationDependencyUnavailable()
            if result.code == "overloaded"
            else InvalidCredential()
        )
    return result


async def _authenticate_machine(
    authenticator: MachineIdentityUseCase,
    token: str,
) -> ControlPlaneAuthenticationResult:
    try:
        result = await authenticator.authenticate(token)
    except asyncio.CancelledError:
        raise
    if isinstance(result, IdentityUnavailable):
        return AuthenticationDependencyUnavailable()
    if isinstance(result, IdentityRejected):
        return (
            AuthenticationDependencyUnavailable()
            if result.code == "overloaded"
            else InvalidCredential()
        )
    return result
