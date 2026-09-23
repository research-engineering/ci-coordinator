"""Keycloak browser identity and back-channel logout HTTP projection."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Security, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import Field, ValidationError

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import cookie_values
from ci_coordinator.api.http.dependencies import ControlPlaneIdentityRouteDependencies
from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.api.http.public_request_limits import PublicRequestLimitPolicy
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.control_plane_identity import (
    BackChannelLogoutCompleted,
    BrowserLoginCompleted,
    BrowserLoginStart,
    BrowserLogoutCompleted,
    IdentityRejected,
    IdentityUnavailable,
    KeycloakEvidenceRejected,
    KeycloakHumanPrincipal,
    KeycloakUnavailable,
)
from ci_coordinator.control_plane_identity.activity import observe_login

CONTROL_PLANE_IDENTITY_PREFIX = "/api/v1/auth"
KEYCLOAK_LOGIN_START_PATH = f"{CONTROL_PLANE_IDENTITY_PREFIX}/keycloak/start"
KEYCLOAK_LOGIN_CALLBACK_PATH = f"{CONTROL_PLANE_IDENTITY_PREFIX}/keycloak/callback"
CONTROL_PLANE_SESSION_PATH = f"{CONTROL_PLANE_IDENTITY_PREFIX}/session"
KEYCLOAK_LOGOUT_PATH = f"{CONTROL_PLANE_IDENTITY_PREFIX}/keycloak/logout"
KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH = f"{CONTROL_PLANE_IDENTITY_PREFIX}/keycloak/backchannel-logout"
_WORKBENCH_PATH = "/workbench"
_NO_STORE = {"Cache-Control": "no-store"}
_MAXIMUM_LOGOUT_FORM_BYTES = 20 * 1_024


class KeycloakCallbackQuery(RequestModel):
    code: str = Field(min_length=1, max_length=1_024)
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    iss: str = Field(min_length=1, max_length=2_048)
    session_state: str | None = Field(
        default=None, min_length=1, max_length=512, pattern=r"^[\x21-\x7e]+$"
    )


_CALLBACK_QUERY_SCHEMA = KeycloakCallbackQuery.model_json_schema()
_CALLBACK_QUERY_CONTRACT = {
    "parameters": [
        {
            "name": name,
            "in": "query",
            "required": name in _CALLBACK_QUERY_SCHEMA["required"],
            "schema": schema,
        }
        for name, schema in _CALLBACK_QUERY_SCHEMA["properties"].items()
    ]
}
_BACK_CHANNEL_LOGOUT_CONTRACT = {
    "requestBody": {
        "required": True,
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "required": ["logout_token"],
                    "additionalProperties": False,
                    "properties": {
                        "logout_token": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAXIMUM_LOGOUT_FORM_BYTES,
                        }
                    },
                }
            }
        },
    }
}


class ControlPlaneIdentityErrorResponse(ResponseModel):
    ok: Literal[False]
    error: Literal[
        "forbidden",
        "invalid_login",
        "invalid_logout",
        "overloaded",
        "rate_limited",
        "unauthenticated",
        "unavailable",
    ]


class ControlPlaneUserResponse(ResponseModel):
    actor_id: str
    preferred_username: str | None
    display_name: str | None


class ControlPlaneSessionResponse(ResponseModel):
    ok: Literal[True]
    user: ControlPlaneUserResponse
    roles: tuple[str, ...]
    csrf_token: str
    expires_at: datetime


class ControlPlaneLogoutRequest(RequestModel):
    pass


class ControlPlaneLogoutResponse(ResponseModel):
    ok: Literal[True]
    redirect_url: str


KEYCLOAK_IDENTITY_BODY_LIMITS = (
    BodyLimitPolicy(
        path=KEYCLOAK_LOGOUT_PATH,
        maximum_body_bytes=32,
        invalid_content_length_response={"ok": False, "error": "forbidden"},
        too_large_response={"ok": False, "error": "forbidden"},
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
    ),
    BodyLimitPolicy(
        path=KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
        maximum_body_bytes=_MAXIMUM_LOGOUT_FORM_BYTES,
        invalid_content_length_response={"ok": False, "error": "invalid_logout"},
        too_large_response={"ok": False, "error": "invalid_logout"},
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
    ),
)
KEYCLOAK_PUBLIC_REQUEST_LIMITS = tuple(
    PublicRequestLimitPolicy(
        path=path,
        methods=frozenset({method}),
        burst=16,
        refill_rate_per_second=4.0,
        rejection_response={"ok": False, "error": "rate_limited"},
    )
    for path, method in (
        (KEYCLOAK_LOGIN_START_PATH, "GET"),
        (KEYCLOAK_LOGIN_CALLBACK_PATH, "GET"),
        (KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH, "POST"),
    )
)


def build_control_plane_identity_router(
    dependencies: ControlPlaneIdentityRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        KEYCLOAK_LOGIN_START_PATH,
        operation_id="start_keycloak_browser_login",
        response_class=RedirectResponse,
        status_code=status.HTTP_302_FOUND,
        responses={
            status.HTTP_302_FOUND: {"description": "Redirect to the admitted Keycloak realm."},
            status.HTTP_400_BAD_REQUEST: {"model": ControlPlaneIdentityErrorResponse},
            status.HTTP_429_TOO_MANY_REQUESTS: _rate_limit_response(),
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ControlPlaneIdentityErrorResponse},
        },
    )
    async def start_keycloak_browser_login(request: Request) -> Response:
        if request.query_params or _header_values(request, b"authorization"):
            await observe_login(dependencies.activity, "login_rejected")
            return _error(status.HTTP_400_BAD_REQUEST, "invalid_login")
        started = dependencies.identity.start_login()
        if isinstance(started, IdentityUnavailable):
            await observe_login(dependencies.activity, "login_unavailable")
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(started, BrowserLoginStart):
            raise RuntimeError("unsupported Keycloak login-start outcome")
        response = RedirectResponse(started.authorization_url, status_code=status.HTTP_302_FOUND)
        response.headers.update(_NO_STORE)
        response.set_cookie(
            dependencies.transaction_cookie_name,
            started.transaction_cookie,
            max_age=started.lifetime_seconds,
            expires=started.expires_at,
            path=KEYCLOAK_LOGIN_CALLBACK_PATH,
            secure=dependencies.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get(
        KEYCLOAK_LOGIN_CALLBACK_PATH,
        operation_id="complete_keycloak_browser_login",
        response_class=RedirectResponse,
        status_code=status.HTTP_302_FOUND,
        responses={
            status.HTTP_302_FOUND: {"description": "Redirect to the authenticated workbench."},
            status.HTTP_400_BAD_REQUEST: {"model": ControlPlaneIdentityErrorResponse},
            status.HTTP_429_TOO_MANY_REQUESTS: _rate_limit_response(),
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ControlPlaneIdentityErrorResponse},
        },
        openapi_extra=_CALLBACK_QUERY_CONTRACT,
    )
    async def complete_keycloak_browser_login(request: Request) -> Response:
        query = _exact_callback_query(request, issuer=dependencies.issuer)
        transactions = cookie_values(request, dependencies.transaction_cookie_name)
        previous = cookie_values(request, dependencies.session_cookie_name)
        if (
            _header_values(request, b"authorization")
            or query is None
            or len(transactions) != 1
            or len(previous) > 1
        ):
            await observe_login(dependencies.activity, "login_rejected")
            return _error(status.HTTP_400_BAD_REQUEST, "invalid_login")
        completed = await dependencies.identity.complete_login(
            code=query.code,
            state=query.state,
            transaction_cookie=transactions[0],
            previous_session_handle=previous[0] if previous else None,
        )
        if isinstance(completed, IdentityRejected):
            return _error(status.HTTP_400_BAD_REQUEST, "invalid_login")
        if isinstance(completed, IdentityUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(completed, BrowserLoginCompleted):
            raise RuntimeError("unsupported Keycloak login outcome")
        response = RedirectResponse(_WORKBENCH_PATH, status_code=status.HTTP_302_FOUND)
        response.headers.update(_NO_STORE)
        response.set_cookie(
            dependencies.session_cookie_name,
            completed.principal.session_handle,
            expires=completed.principal.expires_at,
            path="/",
            secure=dependencies.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get(
        CONTROL_PLANE_SESSION_PATH,
        operation_id="get_control_plane_session",
        response_model=ControlPlaneSessionResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": ControlPlaneIdentityErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ControlPlaneIdentityErrorResponse},
        },
    )
    async def get_control_plane_session(
        request: Request,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        principal = await _authenticate_human(request, dependencies)
        if isinstance(principal, JSONResponse):
            return principal
        response = ControlPlaneSessionResponse.model_validate(
            {
                "ok": True,
                "user": {
                    "actor_id": principal.actor_id,
                    "preferred_username": principal.display.preferred_username,
                    "display_name": principal.display.display_name,
                },
                "roles": tuple(sorted(principal.roles)),
                "csrf_token": dependencies.identity.csrf_token(principal),
                "expires_at": principal.expires_at,
            }
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.to_wire_mapping(),
            headers=_NO_STORE,
        )

    @router.post(
        KEYCLOAK_LOGOUT_PATH,
        operation_id="logout_keycloak_browser_session",
        response_model=ControlPlaneLogoutResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": ControlPlaneIdentityErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ControlPlaneIdentityErrorResponse},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": ControlPlaneIdentityErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ControlPlaneIdentityErrorResponse},
        },
    )
    async def logout_keycloak_browser_session(
        request: Request,
        _body: ControlPlaneLogoutRequest,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> Response:
        principal = await _authenticate_human(request, dependencies)
        if isinstance(principal, JSONResponse):
            return principal
        if not dependencies.mutation_admission.admits(request, principal):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        completed = await dependencies.identity.logout(principal.session_handle)
        if isinstance(completed, IdentityUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(completed, BrowserLogoutCompleted):
            raise RuntimeError("unsupported Keycloak logout outcome")
        response = JSONResponse(
            status_code=status.HTTP_200_OK,
            content=ControlPlaneLogoutResponse(
                ok=True,
                redirect_url=completed.remote_redirect_url or _WORKBENCH_PATH,
            ).to_wire_mapping(),
            headers=_NO_STORE,
        )
        _clear_session_cookie(response, dependencies)
        return response

    @router.post(
        KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
        operation_id="apply_keycloak_back_channel_logout",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ControlPlaneIdentityErrorResponse},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": ControlPlaneIdentityErrorResponse},
            status.HTTP_429_TOO_MANY_REQUESTS: _rate_limit_response(),
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ControlPlaneIdentityErrorResponse},
        },
        openapi_extra=_BACK_CHANNEL_LOGOUT_CONTRACT,
    )
    async def apply_keycloak_back_channel_logout(request: Request) -> Response:
        token = await _back_channel_logout_token(request)
        if token is None:
            return _error(status.HTTP_400_BAD_REQUEST, "invalid_logout")
        try:
            evidence = await dependencies.back_channel_logout_tokens.verify_back_channel_logout(
                token
            )
        except KeycloakEvidenceRejected:
            return _error(status.HTTP_400_BAD_REQUEST, "invalid_logout")
        except KeycloakUnavailable:
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        completed = await dependencies.identity.apply_back_channel_logout(evidence)
        if isinstance(completed, IdentityRejected):
            return _error(status.HTTP_400_BAD_REQUEST, "invalid_logout")
        if isinstance(completed, IdentityUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(completed, BackChannelLogoutCompleted):
            raise RuntimeError("unsupported back-channel logout outcome")
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)

    return router


async def _authenticate_human(
    request: Request,
    dependencies: ControlPlaneIdentityRouteDependencies,
) -> KeycloakHumanPrincipal | JSONResponse:
    sessions = cookie_values(request, dependencies.session_cookie_name)
    if len(sessions) != 1 or _header_values(request, b"authorization"):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
    principal = await dependencies.identity.authenticate(sessions[0])
    if isinstance(principal, IdentityUnavailable):
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
    if isinstance(principal, IdentityRejected):
        return _error(
            status.HTTP_503_SERVICE_UNAVAILABLE
            if principal.code == "overloaded"
            else status.HTTP_401_UNAUTHORIZED,
            "overloaded" if principal.code == "overloaded" else "unauthenticated",
        )
    return principal


async def _back_channel_logout_token(request: Request) -> str | None:
    if (
        request.query_params
        or _header_values(request, b"authorization")
        or _header_values(request, b"cookie")
        or _header_values(request, b"origin")
        or tuple(value.lower() for value in _header_values(request, b"content-type"))
        != (b"application/x-www-form-urlencoded",)
    ):
        return None
    raw = await request.body()
    try:
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=1,
            separator="&",
        )
    except (UnicodeError, ValueError):
        return None
    if len(pairs) != 1 or pairs[0][0] != "logout_token" or not pairs[0][1]:
        return None
    try:
        token_bytes = pairs[0][1].encode("ascii")
    except UnicodeEncodeError:
        return None
    if len(token_bytes) > 16_384:
        return None
    return pairs[0][1]


def _exact_callback_query(request: Request, *, issuer: str) -> KeycloakCallbackQuery | None:
    items = tuple(request.query_params.multi_items())
    if len(items) > 4 or len({name for name, _value in items}) != len(items):
        return None
    try:
        query = KeycloakCallbackQuery.model_validate(dict(items))
    except ValidationError:
        return None
    return query if query.iss == issuer else None


def _clear_session_cookie(
    response: Response,
    dependencies: ControlPlaneIdentityRouteDependencies,
) -> None:
    response.delete_cookie(
        dependencies.session_cookie_name,
        path="/",
        secure=dependencies.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def _header_values(request: Request, name: bytes) -> tuple[bytes, ...]:
    return tuple(
        value for candidate, value in request.scope.get("headers", ()) if candidate.lower() == name
    )


def _rate_limit_response() -> dict[str, Any]:
    return {
        "model": ControlPlaneIdentityErrorResponse,
        "headers": {
            "Retry-After": {
                "description": "Whole seconds before the admission budget refills.",
                "required": True,
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    }


def _error(
    status_code: int,
    error: Literal[
        "forbidden",
        "invalid_login",
        "invalid_logout",
        "overloaded",
        "unauthenticated",
        "unavailable",
    ],
) -> JSONResponse:
    response = ControlPlaneIdentityErrorResponse(ok=False, error=error)
    headers = dict(_NO_STORE)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        headers.update(WWW_AUTHENTICATE_HEADER)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )
