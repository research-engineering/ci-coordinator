from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import types
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from io import StringIO
from threading import Barrier
from typing import Any, Literal, cast, get_args

import pytest
from _pytest.logging import LogCaptureHandler, catching_logs
from prometheus_client import Counter
from prometheus_support import SampleKey, prometheus_samples
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

import ci_coordinator
from ci_coordinator.api.http.errors import UnexpectedErrorMiddleware
from ci_coordinator.app.config_management import ConfigActivationState
from ci_coordinator.observability import (
    BackgroundHealth,
    BackgroundHealthState,
    DependencyReadiness,
    HttpRequestObservationMiddleware,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    StructuredEventLogger,
    assess_readiness,
    health,
    redacted_log_event,
    scope_request_observation,
)
from ci_coordinator.observability import logging as event_logging
from ci_coordinator.observability.runtime_metrics import (
    CI_ECONOMICS_COLLECTION_METRIC_OUTCOMES,
    CiEconomicsCollectionItemMetricOutcome,
)

_ACTIVATION_STATES = (
    "applied",
    "attestation_invalid",
    "coverage_reducing",
    "coverage_unproven",
    "duplicate",
    "forbidden",
    "legacy_new_operation_unsupported",
    "operation_conflict",
    "revision_conflict",
    "target_never_activated",
    "target_unavailable",
    "unavailable",
)
_HISTORY_FAILURE_BASELINES: dict[SampleKey, float] = {
    **{
        ("ci_coordinator_ci_history_items_total", (("lane", lane), ("outcome", outcome))): 0
        for lane in ("backfill", "discovery", "recent", "repair")
        for outcome in ("store_unavailable", "unexpected_error")
    },
    **{
        (
            "ci_coordinator_ci_history_provider_results_total",
            (("lane", lane), ("outcome", outcome)),
        ): 0
        for lane in ("backfill", "discovery", "recent", "repair")
        for outcome in (
            "access_unavailable",
            "timed_out",
            "provider_unavailable",
            "provider_binding_mismatch",
            "provider_malformed",
            "provider_incomplete",
            "provider_unstable",
        )
    },
    **{
        ("ci_coordinator_ci_history_delivery_outcomes_total", (("outcome", outcome),)): 0
        for outcome in ("store_unavailable", "unexpected_error", "timed_out")
    },
}
_HTTP_SAMPLE_NAMES = (
    "ci_coordinator_http_requests_total",
    "ci_coordinator_http_request_duration_seconds_bucket",
    "ci_coordinator_http_request_duration_seconds_count",
    "ci_coordinator_http_request_duration_seconds_sum",
)


def _samples_for(metrics: RuntimeMetrics, *names: str) -> dict[SampleKey, float]:
    return {key: value for key, value in prometheus_samples(metrics).items() if key[0] in names}


@pytest.mark.parametrize("result", ["internal_error", "timed_out", "response_failed", "cancelled"])
def test_new_plan_results_have_real_zero_baselines_and_independent_increments(result: str) -> None:
    metrics = RuntimeMetrics()
    names = ("internal_error", "timed_out", "response_failed", "cancelled")
    assert all(
        prometheus_samples(metrics)[
            (
                "ci_coordinator_plan_requests_total",
                (("result", name),),
            )
        ]
        == 0
        for name in names
    )
    metrics.plan_request(result)
    assert {
        name: prometheus_samples(metrics)[
            (
                "ci_coordinator_plan_requests_total",
                (("result", name),),
            )
        ]
        for name in names
    } == {name: int(name == result) for name in names}


@pytest.mark.parametrize(
    ("result", "authenticated"),
    [("invalid", False), ("unauthenticated", False), ("forbidden", False), ("conflict", True)],
)
@pytest.mark.parametrize(
    ("cancelled", "send_failed", "unexpected_error", "owned_expiry", "eligible_result"),
    [
        (False, False, False, None, "response_failed"),
        (True, False, False, None, "cancelled"),
        (False, True, False, None, "response_failed"),
        (False, False, True, None, "internal_error"),
        (False, False, False, "work_timeout", "timed_out"),
        (False, False, False, "response_timeout", "timed_out"),
    ],
    ids=["incomplete", "cancel", "send", "unexpected", "work-expiry", "response-expiry"],
)
def test_incomplete_plan_exclusions_precede_each_isolated_fault_projection(
    result: Literal["invalid", "unauthenticated", "forbidden", "conflict"],
    authenticated: bool,
    cancelled: bool,
    send_failed: bool,
    unexpected_error: bool,
    owned_expiry: Literal["work_timeout", "response_timeout"] | None,
    eligible_result: str,
) -> None:
    from ci_coordinator.observability.request_observation import RequestObservation

    excluded = RequestObservation(
        plan_authenticated=authenticated,
        plan_route_result=result,
        cancelled=cancelled,
        send_failed=send_failed,
        unexpected_error=unexpected_error,
        owned_expiry=owned_expiry,
    )
    eligible = RequestObservation(
        plan_authenticated=True,
        plan_route_result="issued",
        cancelled=cancelled,
        send_failed=send_failed,
        unexpected_error=unexpected_error,
        owned_expiry=owned_expiry,
    )
    assert excluded.plan_result() == result
    assert eligible.plan_result() == eligible_result


def test_unknown_status_and_absent_duration_do_not_fabricate_status_or_latency() -> None:
    metrics = RuntimeMetrics()
    metrics.bind_http_route_templates(frozenset({"/known"}))
    metrics.http_request(method="GET", route="/known", status_code=None, duration_seconds=None)
    labels = (("method", "GET"), ("route", "/known"), ("status_class", "unknown"))
    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_http_requests_total", labels)] == 1
    assert ("ci_coordinator_http_request_duration_seconds_count", labels) not in samples


def test_non_http_observation_is_an_identity_passthrough() -> None:
    async def scenario() -> None:
        metrics = RuntimeMetrics()
        scope: Scope = {"type": "websocket", "path": "/socket"}
        message: Message = {"type": "websocket.close", "code": 1000}

        async def receive() -> Message:
            raise AssertionError("unused")

        async def send(actual: Message) -> None:
            assert actual is message

        async def downstream(actual: Scope, actual_receive: Receive, actual_send: Send) -> None:
            assert actual is scope and actual_receive is receive and actual_send is send
            await actual_send(message)

        await HttpRequestObservationMiddleware(
            downstream,
            metrics=metrics,
            admitted_routes=(),
            plan_metrics=metrics,
        )(scope, receive, send)
        assert "state" not in scope
        assert not any(
            name == "ci_coordinator_http_requests_total" for name, _ in prometheus_samples(metrics)
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["no_start", "partial", "trailers_pending", "trailers_complete"])
def test_response_completion_requires_successful_start_and_terminal_message(case: str) -> None:
    async def scenario() -> None:
        metrics = RuntimeMetrics()
        output = StringIO()
        logger = logging.Logger("completion-boundary")
        logger.addHandler(logging.StreamHandler(output))
        delivered: list[Message] = []

        async def downstream(scope: Scope, _: Receive, send: Send) -> None:
            observation = scope_request_observation(scope)
            assert observation is not None
            observation.plan_authenticated = True
            observation.plan_route_result = "issued"
            if case != "no_start":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "trailers": case.startswith("trailers"),
                        "headers": [],
                    }
                )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"same bytes",
                    "more_body": case == "partial",
                }
            )
            if case == "trailers_complete":
                await send({"type": "http.response.trailers", "headers": [], "more_trailers": True})
                assert observation.completed_duration is None
                await send({"type": "http.response.trailers", "headers": []})

        async def receive() -> Message:
            raise AssertionError("observer must not read")

        async def send(message: Message) -> None:
            delivered.append(message)

        await HttpRequestObservationMiddleware(
            downstream,
            metrics=metrics,
            plan_metrics=metrics,
            admitted_routes=(),
            logger=StructuredEventLogger(logger),
        )({"type": "http", "method": "POST", "path": "/plan"}, receive, send)
        expected = "issued" if case == "trailers_complete" else "response_failed"
        counts = {
            dict(labels)["result"]: value
            for (name, labels), value in prometheus_samples(metrics).items()
            if name == "ci_coordinator_plan_requests_total" and value
        }
        assert counts == {expected: 1}
        record = json.loads(output.getvalue())
        assert record["responseCompleted"] is (case == "trailers_complete")
        assert record["statusCode"] == (None if case == "no_start" else 200)
        assert record["termination"] == (
            "completed" if case == "trailers_complete" else "incomplete"
        )
        assert (
            next(m["body"] for m in delivered if m["type"] == "http.response.body") == b"same bytes"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_projection", ["plan", "http", "terminal", "send_observation"])
def test_observation_failure_preserves_primary_error_and_independent_sinks(
    monkeypatch: pytest.MonkeyPatch,
    failed_projection: str,
) -> None:
    from ci_coordinator.observability.request_observation import RequestObservation

    async def scenario() -> None:
        plan_metrics, http_metrics = RuntimeMetrics(), RuntimeMetrics()
        sent: list[Message] = []
        primary = LookupError("primary failure after complete response")

        def telemetry_failure(*_: object, **__: object) -> None:
            raise ValueError("instrumentation")

        if failed_projection == "plan":
            monkeypatch.setattr(plan_metrics, "plan_request", telemetry_failure)
        elif failed_projection == "http":
            monkeypatch.setattr(http_metrics, "http_request", telemetry_failure)
        elif failed_projection == "terminal":
            monkeypatch.setattr(HttpRequestObservationMiddleware, "_observe", telemetry_failure)
        else:
            monkeypatch.setattr(RequestObservation, "sent", telemetry_failure)

        async def downstream(scope: Scope, _: Receive, send: Send) -> None:
            observation = scope_request_observation(scope)
            assert observation is not None
            observation.plan_authenticated = True
            observation.plan_route_result = "issued"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"kept"})
            raise primary

        async def receive() -> Message:
            raise AssertionError("no body read")

        async def send(message: Message) -> None:
            sent.append(message)

        with pytest.raises(LookupError) as caught:
            await HttpRequestObservationMiddleware(
                downstream,
                metrics=http_metrics,
                plan_metrics=plan_metrics,
                admitted_routes=(),
            )({"type": "http", "method": "POST", "path": "/plan"}, receive, send)
        assert caught.value is primary
        assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
        if failed_projection == "send_observation":
            assert not any(
                value
                for (name, _), value in prometheus_samples(plan_metrics).items()
                if name == "ci_coordinator_plan_requests_total"
            )
        if failed_projection == "http":
            assert (
                prometheus_samples(plan_metrics)[
                    (
                        "ci_coordinator_plan_requests_total",
                        (("result", "issued"),),
                    )
                ]
                == 1
            )
        if failed_projection == "plan":
            assert (
                prometheus_samples(http_metrics)[
                    (
                        "ci_coordinator_http_requests_total",
                        (("method", "POST"), ("route", "unmatched"), ("status_class", "2xx")),
                    )
                ]
                == 1
            )

    asyncio.run(scenario())


def test_plan_latency_ends_at_send_completion_not_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    import ci_coordinator.observability.request_observation as observation_module

    ticks = iter((100.0, 101.0, 104.0, 110.0))
    monkeypatch.setattr(observation_module, "monotonic", lambda: next(ticks))
    metrics = RuntimeMetrics()

    async def downstream(scope: Scope, _: Receive, send: Send) -> None:
        observation = scope_request_observation(scope)
        assert observation is not None
        observation.plan_authenticated = True
        observation.plan_route_result = "issued"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
        raise RuntimeError("cleanup")

    async def receive() -> Message:
        raise AssertionError("no read")

    async def send(_: Message) -> None:
        pass

    with pytest.raises(RuntimeError, match="cleanup"):
        asyncio.run(
            HttpRequestObservationMiddleware(
                downstream,
                metrics=metrics,
                plan_metrics=metrics,
                admitted_routes=(Route("/api/v1/dynamic-ci/plan", downstream, methods=["POST"]),),
            )({"type": "http", "method": "POST", "path": "/api/v1/dynamic-ci/plan"}, receive, send)
        )
    labels = (("method", "POST"), ("route", "/api/v1/dynamic-ci/plan"), ("status_class", "2xx"))
    assert (
        prometheus_samples(metrics)[("ci_coordinator_http_request_duration_seconds_sum", labels)]
        == 4
    )
    assert (
        prometheus_samples(metrics)[("ci_coordinator_http_request_duration_seconds_count", labels)]
        == 1
    )


class _ExplodingMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        del key
        raise RuntimeError("secret mapping failure")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("secret mapping failure")

    def __len__(self) -> int:
        return 1


def test_health_is_liveness_only() -> None:
    assert health().state == "alive"


def test_readiness_fails_closed_for_unavailable_required_dependency() -> None:
    status = assess_readiness(
        (
            DependencyReadiness("database", False, True),
            DependencyReadiness("github", False, False),
        )
    )

    assert status.ready is False
    assert status.unavailable_dependencies == ("database",)


@pytest.mark.parametrize("prior", [False, True])
def test_unresolved_readiness_removes_only_its_named_sample(prior: bool) -> None:
    metrics = RuntimeMetrics()
    metrics.dependency_ready("oidc_jwks")
    metrics.plan_request("issued")
    if prior:
        metrics.dependency_ready("database")
    else:
        metrics.readiness(ready=False, unavailable_dependencies=("database",))
    before = prometheus_samples(metrics)
    metrics.dependency_unresolved("database")
    metrics.dependency_unresolved("database")
    after = prometheus_samples(metrics)
    removed = ("ci_coordinator_dependency_ready", (("dependency", "database"),))
    assert removed not in after
    assert {
        key: value
        for key, value in before.items()
        if key != removed and key[0].startswith("ci_coordinator_")
    } == {key: value for key, value in after.items() if key[0].startswith("ci_coordinator_")}


def test_unresolved_metric_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = RuntimeMetrics()

    def fail_remove(*_: str) -> None:
        raise RuntimeError("private metric canary")

    monkeypatch.setattr(metrics._dependency_ready, "remove", fail_remove)
    metrics.dependency_unresolved("database")
    assert (
        prometheus_samples(metrics)[
            (
                "ci_coordinator_instrumentation_failures_total",
                (("surface", "dependency_readiness"),),
            )
        ]
        == 1
    )


@pytest.mark.parametrize("reason", ("deterministic_plan_mismatch", "omission_proof_mismatch"))
def test_deterministic_verifier_reasons_keep_their_bounded_metric_identity(reason: str) -> None:
    metrics = RuntimeMetrics()
    metrics.full_ci_fallback(reason)
    metrics.verifier_rejection(reason)
    samples = prometheus_samples(metrics)

    for metric in (
        "ci_coordinator_full_ci_fallbacks_total",
        "ci_coordinator_verifier_rejections_total",
    ):
        assert samples[(metric, (("reason", reason),))] == 1
        assert samples[(metric, (("reason", "other"),))] == 0


def test_runtime_metrics_project_bounded_operational_signals() -> None:
    metrics = RuntimeMetrics()

    metrics.plan_request("issued")
    metrics.full_ci_fallback("planner_rejected:unsafe diff")
    metrics.verifier_rejection("unbounded-provider-message")
    metrics.signed_envelope(duplicate=False)
    metrics.invalid_oidc("oidc_jwks_fetch_timeout")
    metrics.config_activation("revision_conflict")
    metrics.config_activation("coverage_reducing")
    metrics.config_activation("coverage_unproven")
    metrics.shadow_comparison("unsafe")
    metrics.shadow_comparison("replay_mismatch")
    metrics.runner_snapshot("fresh")
    metrics.capacity_decision("conservative", "runner_capacity_stale")
    metrics.planning_unavailable("candidate", "candidate_exception")
    metrics.github_unavailable("diff.compare", "timeout")

    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_plan_requests_total", (("result", "issued"),))] == 1
    assert (
        samples[("ci_coordinator_full_ci_fallbacks_total", (("reason", "planner_rejected"),))] == 1
    )
    assert samples[("ci_coordinator_verifier_rejections_total", (("reason", "other"),))] == 1
    assert samples[("ci_coordinator_signed_envelopes_total", (("result", "created"),))] == 1
    assert samples[("ci_coordinator_invalid_oidc_total", (("reason", "jwks_fetch_timeout"),))] == 1
    assert (
        samples[("ci_coordinator_config_activations_total", (("result", "revision_conflict"),))]
        == 1
    )
    assert samples[("ci_coordinator_config_activation_cas_conflicts_total", ())] == 1
    assert samples[("ci_coordinator_shadow_unsafe_omissions_total", ())] == 1
    assert samples[("ci_coordinator_shadow_replay_mismatches_total", ())] == 1
    assert samples[("ci_coordinator_runner_snapshots_total", (("state", "fresh"),))] == 1
    assert (
        samples[
            (
                "ci_coordinator_capacity_decisions_total",
                (("mode", "conservative"), ("reason", "runner_capacity_stale")),
            )
        ]
        == 1
    )
    assert (
        samples[
            (
                "ci_coordinator_planning_unavailable_total",
                (("reason", "candidate_exception"), ("stage", "candidate")),
            )
        ]
        == 1
    )
    assert (
        samples[
            (
                "ci_coordinator_github_api_unavailable_total",
                (("reason", "timeout"), ("surface", "diff")),
            )
        ]
        == 1
    )


def test_activation_metric_literals_cover_the_application_contract() -> None:
    assert set(_ACTIVATION_STATES) == set(get_args(ConfigActivationState.__value__))


@pytest.mark.parametrize("result", (*_ACTIVATION_STATES, "other", "not-admitted"))
def test_activation_exports_zero_before_each_exact_outcome(result: str) -> None:
    metrics = RuntimeMetrics()
    name = "ci_coordinator_config_activations_total"
    expected = {(name, (("result", state),)): 0 for state in (*_ACTIVATION_STATES, "other")}
    assert _samples_for(metrics, name) == expected

    metrics.config_activation(result)

    expected[(name, (("result", result if result in _ACTIVATION_STATES else "other"),))] = 1
    assert _samples_for(metrics, name) == expected
    assert prometheus_samples(metrics)[
        ("ci_coordinator_config_activation_cas_conflicts_total", ())
    ] == int(result == "revision_conflict")


@pytest.mark.parametrize(("name", "labels"), _HISTORY_FAILURE_BASELINES)
def test_history_failure_exports_zero_before_first_and_third_events(
    name: str, labels: tuple[tuple[str, str], ...]
) -> None:
    metrics = RuntimeMetrics()
    names = {key[0] for key in _HISTORY_FAILURE_BASELINES}
    expected = _HISTORY_FAILURE_BASELINES.copy()
    assert len(expected) == 39
    assert _samples_for(metrics, *names) == expected
    assert not any(
        key[0] == "ci_coordinator_ci_history_worker_running" for key in prometheus_samples(metrics)
    )
    values = dict(labels)
    if name == "ci_coordinator_ci_history_items_total":
        record = partial(metrics.ci_history_item, values["lane"], values["outcome"])
    elif name == "ci_coordinator_ci_history_provider_results_total":
        record = partial(metrics.ci_history_provider_result, values["lane"], values["outcome"])
    else:
        record = partial(metrics.ci_history_delivery, values["outcome"])

    for count in (1, 2, 3):
        record()
        expected[(name, labels)] = count
        assert _samples_for(metrics, *names) == expected


@pytest.mark.parametrize(
    ("name", "labels"),
    (
        ("ci_coordinator_config_activations_total", (("result", "attestation_invalid"),)),
        (
            "ci_coordinator_ci_history_items_total",
            (("lane", "backfill"), ("outcome", "store_unavailable")),
        ),
        (
            "ci_coordinator_ci_history_provider_results_total",
            (("lane", "backfill"), ("outcome", "access_unavailable")),
        ),
        ("ci_coordinator_ci_history_delivery_outcomes_total", (("outcome", "store_unavailable"),)),
    ),
)
def test_failed_counter_seed_does_not_suppress_remaining_children_or_real_events(
    name: str, labels: tuple[tuple[str, str], ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_labels = Counter.labels

    def failing_labels(counter: Counter, *values: str, **named: str) -> Counter:
        if counter._name == name.removesuffix("_total") and values == tuple(
            value for _, value in labels
        ):
            raise RuntimeError("private exporter failure")
        return original_labels(counter, *values, **named)

    with monkeypatch.context() as patch:
        patch.setattr(Counter, "labels", failing_labels)
        metrics = RuntimeMetrics()

    expected: dict[SampleKey, float] = {
        **_HISTORY_FAILURE_BASELINES,
        **{
            ("ci_coordinator_config_activations_total", (("result", state),)): 0
            for state in (*_ACTIVATION_STATES, "other")
        },
    }
    del expected[(name, labels)]
    assert _samples_for(metrics, *(key[0] for key in expected)) == expected
    assert metrics.instrumentation_failure_count == 1
    assert (
        prometheus_samples(metrics)[
            ("ci_coordinator_instrumentation_failures_total", (("surface", "counter"),))
        ]
        == 1
    )
    metrics.config_activation("attestation_invalid")
    metrics.ci_history_item("backfill", "store_unavailable")
    metrics.ci_history_provider_result("backfill", "access_unavailable")
    metrics.ci_history_delivery("store_unavailable")
    assert prometheus_samples(metrics)[(name, labels)] == 1
    assert b"private exporter failure" not in metrics.snapshot().content


@pytest.mark.parametrize(
    "templates",
    (
        frozenset(),
        frozenset({"/unrelated"}),
        frozenset({"/webhooks/github"}),
        frozenset({"/api/v1/dynamic-ci/plan"}),
        frozenset({"/webhooks/github", "/api/v1/dynamic-ci/plan"}),
    ),
)
def test_bound_http_alert_series_export_zero_and_preserve_events_on_rebind(
    templates: frozenset[str],
) -> None:
    metrics = RuntimeMetrics()
    assert _samples_for(metrics, *_HTTP_SAMPLE_NAMES) == {}
    metrics.bind_http_route_templates(templates)
    webhook_labels = (("method", "POST"), ("route", "/webhooks/github"), ("status_class", "5xx"))
    plan_labels = (
        ("method", "POST"),
        ("route", "/api/v1/dynamic-ci/plan"),
        ("status_class", "2xx"),
    )
    expected: dict[SampleKey, float] = {}
    if "/webhooks/github" in templates:
        expected[(_HTTP_SAMPLE_NAMES[0], webhook_labels)] = 0
    if "/api/v1/dynamic-ci/plan" in templates:
        for le in (
            "0.005",
            "0.01",
            "0.025",
            "0.05",
            "0.1",
            "0.25",
            "0.5",
            "1.0",
            "2.5",
            "5.0",
            "10.0",
            "15.0",
            "30.0",
            "60.0",
            "+Inf",
        ):
            expected[(_HTTP_SAMPLE_NAMES[1], (("le", le), *plan_labels))] = 0
        expected[(_HTTP_SAMPLE_NAMES[2], plan_labels)] = 0
        expected[(_HTTP_SAMPLE_NAMES[3], plan_labels)] = 0
    assert _samples_for(metrics, *_HTTP_SAMPLE_NAMES) == expected

    if "/webhooks/github" in templates:
        metrics.http_request(
            method="POST", route="/webhooks/github", status_code=503, duration_seconds=0.5
        )
        expected[(_HTTP_SAMPLE_NAMES[0], webhook_labels)] = 1
    if "/api/v1/dynamic-ci/plan" in templates:
        metrics.http_request(
            method="POST", route="/api/v1/dynamic-ci/plan", status_code=200, duration_seconds=12
        )
        for key in expected:
            if key[0] == _HTTP_SAMPLE_NAMES[1]:
                expected[key] = int(float(dict(key[1])["le"]) >= 12)
        expected[(_HTTP_SAMPLE_NAMES[2], plan_labels)] = 1
        expected[(_HTTP_SAMPLE_NAMES[3], plan_labels)] = 12
    after = _samples_for(metrics, *_HTTP_SAMPLE_NAMES)
    for key, value in expected.items():
        assert after[key] == value
    metrics.bind_http_route_templates(templates)
    assert _samples_for(metrics, *_HTTP_SAMPLE_NAMES) == after


def test_rejected_http_catalog_cannot_seed_alert_children() -> None:
    metrics = RuntimeMetrics()
    metrics.bind_http_route_templates(frozenset())
    with pytest.raises(ValueError):
        metrics.bind_http_route_templates(
            frozenset({"/webhooks/github", "/api/v1/dynamic-ci/plan"})
        )
    assert _samples_for(metrics, *_HTTP_SAMPLE_NAMES) == {}


@pytest.mark.parametrize("failed_parent", ("_http_requests", "_http_duration"))
@pytest.mark.parametrize("failed_recorder", (False, True))
def test_http_seed_failure_is_per_child_and_non_recursive(
    failed_parent: str, failed_recorder: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    metrics = RuntimeMetrics()

    def fail(*_: str) -> None:
        raise RuntimeError("private exporter failure")

    with monkeypatch.context() as patch:
        patch.setattr(getattr(metrics, failed_parent), "labels", fail)
        if failed_recorder:
            patch.setattr(metrics._instrumentation_failures, "labels", fail)
        metrics.bind_http_route_templates(
            frozenset({"/webhooks/github", "/api/v1/dynamic-ci/plan"})
        )
    samples = prometheus_samples(metrics)
    webhook_key = (
        _HTTP_SAMPLE_NAMES[0],
        (("method", "POST"), ("route", "/webhooks/github"), ("status_class", "5xx")),
    )
    plan_key = (
        _HTTP_SAMPLE_NAMES[2],
        (("method", "POST"), ("route", "/api/v1/dynamic-ci/plan"), ("status_class", "2xx")),
    )
    failed_key, healthy_key = (
        (webhook_key, plan_key) if failed_parent == "_http_requests" else (plan_key, webhook_key)
    )
    assert failed_key not in samples
    assert samples[healthy_key] == 0
    assert metrics.instrumentation_failure_count == 1
    assert samples[
        ("ci_coordinator_instrumentation_failures_total", (("surface", "http"),))
    ] == int(not failed_recorder)
    metrics.http_request(
        method="POST", route="/webhooks/github", status_code=503, duration_seconds=0.5
    )
    metrics.http_request(
        method="POST", route="/api/v1/dynamic-ci/plan", status_code=200, duration_seconds=12
    )
    samples = prometheus_samples(metrics)
    assert samples[webhook_key] == samples[plan_key] == 1


def test_http_metrics_use_route_templates_and_bound_unknown_dimensions() -> None:
    metrics = RuntimeMetrics()
    metrics.bind_http_route_templates(
        frozenset({"/api/v1/workbench/repositories/{installation_id}/{repository_id}"})
    )

    metrics.http_request(
        method="GET",
        route="/api/v1/workbench/repositories/{installation_id}/{repository_id}",
        status_code=200,
        duration_seconds=0.25,
    )
    metrics.http_request(
        method="TRACE",
        route="caller-controlled",
        status_code=999,
        duration_seconds=-1,
    )

    samples = prometheus_samples(metrics)
    assert (
        samples[
            (
                "ci_coordinator_http_requests_total",
                (
                    ("method", "GET"),
                    (
                        "route",
                        "/api/v1/workbench/repositories/{installation_id}/{repository_id}",
                    ),
                    ("status_class", "2xx"),
                ),
            )
        ]
        == 1
    )
    assert (
        samples[
            (
                "ci_coordinator_http_requests_total",
                (("method", "OTHER"), ("route", "unmatched"), ("status_class", "unknown")),
            )
        ]
        == 1
    )


@pytest.mark.parametrize("templates", [frozenset(), frozenset({"/known/{id}"})])
def test_http_template_binding_is_idempotent_and_cannot_be_changed(
    templates: frozenset[str],
) -> None:
    metrics = RuntimeMetrics()
    metrics.bind_http_route_templates(templates)
    metrics.bind_http_route_templates(templates)
    with pytest.raises(ValueError):
        metrics.bind_http_route_templates(frozenset({"/other"}))
    for route in ("/known/{id}", "/other"):
        metrics.http_request(method="GET", route=route, status_code=200, duration_seconds=0)
    assert _http_routes(metrics) == templates | {"unmatched"}


@pytest.mark.parametrize("templates", [None, {"/known"}, ["/known"], frozenset({1})])
def test_http_template_binding_rejects_malformed_catalogs_without_binding(
    templates: object,
) -> None:
    metrics = RuntimeMetrics()
    with pytest.raises(TypeError):
        metrics.bind_http_route_templates(cast(frozenset[str], templates))
    metrics.http_request(method="GET", route="/known", status_code=200, duration_seconds=0)
    assert _http_routes(metrics) == {"unmatched"}
    metrics.bind_http_route_templates(frozenset({"/known"}))
    metrics.http_request(method="GET", route="/known", status_code=200, duration_seconds=0)
    assert _http_routes(metrics) == {"/known", "unmatched"}


def test_competing_http_catalog_bindings_admit_exactly_one_catalog() -> None:
    metrics = RuntimeMetrics()
    barrier = Barrier(2, timeout=5)

    def bind(route: str) -> str | None:
        barrier.wait()
        try:
            metrics.bind_http_route_templates(frozenset({route}))
        except ValueError:
            return None
        return route

    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted = set(pool.map(bind, ("/first", "/second")))
    assert None in admitted
    assert len(admitted) == 2
    for route in ("/first", "/second"):
        metrics.http_request(method="GET", route=route, status_code=200, duration_seconds=0)
    assert _http_routes(metrics) == (admitted - {None}) | {"unmatched"}


def _http_routes(metrics: RuntimeMetrics) -> set[str]:
    return {
        dict(labels)["route"]
        for name, labels in prometheus_samples(metrics)
        if name == "ci_coordinator_http_requests_total"
    }


def test_background_metrics_are_label_free_and_count_one_terminal_transition() -> None:
    metrics = RuntimeMetrics()

    metrics.background_health(BackgroundHealth(BackgroundHealthState.HEALTHY))
    metrics.background_health(BackgroundHealth(BackgroundHealthState.TERMINAL_FAILURE))
    metrics.background_health(BackgroundHealth(BackgroundHealthState.UNOBSERVABLE))
    metrics.background_health(BackgroundHealth(BackgroundHealthState.TERMINAL_FAILURE))

    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_reconciliation_background_healthy", ())] == 0
    assert samples[("ci_coordinator_reconciliation_background_health_observable", ())] == 1
    assert samples[("ci_coordinator_reconciliation_background_terminal_failure", ())] == 1
    assert samples[("ci_coordinator_reconciliation_background_terminal_failures_total", ())] == 1


def test_ci_economics_metrics_bound_outcomes_and_reject_invalid_rounds() -> None:
    metrics = RuntimeMetrics()
    admitted_outcomes = cast(
        tuple[CiEconomicsCollectionItemMetricOutcome, ...],
        CI_ECONOMICS_COLLECTION_METRIC_OUTCOMES[:-1],
    )

    metrics.ci_economics_collection_round(
        registered=2,
        outcomes=cast(
            tuple[CiEconomicsCollectionItemMetricOutcome, ...],
            (*admitted_outcomes, "unrecognized"),
        ),
    )
    metrics.ci_economics_collection_round(registered=-1, outcomes=())
    metrics.ci_economics_collection_round(
        registered=0,
        outcomes=cast(tuple[CiEconomicsCollectionItemMetricOutcome, ...], []),
    )

    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_ci_economics_registered_subjects_total", ())] == 2
    for outcome in admitted_outcomes:
        assert (
            samples[
                (
                    "ci_coordinator_ci_economics_collection_item_outcomes_total",
                    (("outcome", outcome),),
                )
            ]
            == 1
        )
    assert (
        samples[
            (
                "ci_coordinator_ci_economics_collection_item_outcomes_total",
                (("outcome", "other"),),
            )
        ]
        == 1
    )
    assert metrics.instrumentation_failure_count == 2


def test_ci_economics_metric_failure_is_contained() -> None:
    metrics = RuntimeMetrics()

    class _FailingMetric:
        def labels(self, *_: object) -> None:
            raise RuntimeError("instrumentation backend failed")

    metrics._ci_economics_collection_item_outcomes = cast(Any, _FailingMetric())
    metrics.ci_economics_collection_round(registered=1, outcomes=("deferred",))

    assert metrics.instrumentation_failure_count == 1


def test_metrics_failure_is_bounded_observable_and_non_recursive() -> None:
    metrics = RuntimeMetrics()

    class _FailingMetric:
        def labels(self, *_: object) -> None:
            raise RuntimeError("instrumentation backend failed")

    metrics._http_requests = cast(Any, _FailingMetric())
    metrics.http_request(method="GET", route="/healthz", status_code=200, duration_seconds=0.1)

    samples = prometheus_samples(metrics)
    assert metrics.instrumentation_failure_count == 1
    assert samples[("ci_coordinator_instrumentation_failures_total", (("surface", "http"),))] == 1


def test_structured_logs_are_canonical_correlated_and_recursively_redacted() -> None:
    output = StringIO()
    logger = logging.Logger("observability-test")
    logger.addHandler(logging.StreamHandler(output))
    structured = StructuredEventLogger(logger)

    structured.emit(
        {
            "event": "http_request_completed",
            "authorization": "Bearer raw-token",
            "provider": {"privateKey": "raw-private-key", "reason": "timeout"},
        },
        correlation_id="a" * 32,
    )

    record = json.loads(output.getvalue())
    assert record["event"] == "http_request_completed"
    assert record["correlationId"] == "a" * 32
    assert record["authorization"] == "[REDACTED]"
    assert record["provider"] == {"privateKey": "[REDACTED]", "reason": "timeout"}
    assert record["service"] == "ci-coordinator"
    assert "raw-token" not in output.getvalue()
    assert "raw-private-key" not in output.getvalue()


def test_structured_log_redaction_preserves_supported_sequences() -> None:
    event = redacted_log_event({"requestId": "request-1", "attempts": [1, 2]})

    assert event == {"requestId": "request-1", "attempts": (1, 2)}


@pytest.mark.parametrize(
    "key",
    [
        "apikey",
        "apiKey",
        "api_key",
        "api-key",
        "clientsecret",
        "clientSecret",
        "client_secret",
        "client-secret",
        "privatekey",
        "privateKey",
        "private_key",
        "private-key",
        "refreshtoken",
        "sessioncookie",
        "signingkey",
    ],
)
def test_structured_log_redaction_admits_closed_compound_credential_aliases(
    key: str,
) -> None:
    assert redacted_log_event({key: "raw-credential"}) == {key: "[REDACTED]"}


@pytest.mark.parametrize("key", ["clientid", "hockey", "monkey", "publickey"])
def test_structured_log_redaction_preserves_noncredential_compound_names(key: str) -> None:
    assert redacted_log_event({key: "benign"}) == {key: "benign"}


def test_structured_logs_bound_hostile_values_without_false_positive_redaction() -> None:
    output = StringIO()
    logger = logging.Logger("hostile-observability-test")
    logger.addHandler(logging.StreamHandler(output))
    structured = StructuredEventLogger(logger)
    cycle: dict[str, object] = {}
    cycle["self"] = cycle

    structured.emit(
        {
            "event": "hostile_event",
            "level": "CALLER_CONTROLLED",
            "service": "caller-controlled",
            "sessionCookie": "raw-cookie",
            "code": "raw-oauth-code",
            "passPhrase": "raw-passphrase",
            "xOAuthCode": "raw-prefixed-oauth-code",
            "monkey": "benign",
            "statusCode": 503,
            "nonFinite": float("nan"),
            "largeInteger": 2**63,
            "cycle": cycle,
            "values": list(range(100)),
            "largeText": "x" * 10_000,
        },
        correlation_id="b" * 32,
    )

    raw = output.getvalue()
    record = json.loads(raw)
    assert record["correlationId"] == "b" * 32
    assert record["level"] == "INFO"
    assert record["service"] == "ci-coordinator"
    assert record["sessionCookie"] == "[REDACTED]"
    assert record["code"] == "[REDACTED]"
    assert record["passPhrase"] == "[REDACTED]"
    assert record["xOAuthCode"] == "[REDACTED]"
    assert record["monkey"] == "benign"
    assert record["statusCode"] == 503
    assert record["nonFinite"] == "[NON_FINITE_NUMBER]"
    assert record["largeInteger"] == "[INTEGER_OUT_OF_RANGE]"
    assert record["cycle"] == {"self": "[CYCLE]"}
    assert len(record["values"]) == 64
    assert record["largeText"].endswith("[TRUNCATED]")
    assert len(raw.encode("utf-8")) <= 65_536
    assert "raw-cookie" not in raw
    assert "raw-oauth-code" not in raw
    assert "raw-passphrase" not in raw
    assert "raw-prefixed-oauth-code" not in raw


@pytest.mark.parametrize(
    ("event_value", "expected_event"),
    [
        ("record_budget_event", "record_budget_event"),
        ({f"nested{index:02d}": "y" * 4_096 for index in range(64)}, "[RECORD_LIMIT]"),
    ],
)
def test_structured_logs_project_record_budget_without_fallback(
    event_value: object,
    expected_event: str,
) -> None:
    output = StringIO()
    logger = logging.Logger("record-budget-observability-test")
    logger.addHandler(logging.StreamHandler(output))
    structured = StructuredEventLogger(logger)
    event: dict[str, object] = {f"field{index:02d}": "x" * 4_096 for index in range(63)}
    event["event"] = event_value

    structured.emit(event, correlation_id="e" * 32)

    raw = output.getvalue()
    record = json.loads(raw)
    assert len(raw.encode("utf-8")) <= 65_536
    assert record["event"] == expected_event
    assert "record_limit" in record["logSanitization"]
    assert "[RECORD_LIMIT]" in record.values()
    assert structured.failure_count == 0


def test_structured_logger_emits_fixed_fallback_for_instrumentation_failure() -> None:
    output = StringIO()
    logger = logging.Logger("failing-observability-test")
    logger.addHandler(logging.StreamHandler(output))
    structured = StructuredEventLogger(logger)

    structured.emit(_ExplodingMapping(), correlation_id="c" * 32)

    assert structured.failure_count == 1
    assert json.loads(output.getvalue()) == {
        "correlationId": "c" * 32,
        "event": "structured_log_failure",
        "level": "ERROR",
        "observedAt": json.loads(output.getvalue())["observedAt"],
        "reason": "instrumentation_failure",
        "service": "ci-coordinator",
        "sourceCommit": None,
        "releaseIdentity": None,
    }
    assert "secret mapping failure" not in output.getvalue()


def test_runtime_diagnostics_exclude_exception_messages_and_arguments() -> None:
    output = StringIO()
    logger = logging.Logger("runtime-diagnostic-test")
    logger.addHandler(logging.StreamHandler(output))
    diagnostics = RuntimeDiagnosticObserver(StructuredEventLogger(logger))

    diagnostics.unexpected_failure(
        "candidate_context",
        RuntimeError("provider response contains a secret"),
        correlation_id="d" * 32,
    )

    record = json.loads(output.getvalue())
    assert record["event"] == "unexpected_failure"
    assert record["stage"] == "candidate_context"
    assert record["exceptionType"] == "RuntimeError"
    assert record["level"] == "ERROR"
    assert record["correlationId"] == "d" * 32
    assert "provider response contains a secret" not in output.getvalue()


_DIAGNOSTIC_CANARY = "canary-value-91d2"
_PROBE_MODULE = "ci_coordinator.diagnostic_probe"
_PACKAGE_DIRECTORY = os.path.dirname(ci_coordinator.__file__)
_DIAGNOSTIC_RECORD_FIELDS = frozenset(
    {
        "correlationId",
        "event",
        "exceptionType",
        "failureFingerprint",
        "level",
        "observedAt",
        "reason",
        "releaseIdentity",
        "service",
        "sourceCommit",
        "sourceLine",
        "sourceModule",
        "sourceQualname",
        "stage",
    }
)


def _probe_raise_at(site: str, secret_argument: str) -> None:
    secret_local = f"{secret_argument}-local"
    if site == "first":
        raise LookupError(f"first {secret_local}")
    raise LookupError(f"second {secret_local}")


def _probe_call_external(callback: Callable[[str], None], secret_argument: str) -> None:
    callback(secret_argument)


def _probe_recurse(depth: int) -> None:
    if depth:
        _probe_recurse(depth - 1)
        return
    raise RuntimeError("recursion floor")


def _probe_raise_given(error: BaseException) -> None:
    raise error


def _external_callback(secret_argument: str) -> None:
    secret_local = f"{secret_argument}-callback"
    error = RuntimeError(f"external {secret_local}")
    error.add_note(secret_local)
    raise error


def _as_module_function[**P](
    function: Callable[P, None],
    relative_path: str = "diagnostic_probe.py",
    *,
    root: str = _PACKAGE_DIRECTORY,
    qualname: str | None = None,
) -> Callable[P, None]:
    # Relocate the code into a package source path: the code filename is the module identity.
    code = cast("types.FunctionType", function).__code__.replace(
        co_filename=os.path.join(root, *relative_path.split("/"))
    )
    if qualname is not None:
        code = code.replace(co_qualname=qualname)
    namespace: dict[str, Any] = {}
    rebound = types.FunctionType(code, namespace, function.__name__)
    namespace[function.__name__] = rebound
    return cast("Callable[P, None]", rebound)


def _source_line(function: Callable[..., object], fragment: str) -> int:
    lines, first_line = inspect.getsourcelines(function)
    matches = [index for index, line in enumerate(lines) if fragment in line]
    assert len(matches) == 1
    return first_line + matches[0]


def _diagnostic_record(
    error: BaseException,
    stage: Literal["candidate_context", "http_request"] = "candidate_context",
) -> tuple[dict[str, Any], str]:
    output = StringIO()
    logger = logging.Logger("diagnostic-coordinates")
    logger.addHandler(logging.StreamHandler(output))
    structured = StructuredEventLogger(logger, source_commit="a" * 40, release_identity="b" * 64)
    RuntimeDiagnosticObserver(structured).unexpected_failure(stage, error, correlation_id="e" * 32)
    rendered = output.getvalue()
    return json.loads(rendered), rendered


def test_runtime_diagnostics_locate_the_innermost_first_party_frame_without_failure_data() -> None:
    with pytest.raises(RuntimeError) as raised:
        _as_module_function(_probe_call_external)(_external_callback, _DIAGNOSTIC_CANARY)

    record, rendered = _diagnostic_record(raised.value)

    assert set(record) == _DIAGNOSTIC_RECORD_FIELDS
    assert (record["sourceModule"], record["sourceQualname"], record["sourceLine"]) == (
        _PROBE_MODULE,
        "_probe_call_external",
        _source_line(_probe_call_external, "callback(secret_argument)"),
    )
    assert record["exceptionType"] == "RuntimeError"
    assert re.fullmatch(r"[0-9a-f]{16}", record["failureFingerprint"])
    assert (record["sourceCommit"], record["releaseIdentity"]) == ("a" * 40, "b" * 64)
    assert record["correlationId"] == "e" * 32
    assert _DIAGNOSTIC_CANARY not in rendered
    assert "test_observability" not in rendered and ".py" not in rendered


def test_same_type_failures_at_distinct_sites_have_distinct_fingerprints() -> None:
    def probe_record(site: str) -> dict[str, Any]:
        with pytest.raises(LookupError) as raised:
            _as_module_function(_probe_raise_at)(site, _DIAGNOSTIC_CANARY)
        record, rendered = _diagnostic_record(raised.value)
        assert _DIAGNOSTIC_CANARY not in rendered
        return record

    first, repeated, second = probe_record("first"), probe_record("first"), probe_record("second")
    assert first["exceptionType"] == second["exceptionType"] == "LookupError"
    assert first["sourceLine"] == _source_line(_probe_raise_at, 'raise LookupError(f"first')
    assert second["sourceLine"] == _source_line(_probe_raise_at, 'raise LookupError(f"second')
    assert first["failureFingerprint"] != second["failureFingerprint"]
    assert {key: repeated[key] for key in ("failureFingerprint", "sourceLine")} == {
        key: first[key] for key in ("failureFingerprint", "sourceLine")
    }

    with pytest.raises(TypeError) as observer_error:
        RuntimeDiagnosticObserver(cast(Any, object()))
    with pytest.raises(TypeError) as logger_error:
        StructuredEventLogger(cast(Any, object()))
    observer_record, _ = _diagnostic_record(observer_error.value)
    logger_record, _ = _diagnostic_record(logger_error.value)
    assert (observer_record["sourceModule"], observer_record["sourceQualname"]) == (
        "ci_coordinator.observability.diagnostics",
        "RuntimeDiagnosticObserver.__init__",
    )
    assert (logger_record["sourceModule"], logger_record["sourceQualname"]) == (
        "ci_coordinator.observability.logging",
        "StructuredEventLogger.__init__",
    )
    assert observer_record["failureFingerprint"] != logger_record["failureFingerprint"]

    http_record, _ = _diagnostic_record(observer_error.value, "http_request")
    assert http_record["sourceLine"] == observer_record["sourceLine"]
    assert http_record["failureFingerprint"] != observer_record["failureFingerprint"]


def test_same_named_exception_classes_have_distinct_fingerprints() -> None:
    shadow_class = cast(
        "type[Exception]", type("LookupError", (Exception,), {"__module__": "shadow_errors"})
    )
    records: list[dict[str, Any]] = []
    for error_class in (LookupError, shadow_class):
        with pytest.raises(error_class) as raised:
            _as_module_function(_probe_raise_given)(error_class(_DIAGNOSTIC_CANARY))
        records.append(_diagnostic_record(raised.value)[0])
    builtin_record, shadow_record = records
    assert builtin_record["exceptionType"] == shadow_record["exceptionType"] == "LookupError"
    assert builtin_record["sourceLine"] == shadow_record["sourceLine"]
    assert builtin_record["failureFingerprint"] != shadow_record["failureFingerprint"]

    opaque_module_class = cast(
        "type[Exception]", type("OpaqueModuleError", (Exception,), {"__module__": object()})
    )
    opaque_record, _ = _diagnostic_record(opaque_module_class(_DIAGNOSTIC_CANARY))
    assert opaque_record["exceptionType"] == "OpaqueModuleError"
    assert re.fullmatch(r"[0-9a-f]{16}", opaque_record["failureFingerprint"])


def test_failures_without_first_party_frames_have_no_source_coordinates() -> None:
    with pytest.raises(json.JSONDecodeError) as third_party:
        json.loads("{" + _DIAGNOSTIC_CANARY)
    outside_package: list[BaseException] = []
    for relative_path, root in (
        ("diagnostic_probe.py", _PACKAGE_DIRECTORY + "_shadow"),
        ("../ci_coordinator_shadow/diagnostic_probe.py", _PACKAGE_DIRECTORY),
        ("diagnostic_probe.py", os.path.dirname(_PACKAGE_DIRECTORY)),
        ("diagnostic-probe.py", _PACKAGE_DIRECTORY),
        ("diagnostic_probe", _PACKAGE_DIRECTORY),
    ):
        with pytest.raises(LookupError) as raised:
            _as_module_function(_probe_raise_at, relative_path, root=root)(
                "first", _DIAGNOSTIC_CANARY
            )
        outside_package.append(raised.value)
    never_raised = RuntimeError(_DIAGNOSTIC_CANARY)

    for error in (third_party.value, *outside_package, never_raised):
        record, rendered = _diagnostic_record(error)
        assert set(record) == _DIAGNOSTIC_RECORD_FIELDS
        assert (record["sourceModule"], record["sourceQualname"], record["sourceLine"]) == (
            None,
            None,
            None,
        )
        assert re.fullmatch(r"[0-9a-f]{16}", record["failureFingerprint"])
        assert _DIAGNOSTIC_CANARY not in rendered


def test_diagnostic_coordinates_and_fingerprint_frames_are_bounded() -> None:
    def recursion_record(depth: int) -> dict[str, Any]:
        with pytest.raises(RuntimeError) as raised:
            _as_module_function(_probe_recurse)(depth)
        return _diagnostic_record(raised.value)[0]

    shallow, deep, deeper = recursion_record(3), recursion_record(20), recursion_record(30)
    assert deep["sourceLine"] == _source_line(_probe_recurse, 'raise RuntimeError("recursion')
    assert deep["sourceQualname"] == "_probe_recurse"
    assert deep["failureFingerprint"] == deeper["failureFingerprint"]
    assert shallow["failureFingerprint"] != deep["failureFingerprint"]

    with pytest.raises(LookupError) as package_error:
        _as_module_function(_probe_raise_at, "diagnostic_probe/__init__.py")(
            "first", _DIAGNOSTIC_CANARY
        )
    assert _diagnostic_record(package_error.value)[0]["sourceModule"] == _PROBE_MODULE

    with pytest.raises(LookupError) as long_error:
        _as_module_function(_probe_raise_at, "m" * 300 + ".py", qualname="q" * 300)(
            "first", _DIAGNOSTIC_CANARY
        )
    record, _ = _diagnostic_record(long_error.value)
    assert record["sourceModule"] == ("ci_coordinator." + "m" * 300)[:256]
    assert record["sourceQualname"] == "q" * 256

    prefix_sharing: list[dict[str, Any]] = []
    for suffix in ("a", "b"):
        with pytest.raises(LookupError) as prefix_error:
            _as_module_function(_probe_raise_at, "m" * 300 + suffix + ".py")(
                "first", _DIAGNOSTIC_CANARY
            )
        prefix_sharing.append(_diagnostic_record(prefix_error.value)[0])
    assert prefix_sharing[0]["sourceModule"] == prefix_sharing[1]["sourceModule"]
    assert prefix_sharing[0]["failureFingerprint"] != prefix_sharing[1]["failureFingerprint"]


def _raise_diagnostic_canary(_: object) -> object:
    raise RuntimeError(_DIAGNOSTIC_CANARY)


def _forged_first_party_traceback(_: object) -> object:
    code = types.SimpleNamespace(
        co_filename=os.path.join(_PACKAGE_DIRECTORY, "diagnostic_probe.py"),
        co_qualname=_DIAGNOSTIC_CANARY,
    )
    return types.SimpleNamespace(
        tb_frame=types.SimpleNamespace(f_code=code), tb_lineno=1, tb_next=None
    )


def _hostile_class(
    name: str, metaclass_namespace: dict[str, object], namespace: dict[str, object]
) -> type[Exception]:
    metaclass = type(f"{name}Meta", (type,), metaclass_namespace)
    return cast("type[Exception]", metaclass(name, (Exception,), namespace))


def _hostile_diagnostic_errors() -> tuple[tuple[BaseException, str | None], ...]:
    raising_traceback = _hostile_class(
        "RaisingTracebackError", {}, {"__traceback__": property(_raise_diagnostic_canary)}
    )
    forged_traceback = _hostile_class(
        "ForgedTracebackError", {}, {"__traceback__": property(_forged_first_party_traceback)}
    )
    raising_module = _hostile_class(
        "RaisingModuleError", {"__module__": property(_raise_diagnostic_canary)}, {}
    )
    with pytest.raises(raising_module) as raised_module:
        _as_module_function(_probe_raise_given)(raising_module(_DIAGNOSTIC_CANARY))
    raising_name = _hostile_class(
        "RaisingNameError", {"__name__": property(_raise_diagnostic_canary)}, {}
    )
    return (
        (raising_traceback(_DIAGNOSTIC_CANARY), "RaisingTracebackError"),
        (forged_traceback(_DIAGNOSTIC_CANARY), "ForgedTracebackError"),
        (raised_module.value, "RaisingModuleError"),
        (raising_name(_DIAGNOSTIC_CANARY), None),
    )


def test_hostile_exception_projection_degrades_to_one_counted_class_only_record() -> None:
    for error, exception_type in _hostile_diagnostic_errors():
        output = StringIO()
        logger = logging.Logger("hostile-diagnostic-projection")
        logger.addHandler(logging.StreamHandler(output))
        structured = StructuredEventLogger(logger)

        RuntimeDiagnosticObserver(structured).unexpected_failure(
            "candidate_context", error, correlation_id="e" * 32
        )

        rendered = output.getvalue()
        assert rendered.count("\n") == 1
        record = json.loads(rendered)
        assert set(record) == _DIAGNOSTIC_RECORD_FIELDS
        assert record["exceptionType"] == exception_type
        assert (record["event"], record["stage"], record["correlationId"]) == (
            "unexpected_failure",
            "candidate_context",
            "e" * 32,
        )
        assert [
            record[key] for key in ("failureFingerprint", "sourceModule", "sourceQualname")
        ] == [
            None,
            None,
            None,
        ]
        assert record["sourceLine"] is None
        assert structured.failure_count == 1
        assert _DIAGNOSTIC_CANARY not in rendered


def test_cancellation_from_diagnostic_projection_still_propagates() -> None:
    def cancel(_: object) -> object:
        raise asyncio.CancelledError

    cancelling = _hostile_class("CancellingTracebackError", {}, {"__traceback__": property(cancel)})
    output = StringIO()
    logger = logging.Logger("cancelled-diagnostic-projection")
    logger.addHandler(logging.StreamHandler(output))
    structured = StructuredEventLogger(logger)

    with pytest.raises(asyncio.CancelledError):
        RuntimeDiagnosticObserver(structured).unexpected_failure(
            "candidate_context", cancelling(_DIAGNOSTIC_CANARY)
        )

    assert output.getvalue() == ""
    assert structured.failure_count == 0


def test_unexpected_http_failure_response_survives_a_hostile_diagnostic_projection() -> None:
    hostile_error = _hostile_diagnostic_errors()[0][0]
    output = StringIO()
    logger = logging.Logger("hostile-http-diagnostic")
    logger.addHandler(logging.StreamHandler(output))
    structured = StructuredEventLogger(logger)

    async def scenario() -> list[Message]:
        sent: list[Message] = []
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/diagnostic-probe",
            "headers": [],
            "state": {"correlation_id": "f" * 32},
        }

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        async def downstream(_scope: Scope, _receive: Receive, _send: Send) -> None:
            raise hostile_error

        await UnexpectedErrorMiddleware(
            downstream, diagnostics=RuntimeDiagnosticObserver(structured)
        )(scope, receive, send)
        return sent

    start, body = asyncio.run(scenario())

    assert start["type"] == "http.response.start" and start["status"] == 500
    headers = dict(start["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"x-correlation-id"] == b"f" * 32
    assert body["body"] == b'{"code":"internal_error"}'
    record = json.loads(output.getvalue())
    assert (record["stage"], record["correlationId"], record["failureFingerprint"]) == (
        "http_request",
        "f" * 32,
        None,
    )
    assert structured.failure_count == 1
    assert _DIAGNOSTIC_CANARY not in output.getvalue() + repr((start, body))


@pytest.mark.parametrize("severity,level", [("INFO", 20), ("WARNING", 30), ("ERROR", 40)])
def test_native_and_json_severity_agree_without_event_field_authority(
    severity: Literal["INFO", "WARNING", "ERROR"], level: int
) -> None:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.Logger("severity-witness", level=logging.INFO)
    logger.addHandler(Capture())
    structured = StructuredEventLogger(logger)
    structured.emit({"event": "sample", "level": "forged"}, severity=severity)
    structured.emit({"event": "ordinary"})
    RuntimeDiagnosticObserver(structured).unexpected_failure(
        "reconciliation_loop", KeyError("private")
    )
    assert [record.levelno for record in records] == [level, 20, 40]
    assert [json.loads(record.getMessage())["level"] for record in records] == [
        severity,
        "INFO",
        "ERROR",
    ]
    assert json.loads(records[2].getMessage())["stage"] == "reconciliation_loop"


@pytest.mark.parametrize("severity", ["DEBUG", "private-level", 1, None, []])
def test_invalid_severity_becomes_fixed_instrumentation_failure(severity: object) -> None:
    output = StringIO()
    logger = logging.Logger("invalid-level")
    logger.addHandler(logging.StreamHandler(output))
    structured = StructuredEventLogger(logger)
    structured.emit({"event": "discarded"}, severity=cast(Any, severity))
    assert structured.failure_count == 1
    row = json.loads(output.getvalue())
    assert (row["event"], row["level"]) == ("structured_log_failure", "ERROR")
    assert "private-level" not in output.getvalue()


@pytest.mark.parametrize("fault", ["once", "always", "flush", "formatter"])
def test_owned_handler_failure_is_contained_without_raw_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    class Stream(StringIO):
        writes = 0
        flushes = 0

        def write(self, text: str) -> int:
            self.writes += 1
            if fault == "always" or (fault == "once" and self.writes == 1):
                raise OSError("private sink diagnostic")
            return super().write(text)

        def flush(self) -> None:
            self.flushes += 1
            if fault == "flush":
                raise OSError("private flush diagnostic")

    class BrokenFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            raise ValueError("private formatter diagnostic")

    logger = logging.Logger("isolated-runtime-events")
    original_get_logger = logging.getLogger
    monkeypatch.setattr(
        logging,
        "getLogger",
        lambda name=None: logger if name == "ci_coordinator.events" else original_get_logger(name),
    )
    structured = event_logging.default_structured_event_logger()
    handler = cast(logging.StreamHandler[Any], logger.handlers[0])
    stream = Stream()
    handler.setStream(stream)
    if fault == "formatter":
        handler.setFormatter(BrokenFormatter())
    structured.emit({"event": "ordinary"})
    assert structured.failure_count == 1
    assert stream.writes == (0 if fault == "formatter" else 2)
    assert capsys.readouterr().err == ""
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row["event"] for row in rows] == (
        ["structured_log_failure"]
        if fault == "once"
        else ["ordinary", "structured_log_failure"]
        if fault == "flush"
        else []
    )


def test_fallback_metadata_failure_does_not_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenClock:
        @staticmethod
        def now(_zone: object) -> object:
            raise ValueError("private clock diagnostic")

    monkeypatch.setattr(event_logging, "datetime", BrokenClock)
    structured = StructuredEventLogger(logging.Logger("broken-clock"))
    structured.emit({"event": "ordinary"})
    assert structured.failure_count == 1


@pytest.mark.parametrize("foreign_state", ["handler", "filter", "disabled", "level", "propagation"])
def test_default_logger_refuses_foreign_configuration(
    monkeypatch: pytest.MonkeyPatch, foreign_state: str
) -> None:
    logger = logging.Logger("foreign-events")
    if foreign_state == "handler":
        logger.addHandler(logging.NullHandler())
    elif foreign_state == "filter":
        logger.addFilter(logging.Filter())
    elif foreign_state == "disabled":
        logger.disabled = True
    elif foreign_state == "level":
        logger.setLevel(logging.ERROR)
    else:
        logger.propagate = False
    before = (
        list(logger.handlers),
        list(logger.filters),
        logger.disabled,
        logger.level,
        logger.propagate,
    )
    original_get_logger = logging.getLogger
    monkeypatch.setattr(
        logging,
        "getLogger",
        lambda name=None: logger if name == "ci_coordinator.events" else original_get_logger(name),
    )
    with pytest.raises(RuntimeError, match="dedicated runtime logger"):
        event_logging.default_structured_event_logger()
    assert (
        logger.handlers,
        logger.filters,
        logger.disabled,
        logger.level,
        logger.propagate,
    ) == before


def test_dedicated_test_logger_isolated_from_real_pytest_capture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolated = logging.getLogger("ci_coordinator.events")
    assert logging.Logger.manager.loggerDict.get(isolated.name) is not isolated
    first = event_logging.default_structured_event_logger()
    owned_handlers = tuple(isolated.handlers)
    assert len(owned_handlers) == 1
    registered = logging.Logger("dedicated-logger-capture-probe")
    get_logger = logging.getLogger

    def probe_logger(name: str | None = None) -> logging.Logger:
        return registered if name == isolated.name else get_logger(name)

    monkeypatch.setitem(logging.Logger.manager.loggerDict, registered.name, registered)
    with monkeypatch.context() as patch:
        patch.setattr(logging, "getLogger", probe_logger)
        event_logging.default_structured_event_logger()
    probe_handlers = tuple(registered.handlers)
    assert len(probe_handlers) == 1
    assert registered.propagate is False
    capture = LogCaptureHandler()

    with catching_logs(capture, logging.INFO):
        assert capture in registered.handlers
        assert capture not in isolated.handlers
        second = event_logging.default_structured_event_logger()
        assert tuple(isolated.handlers) == owned_handlers
        first.emit({"event": "first"})
        second.emit({"event": "second"})
        assert capture.records == []
        with monkeypatch.context() as patch:
            patch.setattr(logging, "getLogger", probe_logger)
            with pytest.raises(RuntimeError, match="dedicated runtime logger"):
                event_logging.default_structured_event_logger()
        assert capture in registered.handlers

    assert tuple(registered.handlers) == probe_handlers
    assert tuple(isolated.handlers) == owned_handlers
    assert [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()] == [
        "first",
        "second",
    ]
