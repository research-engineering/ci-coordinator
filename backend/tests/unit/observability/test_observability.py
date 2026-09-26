from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from typing import Any, Literal, cast

import pytest
from prometheus_support import prometheus_samples

from ci_coordinator.observability import (
    BackgroundHealth,
    BackgroundHealthState,
    DependencyReadiness,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    StructuredEventLogger,
    assess_readiness,
    health,
    redacted_log_event,
)
from ci_coordinator.observability import logging as event_logging
from ci_coordinator.observability.runtime_metrics import (
    CI_ECONOMICS_COLLECTION_METRIC_OUTCOMES,
    CiEconomicsCollectionItemMetricOutcome,
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
