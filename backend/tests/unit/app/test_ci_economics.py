from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import cast

import pytest
from ci_economics.factories import (
    ATTEMPT,
    CONTRACT,
    NOW,
    SUBJECT,
    job,
    provider_snapshot,
    recorded_measurements,
    retained_snapshot,
)
from prometheus_support import prometheus_samples

from ci_coordinator.app.ci_economics import (
    COLLECTION_ITEM_OUTCOMES,
    AttemptJobsAvailable,
    AttemptSummariesAvailable,
    CiEconomicsCollectionRound,
    CiEconomicsCollectionService,
    CiEconomicsReadForbidden,
    CiEconomicsReadNotFound,
    CiEconomicsReadService,
    CiEconomicsReadUnavailable,
    CiEconomicsRetentionService,
    CollectionItemOutcome,
)
from ci_coordinator.ci_economics import (
    MAX_ECONOMICS_PAGE_SIZE,
    AttemptEconomics,
    AttemptIdentity,
    AttemptSummary,
    AttemptSummaryPage,
    CiEconomicsEvidenceConflict,
    CiEconomicsProfile,
    CiEconomicsStoreUnavailable,
    ClaimTransitionResult,
    CollectionClaim,
    ProviderAttemptDeferred,
    ProviderAttemptSnapshot,
    RetryableCollectionFailureReason,
    SnapshotRecordResult,
    WorkflowJobFact,
    derive_attempt_economics,
    load_bundled_ci_economics_profile,
)
from ci_coordinator.ci_economics.catalog import ProviderSourcePage
from ci_coordinator.ci_economics.read_models import RecordedAttemptEconomics
from ci_coordinator.ci_economics.sources import CollectionSource, ReconciliationCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import SystemMonotonicClock
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.observability.runtime_metrics import (
    CI_ECONOMICS_COLLECTION_METRIC_OUTCOMES,
)
from ci_coordinator.runtime.maintenance_round import (
    BoundedMaintenanceOperation,
    RuntimeMaintenanceRound,
)


@dataclass
class _Authorizer:
    allowed: bool
    calls: list[tuple[str, RepositoryScope]] = field(default_factory=list)

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append((actor, scope))
        return self.allowed


@dataclass
class _Query:
    page: AttemptSummaryPage | Exception
    economics: AttemptEconomics | Exception | None
    calls: list[tuple[str, object]] = field(default_factory=list)
    measurements: RecordedAttemptEconomics | BaseException | None = None

    async def list_provider_sources(
        self, scope: RepositoryScope, *, after_cursor: str | None, limit: int
    ) -> ProviderSourcePage:
        raise AssertionError("unexpected source catalog read")

    async def load_measurements(self, attempt: AttemptIdentity) -> RecordedAttemptEconomics | None:
        self.calls.append(("measurements", attempt))
        if isinstance(self.measurements, BaseException):
            raise self.measurements
        return self.measurements

    async def list_attempts(
        self,
        scope: RepositoryScope,
        *,
        after_cursor: str | None,
        limit: int,
    ) -> AttemptSummaryPage:
        self.calls.append(("list", (scope, after_cursor, limit)))
        if isinstance(self.page, Exception):
            raise self.page
        return self.page

    async def load_attempt(self, attempt: AttemptIdentity) -> AttemptEconomics | None:
        self.calls.append(("load", attempt))
        if isinstance(self.economics, Exception):
            raise self.economics
        return self.economics


@dataclass
class _CollectionStore:
    claims: list[CollectionClaim]
    record_result: SnapshotRecordResult = "captured"
    transition_result: ClaimTransitionResult = "applied"
    registered: int = 0
    deferred: list[tuple[CollectionClaim, RetryableCollectionFailureReason]] = field(
        default_factory=list
    )
    rejected: list[CollectionClaim] = field(default_factory=list)
    recorded: list[tuple[CollectionClaim, ProviderAttemptSnapshot]] = field(default_factory=list)

    async def register_eligible(self, *, limit: int) -> int:
        assert limit >= len(self.claims)
        return self.registered

    async def claim_next(self, *, worker_id: str) -> CollectionClaim | None:
        assert worker_id == "c" * 64
        return self.claims.pop(0) if self.claims else None

    async def defer_claim(
        self,
        claim: CollectionClaim,
        reason: RetryableCollectionFailureReason,
    ) -> ClaimTransitionResult:
        self.deferred.append((claim, reason))
        return self.transition_result

    async def record_snapshot(
        self,
        claim: CollectionClaim,
        snapshot: ProviderAttemptSnapshot,
    ) -> SnapshotRecordResult:
        self.recorded.append((claim, snapshot))
        if self.record_result == "claim_lost":
            return "claim_lost"
        return self.record_result

    async def reject_claim(self, claim: CollectionClaim) -> ClaimTransitionResult:
        self.rejected.append(claim)
        return self.transition_result


@dataclass
class _RetentionStore:
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def expire_evidence(self, *, limit: int) -> int:
        self.calls.append(("expire", limit))
        return 0

    async def purge_tombstones(self, *, limit: int) -> int:
        self.calls.append(("purge", limit))
        return 0

    async def delete_expired_observations(self, *, limit: int) -> int:
        self.calls.append(("delete", limit))
        return 0


@dataclass
class _EvidenceProvider:
    outcome: ProviderAttemptSnapshot | ProviderAttemptDeferred | Exception
    active: int = 0
    peak_active: int = 0

    async def load_stable(
        self,
        source: CollectionSource,
    ) -> ProviderAttemptSnapshot | ProviderAttemptDeferred:
        assert source == ReconciliationCollectionSource(SUBJECT, CONTRACT)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize("independent", (False, True))
def test_measurement_read_preserves_exact_source(independent: bool) -> None:
    expected = recorded_measurements(independent=independent)
    query = _Query(_page(ATTEMPT), None, measurements=expected)
    authorizer = _Authorizer(True)
    result = asyncio.run(
        CiEconomicsReadService(authorizer=authorizer, query=query).load_measurements(
            actor="actor", attempt=ATTEMPT
        )
    )
    assert result is expected
    assert authorizer.calls == [("actor", ATTEMPT.scope)]
    assert query.calls == [("measurements", ATTEMPT)]


@pytest.mark.parametrize(
    ("allowed", "stored", "expected"),
    [
        (False, None, CiEconomicsReadForbidden),
        (True, None, CiEconomicsReadNotFound),
        (True, CiEconomicsStoreUnavailable("offline"), CiEconomicsReadUnavailable),
        (True, cast(RecordedAttemptEconomics, object()), CiEconomicsReadUnavailable),
    ],
)
def test_measurement_read_failure_algebra_and_authority_order(
    allowed: bool,
    stored: RecordedAttemptEconomics | BaseException | None,
    expected: type[object],
) -> None:
    query = _Query(_page(ATTEMPT), None, measurements=stored)
    result = asyncio.run(
        CiEconomicsReadService(authorizer=_Authorizer(allowed), query=query).load_measurements(
            actor="actor", attempt=ATTEMPT
        )
    )
    assert type(result) is expected
    assert query.calls == ([("measurements", ATTEMPT)] if allowed else [])


@pytest.mark.parametrize(
    "attempt",
    [
        replace(ATTEMPT, scope=RepositoryScope(102, 202)),
        replace(ATTEMPT, scope=RepositoryScope(101, 203)),
        replace(ATTEMPT, workflow_run_id=304),
        replace(ATTEMPT, run_attempt=3),
        replace(ATTEMPT, head_sha="a" * 40),
    ],
)
def test_measurement_read_rebinds_every_response_operand(attempt: AttemptIdentity) -> None:
    query = _Query(_page(ATTEMPT), None, measurements=recorded_measurements(attempt=attempt))
    result = asyncio.run(
        CiEconomicsReadService(authorizer=_Authorizer(True), query=query).load_measurements(
            actor="actor", attempt=ATTEMPT
        )
    )
    assert isinstance(result, CiEconomicsReadUnavailable)


@pytest.mark.parametrize("error", (asyncio.CancelledError(), RuntimeError("programmer error")))
def test_measurement_read_does_not_mask_cancellation_or_programmer_error(
    error: BaseException,
) -> None:
    query = _Query(_page(ATTEMPT), None, measurements=error)
    with pytest.raises(type(error)):
        asyncio.run(
            CiEconomicsReadService(authorizer=_Authorizer(True), query=query).load_measurements(
                actor="actor", attempt=ATTEMPT
            )
        )


def test_ci_economics_read_authorizes_before_touching_the_store() -> None:
    authorizer = _Authorizer(False)
    query = _Query(_page(ATTEMPT), _economics())
    service = CiEconomicsReadService(authorizer=authorizer, query=query)

    result = asyncio.run(
        service.list_attempts(
            actor="actor",
            scope=ATTEMPT.scope,
            after_cursor=None,
            limit=10,
        )
    )

    assert isinstance(result, CiEconomicsReadForbidden)
    assert query.calls == []


def test_ci_economics_read_rejects_cross_scope_repository_results() -> None:
    foreign = AttemptIdentity(RepositoryScope(9, 9), 303, 2, "b" * 40)
    service = CiEconomicsReadService(
        authorizer=_Authorizer(True),
        query=_Query(_page(foreign), _economics()),
    )

    result = asyncio.run(
        service.list_attempts(
            actor="actor",
            scope=ATTEMPT.scope,
            after_cursor=None,
            limit=10,
        )
    )

    assert isinstance(result, CiEconomicsReadUnavailable)


def test_ci_economics_job_read_returns_a_stable_bounded_page() -> None:
    economics = _economics(job_ids=(404, 405, 406))
    query = _Query(_page(ATTEMPT), economics)
    service = CiEconomicsReadService(authorizer=_Authorizer(True), query=query)

    result = asyncio.run(
        service.load_attempt_jobs(
            actor="actor",
            attempt=ATTEMPT,
            after_job_id=404,
            limit=1,
        )
    )

    assert isinstance(result, AttemptJobsAvailable)
    assert tuple(item.provider_job_id for item in result.jobs) == (405,)
    assert result.next_job_id == 405
    assert query.calls == [("load", ATTEMPT)]


@pytest.mark.parametrize(
    ("query_outcome", "allowed", "expected_type"),
    [
        ("available", False, CiEconomicsReadForbidden),
        ("unavailable", True, CiEconomicsReadUnavailable),
        ("missing", True, CiEconomicsReadNotFound),
    ],
)
def test_ci_economics_job_read_preserves_failure_outcomes(
    query_outcome: str,
    allowed: bool,
    expected_type: type[object],
) -> None:
    query_result: AttemptEconomics | Exception | None
    if query_outcome == "available":
        query_result = _economics()
    elif query_outcome == "unavailable":
        query_result = CiEconomicsStoreUnavailable("unavailable")
    else:
        query_result = None
    service = CiEconomicsReadService(
        authorizer=_Authorizer(allowed),
        query=_Query(_page(ATTEMPT), query_result),
    )

    result = asyncio.run(
        service.load_attempt_jobs(
            actor="actor",
            attempt=ATTEMPT,
            after_job_id=None,
            limit=10,
        )
    )

    assert isinstance(result, expected_type)


def test_ci_economics_job_read_rejects_cross_attempt_results() -> None:
    foreign = AttemptIdentity(RepositoryScope(9, 9), 303, 2, "b" * 40)
    service = CiEconomicsReadService(
        authorizer=_Authorizer(True),
        query=_Query(_page(ATTEMPT), _economics()),
    )

    result = asyncio.run(
        service.load_attempt_jobs(
            actor="actor",
            attempt=foreign,
            after_job_id=None,
            limit=10,
        )
    )

    assert isinstance(result, CiEconomicsReadUnavailable)


@pytest.mark.parametrize("cursor", [0, True, "1"])
def test_ci_economics_job_read_rejects_non_positive_or_non_integer_cursors(
    cursor: object,
) -> None:
    service = CiEconomicsReadService(
        authorizer=_Authorizer(True),
        query=_Query(_page(ATTEMPT), _economics()),
    )

    with pytest.raises(ValueError, match="cursor must be positive"):
        asyncio.run(
            service.load_attempt_jobs(
                actor="actor",
                attempt=ATTEMPT,
                after_job_id=cast(int | None, cursor),
                limit=10,
            )
        )


@pytest.mark.parametrize("limit", [0, MAX_ECONOMICS_PAGE_SIZE + 1, True])
def test_ci_economics_reads_reject_page_limits_outside_the_contract(limit: object) -> None:
    service = CiEconomicsReadService(
        authorizer=_Authorizer(True),
        query=_Query(_page(ATTEMPT), _economics()),
    )

    with pytest.raises(ValueError, match="page size"):
        asyncio.run(
            service.list_attempts(
                actor="actor",
                scope=ATTEMPT.scope,
                after_cursor=None,
                limit=cast(int, limit),
            )
        )


def test_ci_economics_job_page_rejects_cross_attempt_and_cursor_substitution() -> None:
    economics = _economics()
    foreign_job = job(
        attempt=AttemptIdentity(RepositoryScope(9, 9), 303, 2, "b" * 40),
        created_at=None,
        delivery_id=None,
    )

    with pytest.raises(ValueError, match="crosses attempt identity"):
        AttemptJobsAvailable(economics, (foreign_job,), None)
    with pytest.raises(ValueError, match="cursor does not identify"):
        AttemptJobsAvailable(economics, economics.snapshot.jobs, 999)
    with pytest.raises(ValueError, match="cardinality bound"):
        AttemptJobsAvailable(
            economics,
            economics.snapshot.jobs * (MAX_ECONOMICS_PAGE_SIZE + 1),
            None,
        )


def test_ci_economics_result_envelopes_reject_non_exact_values() -> None:
    economics = _economics()

    with pytest.raises(TypeError, match="exact page"):
        AttemptSummariesAvailable(cast(AttemptSummaryPage, object()))
    with pytest.raises(TypeError, match="exact attempt economics"):
        AttemptJobsAvailable(cast(AttemptEconomics, object()), (), None)
    with pytest.raises(TypeError, match="exact job facts"):
        AttemptJobsAvailable(
            economics,
            cast(tuple[WorkflowJobFact, ...], list(economics.snapshot.jobs)),
            None,
        )


def test_ci_economics_reads_reject_non_exact_identity_and_scope() -> None:
    service = CiEconomicsReadService(
        authorizer=_Authorizer(True),
        query=_Query(_page(ATTEMPT), _economics()),
    )

    with pytest.raises(TypeError, match="exact repository scope"):
        asyncio.run(
            service.list_attempts(
                actor="actor",
                scope=cast(RepositoryScope, object()),
                after_cursor=None,
                limit=10,
            )
        )
    with pytest.raises(TypeError, match="exact attempt identity"):
        asyncio.run(
            service.load_attempt_jobs(
                actor="actor",
                attempt=cast(AttemptIdentity, object()),
                after_job_id=None,
                limit=10,
            )
        )


def test_collection_service_bounds_concurrency_and_reports_each_claim_slot() -> None:
    store = _CollectionStore([_claim("d"), _claim("e"), _claim("f")], registered=3)
    provider = _EvidenceProvider(provider_snapshot())
    metrics = RuntimeMetrics()
    service = CiEconomicsCollectionService(
        store,
        provider,
        replace(
            load_bundled_ci_economics_profile(),
            maximum_claims_per_round=3,
            maximum_concurrent_claims=2,
        ),
        runtime_metrics=metrics,
        worker_id="c" * 64,
    )

    result = asyncio.run(service.run(asyncio.Event()))

    assert COLLECTION_ITEM_OUTCOMES == (
        "aborted",
        "captured",
        "claim_lost",
        "deferred",
        "none_due",
        "terminal_conflict",
    )
    assert (*COLLECTION_ITEM_OUTCOMES, "other") == CI_ECONOMICS_COLLECTION_METRIC_OUTCOMES
    assert result.registered == 3
    assert Counter(result.outcomes) == Counter({"captured": 3})
    assert provider.peak_active == 2
    assert len(store.recorded) == 3
    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_ci_economics_registered_subjects_total", ())] == 3
    assert (
        samples[
            (
                "ci_coordinator_ci_economics_collection_item_outcomes_total",
                (("outcome", "captured"),),
            )
        ]
        == 3
    )


@pytest.mark.parametrize(
    ("provider_outcome", "expected_outcome", "expected_reason"),
    [
        (
            ProviderAttemptDeferred("provider_incomplete"),
            "deferred",
            "provider_incomplete",
        ),
        (RuntimeError("provider failed"), "deferred", "unexpected_error"),
    ],
)
def test_collection_service_persists_transient_provider_outcomes(
    provider_outcome: ProviderAttemptDeferred | Exception,
    expected_outcome: str,
    expected_reason: RetryableCollectionFailureReason,
) -> None:
    claim = _claim("d")
    store = _CollectionStore([claim])
    metrics = RuntimeMetrics()
    service = _collection_service(
        store,
        _EvidenceProvider(provider_outcome),
        runtime_metrics=metrics,
    )

    result = asyncio.run(service.run(asyncio.Event()))

    assert result.outcomes[0] == expected_outcome
    assert store.deferred == [(claim, expected_reason)]
    assert (
        prometheus_samples(metrics)[
            (
                "ci_coordinator_ci_economics_collection_item_outcomes_total",
                (("outcome", "deferred"),),
            )
        ]
        == 1
    )


def test_deferred_collection_is_a_successful_maintenance_operation_with_semantic_outcome() -> None:
    claim = _claim("d")
    store = _CollectionStore([claim])
    metrics = RuntimeMetrics()
    service = _collection_service(
        store,
        _EvidenceProvider(ProviderAttemptDeferred("provider_unavailable")),
        runtime_metrics=metrics,
    )

    async def primary(_: asyncio.Event) -> None:
        return None

    runtime = RuntimeMaintenanceRound(
        primary,
        (BoundedMaintenanceOperation("ci_economics_collection", 1, service),),
        metrics=metrics,
        clock=SystemMonotonicClock(),
    )

    async def exercise() -> None:
        abort_signal = asyncio.Event()
        await runtime(abort_signal)
        await runtime(abort_signal)

    asyncio.run(exercise())

    samples = prometheus_samples(metrics)
    assert store.deferred == [(claim, "provider_unavailable")]
    assert (
        samples[
            (
                "ci_coordinator_maintenance_operations_total",
                (
                    ("operation", "ci_economics_collection"),
                    ("result", "succeeded"),
                ),
            )
        ]
        == 1
    )
    assert (
        samples[
            (
                "ci_coordinator_ci_economics_collection_item_outcomes_total",
                (("outcome", "deferred"),),
            )
        ]
        == 1
    )


def test_collection_service_terminalizes_conflicting_immutable_evidence() -> None:
    claim = _claim("d")
    store = _CollectionStore([claim])

    class _ConflictStore(_CollectionStore):
        async def record_snapshot(
            self,
            claim: CollectionClaim,
            snapshot: ProviderAttemptSnapshot,
        ) -> SnapshotRecordResult:
            del claim, snapshot
            raise CiEconomicsEvidenceConflict("conflict")

    conflict_store = _ConflictStore(store.claims)
    service = _collection_service(conflict_store, _EvidenceProvider(provider_snapshot()))

    result = asyncio.run(service.run(asyncio.Event()))

    assert result.outcomes[0] == "terminal_conflict"
    assert conflict_store.rejected == [claim]


@pytest.mark.parametrize(
    "provider_outcome",
    [ProviderAttemptDeferred("provider_incomplete"), RuntimeError("provider failed")],
)
def test_collection_service_reports_claim_loss_during_deferral(
    provider_outcome: ProviderAttemptDeferred | Exception,
) -> None:
    store = _CollectionStore([_claim("d")], transition_result="claim_lost")

    result = asyncio.run(
        _collection_service(store, _EvidenceProvider(provider_outcome)).run(asyncio.Event())
    )

    assert result.outcomes == ("claim_lost",)


def test_collection_service_reports_claim_loss_during_conflict_terminalization() -> None:
    claim = _claim("d")

    class _ConflictStore(_CollectionStore):
        async def record_snapshot(
            self,
            claim: CollectionClaim,
            snapshot: ProviderAttemptSnapshot,
        ) -> SnapshotRecordResult:
            del claim, snapshot
            raise CiEconomicsEvidenceConflict("conflict")

    store = _ConflictStore([claim], transition_result="claim_lost")

    result = asyncio.run(
        _collection_service(store, _EvidenceProvider(provider_snapshot())).run(asyncio.Event())
    )

    assert result.outcomes == ("claim_lost",)


@pytest.mark.parametrize(("aborted", "expected"), [(True, "aborted"), (False, "none_due")])
def test_collection_service_preserves_empty_and_aborted_slots(
    aborted: bool,
    expected: str,
) -> None:
    signal = asyncio.Event()
    if aborted:
        signal.set()

    result = asyncio.run(
        _collection_service(
            _CollectionStore([]),
            _EvidenceProvider(provider_snapshot()),
        ).run(signal)
    )

    assert result.outcomes == (expected,)


def test_collection_round_rejects_non_canonical_results() -> None:
    with pytest.raises(ValueError, match="registered collection count"):
        CiEconomicsCollectionRound(-1, ())
    with pytest.raises(TypeError, match="exact tuple"):
        CiEconomicsCollectionRound(0, cast(tuple[CollectionItemOutcome, ...], []))
    with pytest.raises(ValueError, match="invalid outcome"):
        CiEconomicsCollectionRound(
            0,
            cast(tuple[CollectionItemOutcome, ...], ("unknown",)),
        )


@pytest.mark.parametrize("worker_id", ["short", "G" * 64, 7])
def test_collection_service_rejects_non_canonical_worker_identity(worker_id: object) -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        CiEconomicsCollectionService(
            _CollectionStore([]),
            _EvidenceProvider(provider_snapshot()),
            load_bundled_ci_economics_profile(),
            runtime_metrics=RuntimeMetrics(),
            worker_id=cast(str, worker_id),
        )


def test_collection_and_retention_services_require_the_exact_profile() -> None:
    profile = cast(CiEconomicsProfile, object())

    with pytest.raises(TypeError, match="collection requires an exact profile"):
        CiEconomicsCollectionService(
            _CollectionStore([]),
            _EvidenceProvider(provider_snapshot()),
            profile,
            runtime_metrics=RuntimeMetrics(),
            worker_id="c" * 64,
        )
    with pytest.raises(TypeError, match="exact runtime metrics"):
        CiEconomicsCollectionService(
            _CollectionStore([]),
            _EvidenceProvider(provider_snapshot()),
            load_bundled_ci_economics_profile(),
            runtime_metrics=cast(RuntimeMetrics, object()),
            worker_id="c" * 64,
        )
    with pytest.raises(TypeError, match="retention requires an exact profile"):
        CiEconomicsRetentionService(_RetentionStore(), profile)


def test_collection_service_callable_runs_the_same_bounded_round() -> None:
    service = _collection_service(_CollectionStore([]), _EvidenceProvider(provider_snapshot()))

    assert asyncio.run(service(asyncio.Event())) is None


def test_retention_services_honor_abort_and_use_the_profile_batch_bound() -> None:
    profile = load_bundled_ci_economics_profile()
    store = _RetentionStore()
    service = CiEconomicsRetentionService(store, profile)

    async def exercise() -> None:
        active = asyncio.Event()
        await service.expire_evidence(active)
        await service.purge_tombstones(active)
        await service.delete_expired_observations(active)
        active.set()
        await service.expire_evidence(active)
        await service.purge_tombstones(active)
        await service.delete_expired_observations(active)

    asyncio.run(exercise())

    assert store.calls == [
        ("expire", profile.cleanup_batch_size),
        ("purge", profile.cleanup_batch_size),
        ("delete", profile.cleanup_batch_size),
    ]


@pytest.mark.parametrize(
    ("failure", "expected_type"),
    [
        (CiEconomicsStoreUnavailable("unavailable"), CiEconomicsReadUnavailable),
        (None, AttemptSummariesAvailable),
    ],
)
def test_ci_economics_list_preserves_available_and_unavailable_results(
    failure: Exception | None,
    expected_type: type[object],
) -> None:
    service = CiEconomicsReadService(
        authorizer=_Authorizer(True),
        query=_Query(failure or _page(ATTEMPT), _economics()),
    )

    result = asyncio.run(
        service.list_attempts(
            actor="actor",
            scope=ATTEMPT.scope,
            after_cursor=None,
            limit=10,
        )
    )

    assert isinstance(result, expected_type)


def _page(attempt: AttemptIdentity) -> AttemptSummaryPage:
    summary = AttemptSummary(
        subject_id="c" * 64,
        attempt=attempt,
        contract_hash="d" * 64,
        planned_route="selected",
        job_count=1,
        snapshot_digest="e" * 64,
        recorded_at=NOW,
    )
    return AttemptSummaryPage((summary,), None)


def _economics(*, job_ids: tuple[int, ...] = (404,)) -> AttemptEconomics:
    provider_jobs = tuple(
        job(provider_job_id=job_id, created_at=None, delivery_id=None) for job_id in job_ids
    )
    webhook_jobs = tuple(job(provider_job_id=job_id) for job_id in job_ids)
    return derive_attempt_economics(retained_snapshot(*provider_jobs), webhook_jobs)


def _collection_service(
    store: _CollectionStore,
    provider: _EvidenceProvider,
    *,
    runtime_metrics: RuntimeMetrics | None = None,
) -> CiEconomicsCollectionService:
    return CiEconomicsCollectionService(
        store,
        provider,
        replace(
            load_bundled_ci_economics_profile(),
            maximum_claims_per_round=1,
            maximum_concurrent_claims=1,
        ),
        runtime_metrics=RuntimeMetrics() if runtime_metrics is None else runtime_metrics,
        worker_id="c" * 64,
    )


def _claim(token_prefix: str) -> CollectionClaim:
    policy = load_bundled_ci_economics_profile().collection_policy
    return CollectionClaim(
        source=ReconciliationCollectionSource(SUBJECT, CONTRACT),
        revision=1,
        generation=1,
        attempt_count=1,
        policy_hash=policy.policy_hash,
        worker_id="c" * 64,
        token=token_prefix * 64,
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
