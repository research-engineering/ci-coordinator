"""HTTP projection for one proposal-bound GitHub reviewer step-up."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Security, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import Field, field_validator

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.control_plane_authentication import (
    authenticate_and_admit_roles,
    cookie_values,
)
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    RepositoryAttestationRouteDependencies,
)
from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.api.http.public_request_limits import PublicRequestLimitPolicy
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app.proposal_review import ProposalReviewOutcome
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpoch
from ci_coordinator.control_plane_identity import KeycloakHumanPrincipal, RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.proposal_review import ProposalReviewCommand

REPOSITORY_ATTESTATION_START_PATH = "/api/v1/repository-attestations/github/start"
REPOSITORY_ATTESTATION_CALLBACK_PATH = "/api/v1/repository-attestations/github/callback"
REPOSITORY_ATTESTATION_BODY_LIMIT = BodyLimitPolicy(
    path=REPOSITORY_ATTESTATION_START_PATH,
    maximum_body_bytes=4_096,
    invalid_content_length_response={"ok": False, "error": "invalid_request"},
    too_large_response={"ok": False, "error": "invalid_request"},
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
)
REPOSITORY_ATTESTATION_PUBLIC_REQUEST_LIMITS = (
    PublicRequestLimitPolicy(
        path=REPOSITORY_ATTESTATION_CALLBACK_PATH,
        methods=frozenset({"GET"}),
        burst=16,
        refill_rate_per_second=4.0,
        rejection_response={"ok": False, "error": "rate_limited"},
    ),
)

_WORKBENCH_PATH = "/workbench"
_NO_STORE = {"Cache-Control": "no-store"}
_CALLBACK_QUERY_CONTRACT = {
    "parameters": [
        {
            "name": "code",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        {
            "name": "state",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{43}$"},
        },
    ]
}


class ExpectedActiveRequest(RequestModel):
    epoch_id: str = Field(validation_alias="epochId", pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)


class RepositoryAttestationStartRequest(RequestModel):
    installation_id: int = Field(
        validation_alias="installationId",
        ge=1,
        le=MAX_SAFE_JSON_INTEGER,
    )
    repository_id: int = Field(
        validation_alias="repositoryId",
        ge=1,
        le=MAX_SAFE_JSON_INTEGER,
    )
    operation_id: str = Field(validation_alias="operationId", min_length=1, max_length=256)
    expected_manifest_id: str = Field(
        validation_alias="expectedManifestId",
        pattern=r"^proposal:[0-9a-f]{32}$",
    )
    expected_active: ExpectedActiveRequest | None = Field(validation_alias="expectedActive")

    @field_validator("operation_id")
    @classmethod
    def operation_id_is_bounded_scalar_text(cls, value: str) -> str:
        if "\0" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("operation id must contain Unicode scalar text")
        if len(value.encode("utf-8")) > 256:
            raise ValueError("operation id exceeds its byte bound")
        return value


class RepositoryAttestationStartResponse(ResponseModel):
    ok: Literal[True]
    authorization_url: str


type RepositoryAttestationErrorCode = Literal[
    "already_reviewed",
    "baseline_conflict",
    "blocked",
    "diff_limit",
    "epoch_conflict",
    "forbidden",
    "invalid_callback",
    "operation_conflict",
    "overloaded",
    "rate_limited",
    "replayed",
    "stale",
    "unauthenticated",
    "unavailable",
]


class RepositoryAttestationErrorResponse(ResponseModel):
    ok: Literal[False]
    error: RepositoryAttestationErrorCode


def build_repository_attestation_router(
    dependencies: RepositoryAttestationRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        REPOSITORY_ATTESTATION_START_PATH,
        operation_id="start_github_repository_attestation",
        response_model=RepositoryAttestationStartResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_401_UNAUTHORIZED: {
                "model": RepositoryAttestationErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_409_CONFLICT: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": RepositoryAttestationErrorResponse},
        },
    )
    async def start_github_repository_attestation(
        request: Request,
        body: RepositoryAttestationStartRequest,
        _session: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> Response:
        admission = await authenticate_and_admit_roles(
            request,
            authenticator=dependencies.authenticator,
            role_admission=dependencies.role_admission,
            required_roles=frozenset({"configure"}),
        )
        error = _admission_error(admission)
        if error is not None:
            return error
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported repository-attestation admission")
        principal = admission.principal
        if type(principal) is not KeycloakHumanPrincipal:
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if not dependencies.mutation_admission.admits(request, principal):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        scope = RepositoryScope(body.installation_id, body.repository_id)
        expected_active = (
            None
            if body.expected_active is None
            else ActiveConfigEpoch(
                scope,
                body.expected_active.epoch_id,
                body.expected_active.revision,
            )
        )
        outcome = await dependencies.service.start(
            principal=principal,
            command=ProposalReviewCommand(
                scope=scope,
                operation_id=body.operation_id,
                expected_manifest_id=body.expected_manifest_id,
                expected_active=expected_active,
                actor=principal.actor_id,
            ),
        )
        if outcome.state == "ready":
            if outcome.authorization_url is None or outcome.transaction_cookie is None:
                raise RuntimeError("ready repository attestation omitted its redirect state")
            response = JSONResponse(
                RepositoryAttestationStartResponse(
                    ok=True,
                    authorization_url=outcome.authorization_url,
                ).to_wire_mapping(),
                headers=_NO_STORE,
            )
            response.set_cookie(
                dependencies.transaction_cookie_name,
                outcome.transaction_cookie,
                max_age=300,
                path=REPOSITORY_ATTESTATION_CALLBACK_PATH,
                secure=dependencies.secure_cookies,
                httponly=True,
                samesite="lax",
            )
            return response
        if outcome.state == "forbidden":
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if outcome.state in {
            "already_reviewed",
            "baseline_conflict",
            "blocked",
            "operation_conflict",
            "stale",
        }:
            return _error(status.HTTP_409_CONFLICT, outcome.state)
        if outcome.state == "overloaded":
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "overloaded")
        if outcome.state == "unavailable":
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        raise RuntimeError("unsupported repository-attestation start outcome")

    @router.get(
        REPOSITORY_ATTESTATION_CALLBACK_PATH,
        operation_id="complete_github_repository_attestation",
        response_class=RedirectResponse,
        status_code=status.HTTP_303_SEE_OTHER,
        responses={
            status.HTTP_303_SEE_OTHER: {"description": "Return to the workbench."},
            status.HTTP_400_BAD_REQUEST: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_401_UNAUTHORIZED: {
                "model": RepositoryAttestationErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_409_CONFLICT: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_429_TOO_MANY_REQUESTS: {"model": RepositoryAttestationErrorResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": RepositoryAttestationErrorResponse},
        },
        openapi_extra=_CALLBACK_QUERY_CONTRACT,
    )
    async def complete_github_repository_attestation(
        request: Request,
        _session: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> Response:
        admission = await authenticate_and_admit_roles(
            request,
            authenticator=dependencies.authenticator,
            role_admission=dependencies.role_admission,
            required_roles=frozenset({"configure"}),
        )
        error = _admission_error(admission)
        if error is not None:
            return error
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported repository-attestation callback admission")
        principal = admission.principal
        query = _exact_callback_query(request)
        transactions = cookie_values(request, dependencies.transaction_cookie_name)
        if type(principal) is not KeycloakHumanPrincipal or query is None or len(transactions) != 1:
            return _error(status.HTTP_400_BAD_REQUEST, "invalid_callback")
        outcome = await dependencies.service.complete(
            principal=principal,
            transaction_cookie=transactions[0],
            state=query[1],
            code=query[0],
        )
        if outcome.state == "invalid":
            response: Response = _error(status.HTTP_400_BAD_REQUEST, "invalid_callback")
        elif outcome.state == "replayed":
            response = _error(status.HTTP_409_CONFLICT, "replayed")
        elif outcome.state == "unavailable":
            response = _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        elif outcome.state == "completed":
            if outcome.review is None:
                raise RuntimeError("completed repository attestation omitted review outcome")
            response = _review_response(outcome.review)
        else:
            raise RuntimeError("unsupported repository-attestation callback outcome")
        return response

    return router


def _review_response(outcome: ProposalReviewOutcome) -> Response:
    if outcome.state in {"accepted", "duplicate"}:
        if outcome.record is None:
            raise RuntimeError("accepted repository attestation omitted its retained review")
        command = outcome.record.command
        query = urlencode(
            {
                "installationId": command.scope.installation_id,
                "repositoryId": command.scope.repository_id,
                "limit": 10,
                "repositoryAttestation": "reviewed",
                "proposalManifestId": command.expected_manifest_id,
                "reviewOperationId": command.operation_id,
            }
        )
        response = RedirectResponse(
            f"{_WORKBENCH_PATH}?{query}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.headers.update(_NO_STORE)
        return response
    if outcome.state == "forbidden":
        return _error(status.HTTP_403_FORBIDDEN, "forbidden")
    if outcome.state in {
        "attestation_conflict",
        "baseline_conflict",
        "blocked",
        "epoch_conflict",
        "operation_conflict",
        "stale",
    }:
        code: RepositoryAttestationErrorCode = (
            "replayed" if outcome.state == "attestation_conflict" else outcome.state
        )
        return _error(status.HTTP_409_CONFLICT, code)
    if outcome.state == "diff_limit":
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "diff_limit")
    if outcome.state == "overloaded":
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "overloaded")
    if outcome.state == "unavailable":
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
    raise RuntimeError("unsupported proposal review outcome")


def _admission_error(admission: object) -> JSONResponse | None:
    if isinstance(admission, InvalidCredential):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
    if isinstance(admission, ForbiddenIdentity):
        return _error(status.HTTP_403_FORBIDDEN, "forbidden")
    if isinstance(admission, AuthenticationDependencyUnavailable):
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
    return None


def _exact_callback_query(request: Request) -> tuple[str, str] | None:
    pairs = request.query_params.multi_items()
    if len(pairs) != 2 or {name for name, _ in pairs} != {"code", "state"}:
        return None
    values = dict(pairs)
    code = values["code"]
    state = values["state"]
    if (
        not code
        or len(code) > 512
        or not code.isascii()
        or len(state) != 43
        or not state.isascii()
        or any(not (character.isalnum() or character in "-_") for character in state)
    ):
        return None
    return code, state


def _error(status_code: int, error: RepositoryAttestationErrorCode) -> JSONResponse:
    response = RepositoryAttestationErrorResponse(ok=False, error=error)
    headers = dict(_NO_STORE)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        headers.update(WWW_AUTHENTICATE_HEADER)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )
