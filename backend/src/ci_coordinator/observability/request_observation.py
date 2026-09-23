"""HTTP metrics and logs projected from one request without owning its decision."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.observability.logging import StructuredEventLogger
from ci_coordinator.observability.runtime_metrics import RuntimeMetrics

_DIAGNOSTIC_STATE_KEY = "ci_operation_diagnostic"


@dataclass(frozen=True, slots=True)
class PlanOperationDiagnostic:
    issued_plan_record_id: str
    plan_id: str
    repository_id: int
    workflow_run_id: int
    run_attempt: int

    def __post_init__(self) -> None:
        if not self.issued_plan_record_id.startswith("issued_plan_"):
            raise ValueError("plan diagnostic requires an issued-plan record identity")
        if type(self.plan_id) is not str or not self.plan_id:
            raise ValueError("plan diagnostic requires a plan identity")
        for value in (self.repository_id, self.workflow_run_id, self.run_attempt):
            if type(value) is not int or value < 1:
                raise ValueError("plan diagnostic identifiers must be positive integers")

    def to_log_fields(self) -> dict[str, object]:
        return {
            "issuedPlanRecordId": self.issued_plan_record_id,
            "planId": self.plan_id,
            "repositoryId": self.repository_id,
            "workflowRunId": self.workflow_run_id,
            "runAttempt": self.run_attempt,
        }


def bind_plan_operation_diagnostic(scope: Scope, diagnostic: PlanOperationDiagnostic) -> None:
    if type(diagnostic) is not PlanOperationDiagnostic:
        raise TypeError("request diagnostic must be an exact plan diagnostic")
    state = scope.setdefault("state", {})
    state[_DIAGNOSTIC_STATE_KEY] = diagnostic


class HttpRequestObservationMiddleware:
    """Observe bounded route templates after the application has selected a route."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        metrics: RuntimeMetrics,
        admitted_routes: tuple[BaseRoute, ...],
        logger: StructuredEventLogger | None = None,
    ) -> None:
        if type(metrics) is not RuntimeMetrics:
            raise TypeError("HTTP observation requires exact runtime metrics")
        if type(admitted_routes) is not tuple or any(
            not isinstance(route, BaseRoute) for route in admitted_routes
        ):
            raise TypeError("HTTP observation routes must be an exact route tuple")
        self._app = app
        self._metrics = metrics
        self._admitted_routes = admitted_routes
        self._metrics.bind_http_route_templates(
            frozenset(
                template
                for route in admitted_routes
                if type(template := getattr(route, "path", None)) is str
            )
        )
        self._logger = logger

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = monotonic()
        status_code = 500

        async def observed_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self._app(scope, receive, observed_send)
        except asyncio.CancelledError:
            status_code = 499
            raise
        finally:
            self._observe(scope, status_code, monotonic() - started)

    def _observe(self, scope: Scope, status_code: int, duration_seconds: float) -> None:
        method = str(scope.get("method", "OTHER")).upper()
        route = _route_template(scope, self._admitted_routes)
        self._metrics.http_request(
            method=method,
            route=route,
            status_code=status_code,
            duration_seconds=duration_seconds,
        )
        if self._logger is None:
            return
        event: dict[str, object] = {
            "event": "http_request_completed",
            "method": method,
            "route": route,
            "statusCode": status_code,
            "durationMs": round(duration_seconds * 1_000, 3),
        }
        diagnostic = scope.get("state", {}).get(_DIAGNOSTIC_STATE_KEY)
        if type(diagnostic) is PlanOperationDiagnostic:
            event.update(diagnostic.to_log_fields())
        correlation_id = scope.get("state", {}).get("correlation_id")
        self._logger.emit(
            event,
            correlation_id=correlation_id if type(correlation_id) is str else None,
        )


def _route_template(scope: Scope, admitted_routes: tuple[BaseRoute, ...]) -> str:
    for route in admitted_routes:
        match, _child_scope = route.matches(scope)
        template = getattr(route, "path", None)
        if match is Match.FULL and type(template) is str:
            return template
    return "unmatched"
