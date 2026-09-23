"""Bounded runtime signals exported without becoming decision inputs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from math import isfinite
from threading import Lock
from types import TracebackType
from typing import Final, Literal, Protocol

from prometheus_client import Counter, Gauge, Histogram

from ci_coordinator.observability.background_health import BackgroundHealth
from ci_coordinator.observability.metrics import MetricsRegistry, PrometheusSnapshot

type PlanningStage = Literal[
    "active_epoch",
    "authority",
    "candidate",
    "capacity",
    "override",
    "reconciliation",
    "selected_execution",
]
type PlanningUnavailabilityReason = Literal[
    "active_epoch_exception",
    "authority_not_admitted",
    "candidate_exception",
    "capacity_exception",
    "capacity_plan_request_identity_mismatch",
    "capacity_result_invalid",
    "capacity_workflow_run_mismatch",
    "override_exception",
    "reconciliation_registration_unavailable",
    "selected_execution_projection_invalid",
]
type MaintenanceOperationName = Literal[
    "activity_cleanup",
    "ci_economics_collection",
    "ci_economics_expiry",
    "ci_economics_observation_cleanup",
    "ci_economics_tombstone_purge",
    "ci_observation_discovery",
    "ci_observation_gap_cleanup",
    "ci_history_collection",
    "ci_history_delivery",
    "ci_history_detail_cleanup",
]
type MaintenanceOperationResult = Literal["failed", "succeeded", "timed_out"]
type CiEconomicsCollectionItemMetricOutcome = Literal[
    "aborted",
    "captured",
    "claim_lost",
    "deferred",
    "none_due",
    "terminal_conflict",
]
type CiEconomicsCollectionMetricOutcome = CiEconomicsCollectionItemMetricOutcome | Literal["other"]

_PLANNING_STAGES: Final[tuple[PlanningStage, ...]] = (
    "active_epoch",
    "authority",
    "candidate",
    "capacity",
    "override",
    "reconciliation",
    "selected_execution",
)
_PLANNING_REASONS: Final[tuple[PlanningUnavailabilityReason, ...]] = (
    "active_epoch_exception",
    "authority_not_admitted",
    "candidate_exception",
    "capacity_exception",
    "capacity_plan_request_identity_mismatch",
    "capacity_result_invalid",
    "capacity_workflow_run_mismatch",
    "override_exception",
    "reconciliation_registration_unavailable",
    "selected_execution_projection_invalid",
)

_PLAN_RESULTS: Final = (
    "conflict",
    "dependency_unavailable",
    "forbidden",
    "invalid",
    "issuance_unavailable",
    "issued",
    "unauthenticated",
)
_FALLBACK_REASONS: Final = frozenset(
    {
        "deterministic_plan_mismatch",
        "dynamic_enforcement_disabled",
        "omission_proof_mismatch",
        "operator_override",
        "production_admission_expired",
        "production_admission_mismatch",
        "production_revalidation_failed",
        "override_state_unavailable",
        "planner_rejected",
        "selected_execution_unavailable",
        "verified_plan_fallback",
        "verified_plan_request_mismatch",
        "verified_plan_unavailable",
    }
)
_OIDC_REASONS: Final = frozenset(
    {
        "audience_mismatch",
        "bearer_token_missing",
        "claims_not_json",
        "event_name_mismatch",
        "execution_sha_mismatch",
        "header_not_admitted",
        "issuer_mismatch",
        "job_workflow_sha_mismatch",
        "jwks_fetch_cancelled",
        "jwks_fetch_timeout",
        "jwks_fetch_unavailable",
        "jwks_refresh_throttled",
        "jwks_response_invalid",
        "jwks_response_oversize",
        "jwks_response_redirected",
        "jwks_signing_key_unavailable",
        "ref_mismatch",
        "repository_id_mismatch",
        "repository_mismatch",
        "run_identity_mismatch",
        "signature_invalid",
        "signing_key_invalid",
        "signing_key_unavailable",
        "token_expired",
        "token_malformed",
        "token_not_yet_valid",
        "workflow_not_allowed",
        "workflow_sha_required",
        "workflow_sha_mismatch",
    }
)
_CAPACITY_REASONS: Final = frozenset(
    {
        "capacity_exception",
        "capacity_inputs_unavailable",
        "runner_capacity_stale",
        "runner_capacity_unavailable",
        "runner_capacity_unknown",
        "test_duration_history_missing",
    }
)
_GITHUB_SURFACES: Final = frozenset(
    {
        "actions",
        "app_identity",
        "checks",
        "diff",
        "repositories",
        "runner",
        "workflow_catalog",
    }
)
_HTTP_METHODS: Final = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
_HTTP_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30, 60)
_MAINTENANCE_OPERATIONS: Final[tuple[MaintenanceOperationName, ...]] = (
    "activity_cleanup",
    "ci_economics_collection",
    "ci_economics_expiry",
    "ci_economics_observation_cleanup",
    "ci_economics_tombstone_purge",
    "ci_observation_discovery",
    "ci_observation_gap_cleanup",
    "ci_history_collection",
    "ci_history_delivery",
    "ci_history_detail_cleanup",
)
_HISTORY_DELIVERY_OUTCOMES: Final = frozenset(
    {
        "applied",
        "empty",
        "inactive",
        "busy",
        "capacity_reached",
        "deferred",
        "aborted",
        "store_unavailable",
        "unexpected_error",
        "timed_out",
    }
)
CI_OBSERVATION_ITEM_OUTCOMES: Final = (
    "aborted",
    "none_due",
    "page_recorded",
    "capacity_reached",
    "claim_lost",
    "store_unavailable",
    "unexpected_error",
    "access_unavailable",
    "timed_out",
    "provider_unavailable",
    "provider_binding_mismatch",
    "provider_malformed",
    "provider_incomplete",
    "provider_not_terminal",
    "provider_unstable",
    "other",
)
_HISTORY_LANES: Final = frozenset({"backfill", "recent", "repair", "discovery"})
_HISTORY_ITEM_OUTCOMES: Final = frozenset(
    {
        "applied",
        "claim_lost",
        "capacity_reached",
        "aborted",
        "none_due",
        "recovered",
        "store_unavailable",
        "unexpected_error",
    }
)
_HISTORY_PROVIDER_OUTCOMES: Final = frozenset(
    {
        "page",
        "complete",
        "partial",
        "unavailable",
        "conflict",
        "unavailable_attempt",
        "access_unavailable",
        "timed_out",
        "provider_unavailable",
        "provider_binding_mismatch",
        "provider_malformed",
        "provider_incomplete",
        "provider_not_terminal",
        "provider_unstable",
    }
)
_MAINTENANCE_RESULTS: Final[tuple[MaintenanceOperationResult, ...]] = (
    "failed",
    "succeeded",
    "timed_out",
)
CI_ECONOMICS_COLLECTION_METRIC_OUTCOMES: Final[tuple[CiEconomicsCollectionMetricOutcome, ...]] = (
    "aborted",
    "captured",
    "claim_lost",
    "deferred",
    "none_due",
    "terminal_conflict",
    "other",
)
_MAINTENANCE_DURATION_BUCKETS: Final = (0.01, 0.1, 0.5, 1, 5, 10, 30, 60, 120, 240)
_PREPARATION_OUTCOMES: Final = (
    "cache_expired",
    "cache_hit",
    "cache_invalid",
    "cache_miss",
    "cancelled",
    "coalesced",
    "failed",
    "ineligible",
    "invalid",
    "not_cached",
    "other",
    "prepared",
    "queued",
    "saturated",
    "timed_out",
    "unavailable",
)
_INSTRUMENTATION_SURFACES: Final = (
    "background",
    "counter",
    "dependency_readiness",
    "http",
    "readiness",
)


class _StageTimer(Protocol):
    def __enter__(self) -> object: ...

    def labels(self, *, lane: str, stage: str, outcome: str) -> None: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None: ...


class RuntimeMetrics:
    """Project fixed-cardinality operational facts into one Prometheus registry."""

    def __init__(self, registry: MetricsRegistry | None = None) -> None:
        if registry is not None and type(registry) is not MetricsRegistry:
            raise TypeError("runtime metrics require an exact metric registry")
        self._registry = MetricsRegistry() if registry is None else registry
        self._http_route_templates: frozenset[str] | None = None
        self._http_route_binding_lock = Lock()
        prometheus = self._registry.collector_registry
        self._plan_requests = Counter(
            "ci_coordinator_plan_requests_total",
            "Dynamic plan requests by terminal transport result.",
            ("result",),
            registry=prometheus,
        )
        self._fallbacks = Counter(
            "ci_coordinator_full_ci_fallbacks_total",
            "Issued FullCI fallback plans by bounded reason.",
            ("reason",),
            registry=prometheus,
        )
        self._verifier_rejections = Counter(
            "ci_coordinator_verifier_rejections_total",
            "Verifier rejections by bounded fallback reason.",
            ("reason",),
            registry=prometheus,
        )
        self._signed_envelopes = Counter(
            "ci_coordinator_signed_envelopes_total",
            "Signed plan envelopes by persistence result.",
            ("result",),
            registry=prometheus,
        )
        self._invalid_oidc = Counter(
            "ci_coordinator_invalid_oidc_total",
            "Rejected GitHub Actions identities by bounded reason.",
            ("reason",),
            registry=prometheus,
        )
        self._config_activations = Counter(
            "ci_coordinator_config_activations_total",
            "Configuration activation attempts by result.",
            ("result",),
            registry=prometheus,
        )
        self._config_cas_conflicts = Counter(
            "ci_coordinator_config_activation_cas_conflicts_total",
            "Configuration activation compare-and-set conflicts.",
            registry=prometheus,
        )
        self._shadow_comparisons = Counter(
            "ci_coordinator_shadow_comparisons_total",
            "Shadow comparisons by terminal classification.",
            ("result",),
            registry=prometheus,
        )
        self._unsafe_omissions = Counter(
            "ci_coordinator_shadow_unsafe_omissions_total",
            "Observed unsafe candidate omissions.",
            registry=prometheus,
        )
        self._replay_mismatches = Counter(
            "ci_coordinator_shadow_replay_mismatches_total",
            "Observed shadow replay mismatches.",
            registry=prometheus,
        )
        self._runner_snapshots = Counter(
            "ci_coordinator_runner_snapshots_total",
            "Runner capacity snapshots by freshness state.",
            ("state",),
            registry=prometheus,
        )
        self._capacity_decisions = Counter(
            "ci_coordinator_capacity_decisions_total",
            "Capacity decisions by mode and bounded reason.",
            ("mode", "reason"),
            registry=prometheus,
        )
        self._planning_unavailable = Counter(
            "ci_coordinator_planning_unavailable_total",
            "Fail-safe planning outcomes by bounded stage and reason.",
            ("stage", "reason"),
            registry=prometheus,
        )
        self._github_unavailable = Counter(
            "ci_coordinator_github_api_unavailable_total",
            "GitHub API unavailable outcomes by surface and reason.",
            ("surface", "reason"),
            registry=prometheus,
        )
        self._http_requests = Counter(
            "ci_coordinator_http_requests_total",
            "HTTP requests by method, route template, and status class.",
            ("method", "route", "status_class"),
            registry=prometheus,
        )
        self._http_duration = Histogram(
            "ci_coordinator_http_request_duration_seconds",
            "HTTP request duration by method, route template, and status class.",
            ("method", "route", "status_class"),
            buckets=_HTTP_BUCKETS,
            registry=prometheus,
        )
        self._ready = Gauge(
            "ci_coordinator_ready",
            "Whether every dependency required by the selected runtime path is ready.",
            registry=prometheus,
        )
        self._dependency_ready = Gauge(
            "ci_coordinator_dependency_ready",
            "Readiness of one bounded required dependency.",
            ("dependency",),
            registry=prometheus,
        )
        self._background_healthy = Gauge(
            "ci_coordinator_reconciliation_background_healthy",
            "Whether periodic reconciliation is currently healthy.",
            registry=prometheus,
        )
        self._background_observable = Gauge(
            "ci_coordinator_reconciliation_background_health_observable",
            "Whether periodic reconciliation health can be observed.",
            registry=prometheus,
        )
        self._background_terminal = Gauge(
            "ci_coordinator_reconciliation_background_terminal_failure",
            "Whether periodic reconciliation entered terminal failure.",
            registry=prometheus,
        )
        self._background_terminal_total = Counter(
            "ci_coordinator_reconciliation_background_terminal_failures_total",
            "Periodic reconciliation terminal-failure transitions.",
            registry=prometheus,
        )
        self._reconciliation_rounds = Counter(
            "ci_coordinator_reconciliation_rounds_total",
            "Periodic reconciliation rounds by completion result.",
            ("result",),
            registry=prometheus,
        )
        self._maintenance_operations = Counter(
            "ci_coordinator_maintenance_operations_total",
            "Non-authoritative maintenance operations by bounded result.",
            ("operation", "result"),
            registry=prometheus,
        )
        self._maintenance_duration = Histogram(
            "ci_coordinator_maintenance_operation_duration_seconds",
            "Non-authoritative maintenance duration by operation and result.",
            ("operation", "result"),
            buckets=_MAINTENANCE_DURATION_BUCKETS,
            registry=prometheus,
        )
        self._ci_economics_registered_subjects = Counter(
            "ci_coordinator_ci_economics_registered_subjects_total",
            "Subjects admitted to durable CI economics collection.",
            registry=prometheus,
        )
        self._ci_economics_collection_item_outcomes = Counter(
            "ci_coordinator_ci_economics_collection_item_outcomes_total",
            "CI economics collection item attempts by bounded outcome.",
            ("outcome",),
            registry=prometheus,
        )
        self._instrumentation_failures = Counter(
            "ci_coordinator_instrumentation_failures_total",
            "Non-authoritative instrumentation failures by bounded surface.",
            ("surface",),
            registry=prometheus,
        )
        self._ci_observation_item_outcomes = Counter(
            "ci_coordinator_ci_observation_item_outcomes_total",
            "Repository discovery page attempts by bounded result, not collection success.",
            ("outcome",),
            registry=prometheus,
        )
        self._ci_history_items = Counter(
            "ci_coordinator_ci_history_items_total",
            "Archive work-item transitions, independent of provider read quality.",
            ("lane", "outcome"),
            registry=prometheus,
        )
        self._ci_history_workers_running = Gauge(
            "ci_coordinator_ci_history_worker_running",
            "Process-owned archive lane task is running; not collection success or readiness.",
            ("lane",),
            registry=prometheus,
        )
        self._ci_history_provider_results = Counter(
            "ci_coordinator_ci_history_provider_results_total",
            "Archive provider outcomes before durable completion.",
            ("lane", "outcome"),
            registry=prometheus,
        )
        self._ci_history_stage_duration = Histogram(
            "ci_coordinator_ci_history_stage_duration_seconds",
            "Archive stage elapsed time by control-flow outcome, not durable success or CPU.",
            ("lane", "stage", "outcome"),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 45),
            registry=prometheus,
        )
        self._ci_history_delivery_outcomes = Counter(
            "ci_coordinator_ci_history_delivery_outcomes_total",
            "Archive inbox transfer outcomes, not provider collection or archived run counts.",
            ("outcome",),
            registry=prometheus,
        )
        self._ci_history_delivery_sources = Counter(
            "ci_coordinator_ci_history_delivery_sources_total",
            "Sources considered or durably transferred during archive inbox processing.",
            ("stage",),
            registry=prometheus,
        )
        self._ci_history_pending_age = Histogram(
            "ci_coordinator_ci_history_pending_age_seconds",
            "Oldest eligible source in each selected inbox page, not the full backlog age.",
            buckets=(1, 10, 60, 300, 3600, 86400, 604800, 2592000, 7776000),
            registry=prometheus,
        )
        self._ci_history_expired_pending_sample = Histogram(
            "ci_coordinator_ci_history_expired_pending_sample",
            "Expired pending sources per bounded sample; repeats are not distinct losses.",
            buckets=(0, 1, 10, 50, 100),
            registry=prometheus,
        )
        self._planning_preparation = Counter(
            "ci_coordinator_planning_preparation_total",
            "Best-effort context preparation and cache observations by bounded outcome.",
            ("outcome",),
            registry=prometheus,
        )
        self._instrumentation_failure_count = 0
        self._instrumentation_failure_lock = Lock()
        self._terminal_failure_observed = False
        self._initialize_bounded_series()

    def snapshot(self) -> PrometheusSnapshot:
        return self._registry.snapshot()

    @property
    def instrumentation_failure_count(self) -> int:
        with self._instrumentation_failure_lock:
            return self._instrumentation_failure_count

    def plan_request(self, result: str) -> None:
        self._safe_increment(self._plan_requests, _member(result, set(_PLAN_RESULTS)))

    def planning_preparation(self, outcome: str) -> None:
        self._safe_increment(
            self._planning_preparation, _member(outcome, set(_PREPARATION_OUTCOMES))
        )

    def full_ci_fallback(self, reason: str) -> None:
        self._safe_increment(self._fallbacks, _prefixed_member(reason, _FALLBACK_REASONS))

    def verifier_rejection(self, reason: str | None) -> None:
        self._safe_increment(
            self._verifier_rejections,
            _prefixed_member(reason or "unknown", _FALLBACK_REASONS),
        )

    def signed_envelope(self, *, duplicate: bool) -> None:
        self._safe_increment(self._signed_envelopes, "duplicate" if duplicate else "created")

    def invalid_oidc(self, reason: str) -> None:
        self._safe_increment(self._invalid_oidc, _oidc_reason(reason))

    def config_activation(self, result: str) -> None:
        category = _member(
            result,
            {
                "applied",
                "coverage_reducing",
                "coverage_unproven",
                "duplicate",
                "forbidden",
                "operation_conflict",
                "revision_conflict",
                "target_unavailable",
                "unavailable",
            },
        )
        self._safe_increment(self._config_activations, category)
        if category == "revision_conflict":
            self._safe_increment(self._config_cas_conflicts)

    def shadow_comparison(self, classification: str) -> None:
        category = _member(classification, {"replay_mismatch", "safe", "unknown", "unsafe"})
        self._safe_increment(self._shadow_comparisons, category)
        if category == "unsafe":
            self._safe_increment(self._unsafe_omissions)
        elif category == "replay_mismatch":
            self._safe_increment(self._replay_mismatches)

    def runner_snapshot(self, state: str) -> None:
        self._safe_increment(
            self._runner_snapshots,
            _member(state, {"fresh", "missing", "stale"}),
        )

    def capacity_decision(self, mode: str, reason: str | None = None) -> None:
        category = _member(mode, {"conservative", "optimized"})
        bounded_reason = (
            _prefixed_member(reason or "unknown", _CAPACITY_REASONS)
            if category == "conservative"
            else "none"
        )
        self._safe_increment(self._capacity_decisions, category, bounded_reason)

    def planning_unavailable(
        self,
        stage: PlanningStage,
        reason: PlanningUnavailabilityReason,
    ) -> None:
        self._safe_increment(self._planning_unavailable, stage, reason)

    def github_unavailable(self, operation: str, reason: str) -> None:
        surface, _, _ = operation.partition(".")
        self._safe_increment(
            self._github_unavailable,
            surface if surface in _GITHUB_SURFACES else "other",
            _member(reason, {"http", "timeout", "unavailable"}),
        )

    def bind_http_route_templates(self, templates: frozenset[str]) -> None:
        if type(templates) is not frozenset or any(
            type(template) is not str for template in templates
        ):
            raise TypeError("HTTP route templates require an exact immutable string set")
        with self._http_route_binding_lock:
            if self._http_route_templates is not None and self._http_route_templates != templates:
                raise ValueError("HTTP route templates are already bound to another catalog")
            self._http_route_templates = templates

    def http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        bounded_method = method if method in _HTTP_METHODS else "OTHER"
        bounded_route = (
            route
            if type(route) is str and route in (self._http_route_templates or ())
            else "unmatched"
        )
        status_class = _status_class(status_code)
        duration = duration_seconds if duration_seconds >= 0 else 0.0
        try:
            labels = (bounded_method, bounded_route, status_class)
            self._http_requests.labels(*labels).inc()
            self._http_duration.labels(*labels).observe(duration)
        except Exception:
            self._record_instrumentation_failure("http")
            return

    def readiness(self, *, ready: bool, unavailable_dependencies: Iterable[str]) -> None:
        unavailable = frozenset(unavailable_dependencies)
        try:
            self._ready.set(int(ready))
            for dependency in unavailable:
                if _admitted_dependency(dependency):
                    self._dependency_ready.labels(dependency).set(0)
        except Exception:
            self._record_instrumentation_failure("readiness")
            return

    def dependency_ready(self, dependency: str) -> None:
        if not _admitted_dependency(dependency):
            return
        try:
            self._dependency_ready.labels(dependency).set(1)
        except Exception:
            self._record_instrumentation_failure("dependency_readiness")
            return

    def background_health(self, health: BackgroundHealth) -> None:
        try:
            self._background_healthy.set(int(health.ready))
            self._background_observable.set(int(health.observable))
            self._background_terminal.set(int(health.terminal_failure))
            if health.terminal_failure and not self._terminal_failure_observed:
                self._background_terminal_total.inc()
                self._terminal_failure_observed = True
        except Exception:
            self._record_instrumentation_failure("background")
            return

    def reconciliation_round(self, result: Literal["failed", "succeeded"]) -> None:
        self._safe_increment(self._reconciliation_rounds, result)

    def maintenance_operation(
        self,
        operation: MaintenanceOperationName,
        result: MaintenanceOperationResult,
        *,
        duration_seconds: float,
    ) -> None:
        bounded_operation = _member(operation, set(_MAINTENANCE_OPERATIONS))
        bounded_result = _member(result, set(_MAINTENANCE_RESULTS))
        duration = duration_seconds if duration_seconds >= 0 else 0.0
        try:
            labels = (bounded_operation, bounded_result)
            self._maintenance_operations.labels(*labels).inc()
            self._maintenance_duration.labels(*labels).observe(duration)
        except Exception:
            self._record_instrumentation_failure("counter")
            return

    def ci_economics_collection_round(
        self,
        *,
        registered: int,
        outcomes: tuple[CiEconomicsCollectionItemMetricOutcome, ...],
    ) -> None:
        if type(registered) is not int or registered < 0 or type(outcomes) is not tuple:
            self._record_instrumentation_failure("counter")
            return
        allowed = set(CI_ECONOMICS_COLLECTION_METRIC_OUTCOMES)
        bounded_outcomes = tuple(
            _member(outcome, allowed) if type(outcome) is str else "other" for outcome in outcomes
        )
        try:
            self._ci_economics_registered_subjects.inc(registered)
            for outcome in bounded_outcomes:
                self._ci_economics_collection_item_outcomes.labels(outcome).inc()
        except Exception:
            self._record_instrumentation_failure("counter")
            return

    def ci_observation_item(self, outcome: str) -> None:
        bounded = _member(outcome, set(CI_OBSERVATION_ITEM_OUTCOMES))
        self._safe_increment(self._ci_observation_item_outcomes, bounded)

    def ci_history_item(self, lane: str, outcome: str) -> None:
        self._safe_increment(
            self._ci_history_items,
            _member(lane, set(_HISTORY_LANES)),
            _member(outcome, set(_HISTORY_ITEM_OUTCOMES)),
        )

    def ci_history_worker_running(self, lane: str, running: bool) -> None:
        if type(running) is not bool:
            self._record_instrumentation_failure("background")
            return
        try:
            self._ci_history_workers_running.labels(_member(lane, set(_HISTORY_LANES))).set(
                int(running)
            )
        except Exception:
            self._record_instrumentation_failure("background")

    def ci_history_provider_result(self, lane: str, outcome: str) -> None:
        self._safe_increment(
            self._ci_history_provider_results,
            _member(lane, set(_HISTORY_LANES)),
            _member(outcome, set(_HISTORY_PROVIDER_OUTCOMES)),
        )

    @contextmanager
    def ci_history_stage(self, lane: str, stage: str) -> Iterator[None]:
        timer: _StageTimer | None = None
        try:
            started_timer: _StageTimer = self._ci_history_stage_duration.time()
            started_timer.__enter__()
            timer = started_timer
        except Exception:
            self._record_instrumentation_failure("timer")
        outcome = "raised"
        try:
            yield
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        else:
            outcome = "returned"
        finally:
            if timer is not None:
                try:
                    timer.labels(
                        lane=_member(lane, set(_HISTORY_LANES)),
                        stage=_member(stage, {"claim", "access", "provider", "completion"}),
                        outcome=outcome,
                    )
                    timer.__exit__(None, None, None)
                except Exception:
                    self._record_instrumentation_failure("timer")

    def ci_history_delivery(
        self,
        outcome: str,
        *,
        candidate_count: int = 0,
        transferred_count: int = 0,
        pending_age_seconds: float | None = None,
        expired_pending_sample: int | None = None,
    ) -> None:
        self._safe_increment(
            self._ci_history_delivery_outcomes, _member(outcome, set(_HISTORY_DELIVERY_OUTCOMES))
        )
        try:
            if (
                type(candidate_count) is not int
                or type(transferred_count) is not int
                or not 0 <= transferred_count <= candidate_count <= 100
                or (
                    expired_pending_sample is not None
                    and (
                        type(expired_pending_sample) is not int
                        or not 0 <= expired_pending_sample <= 100
                    )
                )
                or (
                    pending_age_seconds is not None
                    and (
                        not isfinite(pending_age_seconds) or not 0 <= pending_age_seconds <= 7776000
                    )
                )
            ):
                raise ValueError("archive delivery metric is outside its admitted bounds")
            self._ci_history_delivery_sources.labels("candidates").inc(candidate_count)
            self._ci_history_delivery_sources.labels("transferred").inc(transferred_count)
            if pending_age_seconds is not None:
                self._ci_history_pending_age.observe(pending_age_seconds)
            if expired_pending_sample is not None:
                self._ci_history_expired_pending_sample.observe(expired_pending_sample)
        except Exception:
            self._record_instrumentation_failure("counter")

    def _initialize_bounded_series(self) -> None:
        _initialize(self._planning_preparation, _PREPARATION_OUTCOMES)
        _initialize(self._plan_requests, _PLAN_RESULTS)
        _initialize(self._fallbacks, (*sorted(_FALLBACK_REASONS), "other"))
        _initialize(self._verifier_rejections, (*sorted(_FALLBACK_REASONS), "other"))
        _initialize(self._signed_envelopes, ("created", "duplicate"))
        _initialize(self._invalid_oidc, (*sorted(_OIDC_REASONS), "other"))
        _initialize(
            self._config_activations,
            (
                "applied",
                "coverage_reducing",
                "coverage_unproven",
                "duplicate",
                "forbidden",
                "operation_conflict",
                "other",
                "revision_conflict",
                "target_unavailable",
                "unavailable",
            ),
        )
        _initialize(
            self._shadow_comparisons,
            ("other", "replay_mismatch", "safe", "unknown", "unsafe"),
        )
        _initialize(self._runner_snapshots, ("fresh", "missing", "other", "stale"))
        _initialize(self._reconciliation_rounds, ("failed", "succeeded"))
        for operation in _MAINTENANCE_OPERATIONS:
            for result in _MAINTENANCE_RESULTS:
                self._maintenance_operations.labels(operation, result)
                self._maintenance_duration.labels(operation, result)
        _initialize(
            self._ci_economics_collection_item_outcomes,
            CI_ECONOMICS_COLLECTION_METRIC_OUTCOMES,
        )
        _initialize(self._instrumentation_failures, _INSTRUMENTATION_SURFACES)
        _initialize(self._ci_observation_item_outcomes, CI_OBSERVATION_ITEM_OUTCOMES)
        for stage in _PLANNING_STAGES:
            for reason in _PLANNING_REASONS:
                self._planning_unavailable.labels(stage, reason)

    def _safe_increment(self, counter: Counter, *labels: str) -> None:
        try:
            if labels:
                counter.labels(*labels).inc()
            else:
                counter.inc()
        except Exception:
            self._record_instrumentation_failure("counter")
            return

    def _record_instrumentation_failure(self, surface: str) -> None:
        with self._instrumentation_failure_lock:
            self._instrumentation_failure_count += 1
        try:
            self._instrumentation_failures.labels(surface).inc()
        except Exception:
            return


def _initialize(counter: Counter, values: tuple[str, ...]) -> None:
    for value in values:
        counter.labels(value)


def _member(value: str, allowed: set[str]) -> str:
    return value if value in allowed else "other"


def _prefixed_member(value: str, allowed: frozenset[str]) -> str:
    for candidate in allowed:
        if value == candidate or value.startswith(candidate + ":"):
            return candidate
    return "other"


def _oidc_reason(value: str) -> str:
    normalized = value.removeprefix("oidc_")
    return normalized if normalized in _OIDC_REASONS else "other"


def _admitted_dependency(dependency: str) -> bool:
    return (
        type(dependency) is str
        and 1 <= len(dependency) <= 64
        and all(
            character.isascii() and (character.isalnum() or character == "_")
            for character in dependency
        )
    )


def _status_class(status_code: int) -> str:
    if type(status_code) is not int or not 100 <= status_code <= 599:
        return "unknown"
    return f"{status_code // 100}xx"
