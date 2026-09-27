"""HTTP metrics and logs projected from one request without owning its decision."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import Literal

from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.observability.logging import StructuredEventLogger
from ci_coordinator.observability.runtime_metrics import RuntimeMetrics

_DIAGNOSTIC_STATE_KEY = "ci_operation_diagnostic"
_OBSERVATION_STATE_KEY = "ci_request_observation"

type PlanRouteResult = Literal[
    "invalid",
    "unauthenticated",
    "forbidden",
    "conflict",
    "dependency_unavailable",
    "issuance_unavailable",
    "issued",
]
type RequestTermination = Literal[
    "completed",
    "work_timeout",
    "response_timeout",
    "cancelled",
    "exception",
    "incomplete",
]


@dataclass(slots=True)
class RequestObservation:
    plan_authenticated: bool = False
    plan_route_result: PlanRouteResult | None = None
    work_timeout: asyncio.Timeout | None = None
    hard_timeout: asyncio.Timeout | None = None
    owned_expiry: Literal["work_timeout", "response_timeout"] | None = None
    status_code: int | None = None
    trailers_expected: bool = False
    body_completed: bool = False
    completed_duration: float | None = None
    completed_plan_result: str | None = None
    cancelled: bool = False
    send_failed: bool = False
    unexpected_error: bool = False

    def capture_expiry(self) -> None:
        if self.completed_duration is not None or self.owned_expiry is not None:
            return
        if self.work_timeout is not None and self.work_timeout.expired():
            self.owned_expiry = "work_timeout"
        elif self.hard_timeout is not None and self.hard_timeout.expired():
            self.owned_expiry = "response_timeout"

    def complete(self, duration: float) -> None:
        if self.completed_duration is not None:
            return
        self.capture_expiry()
        self.completed_plan_result = self._plan_result(completed=True)
        self.completed_duration = duration

    def sent(self, message: Message, duration: float) -> None:
        if self.completed_duration is not None:
            return
        if message["type"] == "http.response.start":
            self.status_code = message["status"]
            self.trailers_expected = message.get("trailers", False)
        elif self.status_code is not None:
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self.body_completed = True
                if not self.trailers_expected:
                    self.complete(duration)
            elif (
                message["type"] == "http.response.trailers"
                and self.trailers_expected
                and self.body_completed
                and not message.get("more_trailers", False)
            ):
                self.complete(duration)

    def plan_result(self) -> str | None:
        if self.completed_duration is not None:
            return self.completed_plan_result
        return self._plan_result(completed=False)

    def _plan_result(self, *, completed: bool) -> str | None:
        result = self.plan_route_result
        if result in {"invalid", "unauthenticated", "forbidden", "conflict"}:
            return result
        if not self.plan_authenticated and result != "dependency_unavailable":
            return None
        if self.owned_expiry is not None:
            return "timed_out"
        if self.cancelled:
            return "cancelled"
        if self.send_failed:
            return "response_failed"
        if self.unexpected_error or (completed and self.status_code == 500):
            return "internal_error"
        if result in {"dependency_unavailable", "issuance_unavailable"}:
            return result
        if completed:
            if (
                result == "issued"
                and self.status_code is not None
                and 200 <= self.status_code < 300
            ):
                return "issued"
            return "internal_error"
        return "response_failed"

    def termination(self) -> RequestTermination:
        if self.owned_expiry is not None:
            return self.owned_expiry
        if self.completed_duration is not None:
            return "completed"
        if self.cancelled:
            return "cancelled"
        if self.send_failed or self.unexpected_error:
            return "exception"
        return "incomplete"


def scope_request_observation(scope: Scope) -> RequestObservation | None:
    value = scope.get("state", {}).get(_OBSERVATION_STATE_KEY)
    return value if type(value) is RequestObservation else None


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
        metrics: RuntimeMetrics | None,
        admitted_routes: tuple[BaseRoute, ...],
        logger: StructuredEventLogger | None = None,
        plan_metrics: RuntimeMetrics | None = None,
    ) -> None:
        if metrics is not None and type(metrics) is not RuntimeMetrics:
            raise TypeError("HTTP observation requires exact runtime metrics")
        if plan_metrics is not None and type(plan_metrics) is not RuntimeMetrics:
            raise TypeError("plan observation requires exact runtime metrics")
        if type(admitted_routes) is not tuple or any(
            not isinstance(route, BaseRoute) for route in admitted_routes
        ):
            raise TypeError("HTTP observation routes must be an exact route tuple")
        self._app = app
        self._metrics = metrics
        self._plan_metrics = plan_metrics
        self._admitted_routes = admitted_routes
        if metrics is not None:
            metrics.bind_http_route_templates(
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
        observation = RequestObservation()
        scope.setdefault("state", {})[_OBSERVATION_STATE_KEY] = observation
        observation_failed = False

        async def observed_send(message: Message) -> None:
            nonlocal observation_failed
            try:
                await send(message)
            except asyncio.CancelledError:
                if observation.completed_duration is None:
                    observation.cancelled = True
                raise
            except Exception:
                if observation.completed_duration is None:
                    observation.send_failed = True
                raise
            try:
                observation.sent(message, monotonic() - started)
            except Exception:
                observation_failed = True

        try:
            await self._app(scope, receive, observed_send)
        except asyncio.CancelledError:
            if observation.completed_duration is None:
                observation.cancelled = True
            raise
        except Exception:
            if observation.completed_duration is None:
                observation.unexpected_error = True
            raise
        finally:
            if not observation_failed:
                with suppress(Exception):
                    observation.capture_expiry()
                    self._observe(scope, observation, monotonic() - started)

    def _observe(
        self, scope: Scope, observation: RequestObservation, duration_seconds: float
    ) -> None:
        plan_result = observation.plan_result()
        if self._plan_metrics is not None and plan_result is not None:
            with suppress(Exception):
                self._plan_metrics.plan_request(plan_result)
        method = str(scope.get("method", "OTHER")).upper()
        route = _route_template(scope, self._admitted_routes)
        if self._metrics is not None:
            histogram_duration: float | None = duration_seconds
            if method == "POST" and route == "/api/v1/dynamic-ci/plan":
                histogram_duration = (
                    observation.completed_duration if plan_result == "issued" else None
                )
            with suppress(Exception):
                self._metrics.http_request(
                    method=method,
                    route=route,
                    status_code=observation.status_code,
                    duration_seconds=histogram_duration,
                )
        if self._logger is None:
            return
        event: dict[str, object] = {
            "event": "http_request_completed",
            "method": method,
            "route": route,
            "statusCode": observation.status_code,
            "responseCompleted": observation.completed_duration is not None,
            "termination": observation.termination(),
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
