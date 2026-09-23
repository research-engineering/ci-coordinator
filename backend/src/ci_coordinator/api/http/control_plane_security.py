"""Request-integrity admission for authenticated control-plane mutations."""

from __future__ import annotations

from fastapi import Request

from ci_coordinator.control_plane_identity import (
    BreakGlassPrincipal,
    BrowserIdentityUseCase,
    ControlPlanePrincipal,
    KeycloakHumanPrincipal,
    KeycloakWorkloadPrincipal,
)


class ControlPlaneMutationGuard:
    def __init__(
        self,
        *,
        human: BrowserIdentityUseCase | None,
        public_origin: str | None,
    ) -> None:
        if (human is None) != (public_origin is None):
            raise ValueError(
                "human mutation verifier and public origin must be configured together"
            )
        if public_origin is not None:
            try:
                encoded = public_origin.encode("ascii")
            except UnicodeEncodeError as error:
                raise ValueError("control-plane public origin must be ASCII") from error
            if not encoded or any(character <= 0x20 for character in encoded):
                raise ValueError("control-plane public origin is invalid")
        self._human = human
        self._public_origin = public_origin

    def admits(self, request: Request, principal: ControlPlanePrincipal) -> bool:
        return mutation_request_is_admitted(
            request,
            principal=principal,
            human=self._human,
            public_origin=self._public_origin,
        )


def mutation_request_is_admitted(
    request: Request,
    *,
    principal: ControlPlanePrincipal,
    human: BrowserIdentityUseCase | None,
    public_origin: str | None,
) -> bool:
    content_type = _header_values(request, b"content-type")
    if content_type != (b"application/json",):
        return False
    if isinstance(principal, KeycloakHumanPrincipal):
        if human is None or public_origin is None:
            return False
        csrf = _header_values(request, b"x-csrf-token")
        return (
            _header_values(request, b"origin") == (public_origin.encode("ascii"),)
            and len(csrf) == 1
            and human.csrf_matches(principal, _ascii(csrf[0]))
        )
    if isinstance(principal, KeycloakWorkloadPrincipal | BreakGlassPrincipal):
        return not _header_values(request, b"origin") and not _header_values(request, b"cookie")
    return False


def _header_values(request: Request, name: bytes) -> tuple[bytes, ...]:
    return tuple(
        value for candidate, value in request.scope.get("headers", ()) if candidate.lower() == name
    )


def _ascii(value: bytes) -> str | None:
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        return None
