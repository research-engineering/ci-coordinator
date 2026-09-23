from __future__ import annotations

import logging
import re
from contextlib import nullcontext
from dataclasses import dataclass, fields, replace
from http import HTTPMethod
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from prometheus_support import prometheus_samples
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    ObservabilityRouteDependencies,
)
from ci_coordinator.api.http.metrics_authentication import StaticMetricsBearerAuthenticator
from ci_coordinator.api.http.routers.ci_economics import CI_ECONOMICS_JOBS_PATH
from ci_coordinator.observability import ReadinessStatus, RuntimeMetrics, StructuredEventLogger
from ci_coordinator.observability.request_observation import HttpRequestObservationMiddleware


@dataclass
class ReadinessUseCase:
    status: ReadinessStatus

    async def __call__(self) -> ReadinessStatus:
        return self.status


class FailingLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise RuntimeError("log sink unavailable")


def test_liveness_has_no_dependency_precondition() -> None:
    client = _client(ReadinessStatus(False, ("database",)), RuntimeMetrics())

    for path in ("/healthz", "/api/v1/health"):
        response = client.get(path)

        assert response.status_code == 200
        assert response.json() == {"ok": True, "status": "alive"}


def test_readiness_fails_closed_when_a_required_dependency_is_unavailable() -> None:
    client = _client(ReadinessStatus(False, ("database",)), RuntimeMetrics())

    for path in ("/readyz", "/api/v1/ready"):
        response = client.get(path)

        assert response.status_code == 503
        assert response.json() == {
            "ok": False,
            "status": "not_ready",
        }
        assert response.headers["cache-control"] == "no-store"


def test_ready_response_cannot_be_reused_after_dependency_state_changes() -> None:
    client = _client(ReadinessStatus(True, ()), RuntimeMetrics())

    for path in ("/readyz", "/api/v1/ready"):
        response = client.get(path)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_metrics_are_a_canonical_read_only_projection() -> None:
    metrics = RuntimeMetrics()
    metrics.full_ci_fallback("verified_plan_unavailable")
    client = _client(ReadinessStatus(True, ()), metrics)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/plain; version=1.0.0")
    assert (
        'ci_coordinator_full_ci_fallbacks_total{reason="verified_plan_unavailable"} 1.0'
        in response.text
    )


def test_connected_metrics_require_the_exact_metrics_bearer() -> None:
    token = "m" * 32
    client = _client(
        ReadinessStatus(True, ()),
        RuntimeMetrics(),
        metrics_authenticator=StaticMetricsBearerAuthenticator(token),
    )

    for headers in (
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Bearer {'n' * 32}"},
    ):
        response = client.get("/metrics", headers=headers)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["www-authenticate"] == "Bearer"

    admitted = client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
    assert admitted.status_code == 200
    assert admitted.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Basic " + b"m" * 32)],
        [(b"authorization", b"Bearer short")],
        [(b"authorization", b"Bearer " + b"m" * 32 + b" ")],
        [
            (b"authorization", b"Bearer " + b"m" * 32),
            (b"authorization", b"Bearer " + b"m" * 32),
        ],
    ],
)
def test_metrics_authentication_rejects_every_noncanonical_header_set(
    headers: list[tuple[bytes, bytes]],
) -> None:
    authenticator = StaticMetricsBearerAuthenticator("m" * 32)
    request = Request({"type": "http", "headers": headers})

    assert not authenticator.authenticate(request)


@pytest.mark.parametrize("token", ("m" * 31, "m" * 31 + " ", "m" * 31 + "\n"))
def test_metrics_authenticator_rejects_unrepresentable_configured_tokens(token: str) -> None:
    with pytest.raises(ValueError, match="header-safe"):
        StaticMetricsBearerAuthenticator(token)


def test_observability_openapi_exposes_only_read_endpoints() -> None:
    app = create_app(
        HttpRouteDependencies(
            observability=ObservabilityRouteDependencies(
                readiness=ReadinessUseCase(ReadinessStatus(True, ())),
                metrics=RuntimeMetrics(),
            )
        )
    )
    paths = app.openapi()["paths"]

    assert set(paths) == {
        "/api/v1/health",
        "/api/v1/ready",
        "/healthz",
        "/readyz",
        "/metrics",
    }
    assert all(set(item) == {"get"} for item in paths.values())


def test_http_observation_uses_the_matched_route_template() -> None:
    metrics = RuntimeMetrics()
    client = _client(ReadinessStatus(True, ()), metrics)

    assert client.get("/api/v1/health").status_code == 200

    samples = prometheus_samples(metrics)
    assert (
        samples[
            (
                "ci_coordinator_http_requests_total",
                (("method", "GET"), ("route", "/api/v1/health"), ("status_class", "2xx")),
            )
        ]
        == 1
    )


@pytest.mark.parametrize(
    "template",
    [
        "/api/v1/economics/repositories/{installation_id}/{repository_id}"
        "/attempts/{workflow_run_id}/{run_attempt}/jobs",
        "/configuration/{epoch_id}",
        "/authority/{authority_id}",
        "/future/{new_parameter}",
        "/" + "namespace/" * 30 + "{identifier}",
    ],
)
def test_every_captured_template_retains_identity_without_concrete_path_labels(
    template: str,
) -> None:
    async def endpoint(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    routes = (Route(template, endpoint, methods=["GET"]),)
    metrics = RuntimeMetrics()
    client = TestClient(
        HttpRequestObservationMiddleware(
            Starlette(routes=routes), metrics=metrics, admitted_routes=routes
        )
    )
    for identifier in ("123", "456"):
        path = re.sub(r"\{[^}]+}", identifier, template)
        assert client.get(path, params={"token": "must-not-appear"}).status_code == 200
        assert client.get(f"/unregistered/{identifier}").status_code == 404

    labels = {
        dict(dimensions)["route"]
        for name, dimensions in prometheus_samples(metrics)
        if name == "ci_coordinator_http_requests_total"
    }
    assert labels == {template, "unmatched"}


@pytest.mark.parametrize("omit_product_template", [False, True])
def test_mounted_public_catalog_matches_metrics_and_detects_a_missing_capture(
    omit_product_template: bool,
) -> None:
    metrics = RuntimeMetrics()
    cookie_settings = SimpleNamespace(transaction_cookie_name="fixture", secure_cookies=True)
    dependencies = HttpRouteDependencies(
        **{field.name: cast(Any, cookie_settings) for field in fields(HttpRouteDependencies)}
    )
    app = create_app(
        replace(
            dependencies,
            observability=ObservabilityRouteDependencies(
                readiness=ReadinessUseCase(ReadinessStatus(True, ())), metrics=metrics
            ),
        ),
        include_operator_ui=False,
    )
    public_routes = app.openapi()["paths"]
    assert CI_ECONOMICS_JOBS_PATH in public_routes
    if omit_product_template:
        observation = next(
            middleware
            for middleware in app.user_middleware
            if cast(object, middleware.cls) is HttpRequestObservationMiddleware
        )
        observation.kwargs["admitted_routes"] = tuple(
            route
            for route in cast(tuple[BaseRoute, ...], observation.kwargs["admitted_routes"])
            if getattr(route, "path", None) != CI_ECONOMICS_JOBS_PATH
        )
    with TestClient(app, raise_server_exceptions=False) as client:
        for template, operations in public_routes.items():
            for value in ("101", "202"):
                path = re.sub(r"\{[^}]+}", value, template)
                for method in operations:
                    if method.upper() in HTTPMethod:
                        client.request(method, path)
                client.get("/unknown/" + value)
    actual_routes = {
        dict(labels)["route"]
        for name, labels in prometheus_samples(metrics)
        if name == "ci_coordinator_http_requests_total"
    }
    oracle = (
        pytest.raises(AssertionError, match="mounted public templates differ")
        if omit_product_template
        else nullcontext()
    )
    with oracle:
        assert actual_routes == set(public_routes) | {"unmatched"}, (
            "mounted public templates differ"
        )


def test_logging_failure_cannot_change_the_http_outcome() -> None:
    logger = logging.Logger("failing-observability-test")
    logger.addHandler(FailingLogHandler())
    client = _client(
        ReadinessStatus(True, ()),
        RuntimeMetrics(),
        request_logger=StructuredEventLogger(logger),
    )

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "alive"}


def _client(
    readiness: ReadinessStatus,
    metrics: RuntimeMetrics,
    *,
    request_logger: StructuredEventLogger | None = None,
    metrics_authenticator: StaticMetricsBearerAuthenticator | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                observability=ObservabilityRouteDependencies(
                    readiness=ReadinessUseCase(readiness),
                    metrics=metrics,
                    request_logger=request_logger,
                    metrics_authenticator=metrics_authenticator,
                )
            )
        )
    )
