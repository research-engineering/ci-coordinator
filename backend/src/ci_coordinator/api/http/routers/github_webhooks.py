from __future__ import annotations

from typing import Literal, Never, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.contracts import ErrorBody
from ci_coordinator.api.http.dependencies import GitHubWebhookRouteDependencies
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.api.http.webhook_ingress import (
    WebhookAdmissionUnavailable,
    WebhookIngressCommand,
    WebhookPreparationOffered,
)
from ci_coordinator.github_ingestion import (
    DeliveryUnavailable,
    DuplicateDelivery,
    IngestionRejection,
    IngestionResult,
    NoopDelivery,
    PingDelivery,
    SeedIngestion,
    UnsupportedWebhook,
    WorkflowJobObservation,
    WorkflowRunObservation,
    load_bundled_profile,
)
from ci_coordinator.identity_admission import RejectedIdentity

GITHUB_WEBHOOK_PATH = "/webhooks/github"
_IDENTITY_HEADER_NAMES = frozenset({"x-github-delivery", "x-github-event", "x-hub-signature-256"})


def github_webhook_body_limit() -> BodyLimitPolicy:
    return BodyLimitPolicy(
        path=GITHUB_WEBHOOK_PATH,
        maximum_body_bytes=load_bundled_profile().limits.maximum_body_bytes,
        invalid_content_length_response={"ok": False, "error": "invalid webhook"},
        too_large_response={"ok": False, "error": "request body too large"},
        overload_response={"ok": False, "error": "webhook ingestion overloaded"},
        timeout_response={"ok": False, "error": "webhook ingestion unavailable"},
    )


type WebhookErrorMessage = Literal[
    "invalid signature",
    "invalid webhook",
    "request body too large",
    "webhook delivery conflict",
    "webhook ingestion overloaded",
    "webhook ingestion unavailable",
]
type WebhookIgnoredReason = Literal[
    "duplicate_delivery",
    "ignored_webhook",
    "incomplete_merge_group",
    "incomplete_pull_request",
    "incomplete_push",
    "incomplete_workflow_run",
    "non_live_merge_group_head",
    "non_live_pull_request_head",
    "non_live_push_ref",
    "unsupported_event",
    "workflow_run_not_dynamic_ci_seed",
]


class WebhookProcessedResponse(ResponseModel):
    ok: Literal[True]
    processed: Literal[True]
    duplicate: Literal[False]
    effect: Literal["delivery_claim_recorded"]
    downstream: Literal["none", "best_effort_preparation"]


class WebhookIgnoredResponse(ResponseModel):
    ok: Literal[True]
    duplicate: bool
    ignored: Literal[True]
    reason: WebhookIgnoredReason


class WebhookErrorResponse(ResponseModel):
    ok: Literal[False]
    error: WebhookErrorMessage


def create_github_webhooks_router(
    dependencies: GitHubWebhookRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        GITHUB_WEBHOOK_PATH,
        operation_id="receive_github_webhook",
        response_model=WebhookProcessedResponse | WebhookIgnoredResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": WebhookErrorResponse},
            status.HTTP_401_UNAUTHORIZED: {"model": WebhookErrorResponse},
            status.HTTP_409_CONFLICT: {"model": WebhookErrorResponse},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": WebhookErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": WebhookErrorResponse},
        },
        status_code=status.HTTP_200_OK,
        openapi_extra={
            "parameters": [
                {
                    "in": "header",
                    "name": "X-Hub-Signature-256",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "in": "header",
                    "name": "X-GitHub-Delivery",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "in": "header",
                    "name": "X-GitHub-Event",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"additionalProperties": True, "type": "object"}}
                },
                "required": True,
            },
        },
    )
    async def github_webhook(request: Request) -> JSONResponse:
        headers = _duplicate_capable_headers(request)
        if headers.get("content-type") != ("application/json",) or "content-encoding" in headers:
            return _error_response(status.HTTP_400_BAD_REQUEST, "invalid webhook")
        raw_body = await request.body()
        result = await dependencies.webhook_ingress(
            WebhookIngressCommand(
                headers=tuple(
                    (name, values)
                    for name, values in sorted(headers.items())
                    if name in _IDENTITY_HEADER_NAMES
                ),
                raw_body=raw_body,
            )
        )
        if isinstance(result, RejectedIdentity):
            return _error_response(status.HTTP_401_UNAUTHORIZED, "invalid signature")
        return _application_response(result)

    return router


def _duplicate_capable_headers(request: Request) -> dict[str, tuple[str, ...]]:
    # ASGI preserves field lines that higher-level header mappings may coalesce.
    raw_headers = cast(list[tuple[bytes, bytes]], request.scope.get("headers", []))
    values_by_name: dict[str, list[str]] = {}
    for raw_name, raw_value in raw_headers:
        name = raw_name.decode("latin-1").lower()
        values_by_name.setdefault(name, []).append(raw_value.decode("latin-1"))
    return {name: tuple(values) for name, values in values_by_name.items()}


def _application_response(
    result: IngestionResult | WebhookAdmissionUnavailable | WebhookPreparationOffered,
) -> JSONResponse:
    if isinstance(
        result,
        SeedIngestion | WorkflowRunObservation | WorkflowJobObservation | WebhookPreparationOffered,
    ):
        response = WebhookProcessedResponse(
            ok=True,
            processed=True,
            duplicate=False,
            effect="delivery_claim_recorded",
            downstream=(
                "best_effort_preparation"
                if isinstance(result, WebhookPreparationOffered)
                else "none"
            ),
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=response.to_wire_mapping())
    if isinstance(result, NoopDelivery):
        return _ignored_response(False, _redacted_ignored_reason(result.reason_code))
    if isinstance(result, PingDelivery):
        return _ignored_response(False, "ignored_webhook")
    if isinstance(result, UnsupportedWebhook):
        return _ignored_response(False, _redacted_ignored_reason(result.reason_code))
    if isinstance(result, DuplicateDelivery):
        return _ignored_response(True, "duplicate_delivery")
    if isinstance(result, DeliveryUnavailable):
        return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook ingestion unavailable")
    if isinstance(result, WebhookAdmissionUnavailable):
        return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook ingestion unavailable")
    if isinstance(result, IngestionRejection):
        return _rejection_response(result)
    return _unsupported_result(result)


def _rejection_response(result: IngestionRejection) -> JSONResponse:
    if result.code == "delivery_id_conflict":
        return _error_response(status.HTTP_409_CONFLICT, "webhook delivery conflict")
    if result.code == "delivery_idempotency_unavailable":
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "webhook ingestion unavailable",
        )
    if result.code == "delivery_idempotency_contract_violation":
        raise RuntimeError("delivery idempotency contract violation")
    return _error_response(status.HTTP_400_BAD_REQUEST, "invalid webhook")


def _ignored_response(duplicate: bool, reason: WebhookIgnoredReason) -> JSONResponse:
    response = WebhookIgnoredResponse(
        ok=True,
        duplicate=duplicate,
        ignored=True,
        reason=reason,
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content=response.to_wire_mapping())


def _error_response(status_code: int, error: WebhookErrorMessage) -> JSONResponse:
    response = WebhookErrorResponse(ok=False, error=error)
    return JSONResponse(status_code=status_code, content=response.to_wire_mapping())


def _redacted_ignored_reason(reason_code: str) -> WebhookIgnoredReason:
    match reason_code:
        case (
            "incomplete_merge_group"
            | "incomplete_pull_request"
            | "incomplete_push"
            | "incomplete_workflow_run"
            | "non_live_merge_group_head"
            | "non_live_pull_request_head"
            | "non_live_push_ref"
            | "unsupported_event"
            | "workflow_run_not_dynamic_ci_seed"
        ):
            return reason_code
        case _:
            return "ignored_webhook"


def _unsupported_result(_: Never) -> Never:
    raise RuntimeError("unsupported webhook ingress result")
