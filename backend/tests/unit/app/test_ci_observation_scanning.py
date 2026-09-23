import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import cast

import pytest
from ci_economics.observation_factories import claimed_scan
from prometheus_support import prometheus_samples

from ci_coordinator.app import ci_observation_scanning
from ci_coordinator.app.ci_observation_scanning import CiObservationScanningService
from ci_coordinator.ci_economics.collection import ProviderCollectionFailureReason
from ci_coordinator.ci_economics.discovery import (
    ProviderObservationPage,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.observation_ports import (
    ClaimedObservation,
    ObservationTransitionResult,
)
from ci_coordinator.ci_economics.observation_progress import ObservationFailure
from ci_coordinator.ci_economics.observation_scan import ObservationClaim
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable, ProviderAttemptDeferred
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

SNAPSHOT, _, CLAIM = claimed_scan()
WORK = ClaimedObservation(SNAPSHOT, CLAIM)
PAGE = ProviderObservationPage(
    ProviderRunDiscoveryPage(CLAIM.scope, CLAIM.cursor.window, 1, 0, (), "exhausted"), ()
)
type Hook = Callable[[str], Awaitable[None]]


@dataclass
class Boundary:
    claims: deque[ClaimedObservation] = field(default_factory=lambda: deque([WORK]))
    allowed: bool = True
    page: ProviderObservationPage | ProviderAttemptDeferred = PAGE
    transition: ObservationTransitionResult = "applied"
    errors: dict[str, BaseException] = field(default_factory=dict)
    hook: Hook | None = None
    calls: list[tuple[str, object]] = field(default_factory=list)
    metrics: RuntimeMetrics = field(default_factory=RuntimeMetrics)

    async def record(self, stage: str, value: object = None) -> None:
        self.calls.append((stage, value))
        if self.hook is not None:
            await self.hook(stage)
        if stage in self.errors:
            raise self.errors[stage]

    async def claim_observation(self, *, worker_id: str) -> ClaimedObservation | None:
        await self.record("claim", worker_id)
        return self.claims.popleft() if self.claims else None

    async def allows_repository(self, scope: RepositoryScope) -> bool:
        await self.record("access", scope)
        return self.allowed

    async def discover_observation_page(
        self, scope: RepositoryScope, window: RunDiscoveryWindow, *, page_number: int
    ) -> ProviderObservationPage | ProviderAttemptDeferred:
        await self.record("discover", (scope, window, page_number))
        return self.page

    async def record_observation_page(
        self, claim: ObservationClaim, page: ProviderObservationPage
    ) -> ObservationTransitionResult:
        await self.record("page", (claim, page))
        return self.transition

    async def defer_observation(
        self, claim: ObservationClaim, reason: ObservationFailure
    ) -> ObservationTransitionResult:
        await self.record("defer", (claim, reason))
        return self.transition

    async def purge_observation_gaps(self, *, scope_limit: int) -> int:
        await self.record("purge", scope_limit)
        return 0

    def service(self, *, worker_id: str = "a" * 64) -> CiObservationScanningService:
        return CiObservationScanningService(
            store=self,
            provider=self,
            repository_access=self,
            worker_id=worker_id,
            metrics=self.metrics,
        )

    def count(self, outcome: str) -> float:
        return prometheus_samples(self.metrics)[
            ("ci_coordinator_ci_observation_item_outcomes_total", (("outcome", outcome),))
        ]


@pytest.mark.parametrize(
    "worker_id", [None, True, b"a" * 64, "a" * 63, "a" * 65, "A" * 64, "g" * 64]
)
def test_observation_worker_identity_is_rejected_before_effects(worker_id: object) -> None:
    boundary = Boundary()
    with pytest.raises(ValueError):
        boundary.service(worker_id=cast(str, worker_id))
    assert boundary.calls == []


async def test_one_page_uses_current_access_and_exact_claim_without_changing_identity() -> None:
    boundary = Boundary()
    result = await boundary.service().run(asyncio.Event())
    assert result.count("page_recorded") == 1
    assert boundary.count("page_recorded") == 1
    assert [call for call in boundary.calls if call[0] != "claim"] == [
        ("access", CLAIM.scope),
        ("discover", (CLAIM.scope, CLAIM.cursor.window, 1)),
        ("page", (CLAIM, PAGE)),
    ]


@pytest.mark.parametrize("authority", ["denied", "malformed", "unavailable"])
async def test_membership_failure_never_reads_provider_and_records_a_distinct_reason(
    authority: str,
) -> None:
    boundary = Boundary(allowed=cast(bool, 1) if authority == "malformed" else False)
    if authority == "unavailable":
        boundary.errors["access"] = RepositoryAccessUnavailable("private")
    outcomes = await boundary.service().run(asyncio.Event())
    assert outcomes.count("access_unavailable") == 1
    assert not any(stage in {"discover", "page"} for stage, _ in boundary.calls)
    assert ("defer", (CLAIM, "access_unavailable")) in boundary.calls
    assert boundary.count("access_unavailable") == 1
    assert boundary.count("page_recorded") == 0


@pytest.mark.parametrize(
    "reason",
    [
        "provider_unavailable",
        "provider_malformed",
        "provider_binding_mismatch",
        "provider_incomplete",
        "provider_not_terminal",
        "provider_unstable",
    ],
)
async def test_provider_failure_is_durable_and_not_a_recorded_page(
    reason: ProviderCollectionFailureReason,
) -> None:
    boundary = Boundary(page=ProviderAttemptDeferred(reason))
    outcomes = await boundary.service().run(asyncio.Event())
    assert reason in outcomes
    assert ("defer", (CLAIM, reason)) in boundary.calls
    assert boundary.count(reason) == 1
    assert boundary.count("page_recorded") == 0


@pytest.mark.parametrize("stage", ["claim", "access", "discover"])
async def test_abort_after_each_await_prevents_the_next_effect(stage: str) -> None:
    abort = asyncio.Event()

    async def signal(current: str) -> None:
        if current == stage:
            abort.set()

    boundary = Boundary(hook=signal)
    outcomes = await boundary.service().run(abort)
    assert set(outcomes) == {"aborted"}
    effect_names = [name for name, _ in boundary.calls]
    assert (
        effect_names
        == {
            "claim": ["claim"],
            "access": ["claim", "access"],
            "discover": ["claim", "access", "discover"],
        }[stage]
    )
    assert boundary.count("page_recorded") == 0


@pytest.mark.parametrize("stage", ["claim", "access", "discover", "page", "defer"])
async def test_cancellation_remains_cancellation_without_completion_or_retry(stage: str) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()

    async def block(current: str) -> None:
        if current == stage:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    boundary = Boundary(
        hook=block,
        page=ProviderAttemptDeferred("provider_unavailable") if stage == "defer" else PAGE,
    )
    async with asyncio.timeout(3):
        task = asyncio.create_task(boundary.service().run(asyncio.Event()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped.is_set()
    assert boundary.count("page_recorded") == 0
    assert boundary.count("aborted") >= 1


@pytest.mark.parametrize("stage", ["claim", "page", "defer"])
async def test_database_failure_does_not_publish_success(stage: str) -> None:
    boundary = Boundary(
        errors={stage: CiEconomicsStoreUnavailable("private")},
        page=ProviderAttemptDeferred("provider_unavailable") if stage == "defer" else PAGE,
    )
    outcomes = await boundary.service().run(asyncio.Event())
    assert "store_unavailable" in outcomes
    assert boundary.count("page_recorded") == 0


@pytest.mark.parametrize("transition", ["claim_lost", "capacity_reached"])
async def test_commit_result_is_not_relabelled_as_page_success(
    transition: ObservationTransitionResult,
) -> None:
    boundary = Boundary(transition=transition)
    outcomes = await boundary.service().run(asyncio.Event())
    assert transition in outcomes
    assert boundary.count("page_recorded") == 0
    assert boundary.count(transition) == 1


async def test_lost_worker_identity_performs_no_external_or_persistence_effect() -> None:
    foreign = replace(CLAIM, lease=replace(CLAIM.lease, worker_id="f" * 64))
    boundary = Boundary(claims=deque([ClaimedObservation(SNAPSHOT, foreign)]))
    assert "store_unavailable" in await boundary.service().run(asyncio.Event())
    assert all(stage == "claim" for stage, _ in boundary.calls)


@pytest.mark.parametrize("aborted", [False, True])
async def test_gap_cleanup_is_bounded_and_abort_aware(aborted: bool) -> None:
    boundary = Boundary()
    abort = asyncio.Event()
    if aborted:
        abort.set()
    await boundary.service().purge_gaps(abort)
    assert boundary.calls == ([] if aborted else [("purge", 4)])


async def test_two_workers_bound_concurrency_and_claim_population() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    active = peak = 0

    async def hold(stage: str) -> None:
        nonlocal active, peak
        if stage == "discover":
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
            finally:
                active -= 1

    work = deque(
        ClaimedObservation(
            SNAPSHOT,
            replace(CLAIM, expected_revision=10 + i, lease=replace(CLAIM.lease, token=f"{i:064x}")),
        )
        for i in range(8)
    )
    boundary = Boundary(claims=work, hook=hold)
    async with asyncio.timeout(3):
        task = asyncio.create_task(boundary.service().run(asyncio.Event()))
        try:
            await entered.wait()
            assert len([stage for stage, _ in boundary.calls if stage == "claim"]) == 2
            assert len(work) == 6
        finally:
            release.set()
        outcomes = await task
    assert outcomes == ("page_recorded",) * 4
    assert peak == 2 and active == 0 and len(work) == 4


@pytest.mark.parametrize("stage", ["access", "discover"])
async def test_complete_provider_deadline_includes_access_and_discovery(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ci_observation_scanning.OBSERVATION_PROVIDER_DEADLINE_SECONDS == 20
    monkeypatch.setattr(ci_observation_scanning, "OBSERVATION_PROVIDER_DEADLINE_SECONDS", 0.01)
    stopped = asyncio.Event()

    async def hang(current: str) -> None:
        if current == stage:
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    boundary = Boundary(hook=hang)
    async with asyncio.timeout(3):
        outcomes = await boundary.service().run(asyncio.Event())
    assert stopped.is_set() and "timed_out" in outcomes
    assert ("defer", (CLAIM, "timed_out")) in boundary.calls
    assert boundary.count("timed_out") == 1
    assert not any(name == "page" for name, _ in boundary.calls)


async def test_foreign_timeout_does_not_claim_local_deadline_expiry() -> None:
    boundary = Boundary(errors={"discover": TimeoutError("private")})
    assert "provider_unavailable" in await boundary.service().run(asyncio.Event())
    assert boundary.count("timed_out") == 0
    assert ("defer", (CLAIM, "provider_unavailable")) in boundary.calls


@pytest.mark.parametrize("operand", ["scope", "window", "page_number", "type"])
async def test_misbound_provider_response_is_never_registered(operand: str) -> None:
    page = PAGE.page
    if operand == "scope":
        page = replace(page, scope=RepositoryScope(999, 888))
    elif operand == "window":
        page = replace(
            page, window=RunDiscoveryWindow(page.window.created_from, page.window.created_from)
        )
    elif operand == "page_number":
        page = replace(page, page_number=2, provider_total=101, termination="truncated")
    boundary = Boundary(
        page=cast(ProviderObservationPage, object())
        if operand == "type"
        else ProviderObservationPage(page, ())
    )
    assert "provider_binding_mismatch" in await boundary.service().run(asyncio.Event())
    assert ("defer", (CLAIM, "provider_binding_mismatch")) in boundary.calls
    assert not any(stage == "page" for stage, _ in boundary.calls)


async def test_unexpected_provider_failure_leaves_other_items_available() -> None:
    calls = 0

    async def fail_once(stage: str) -> None:
        nonlocal calls
        if stage == "discover":
            calls += 1
            if calls == 1:
                raise RuntimeError("private")

    boundary = Boundary(claims=deque([WORK, WORK]), hook=fail_once)
    outcomes = await boundary.service().run(asyncio.Event())
    assert outcomes.count("unexpected_error") == 1
    assert outcomes.count("page_recorded") == 1
    assert boundary.count("unexpected_error") == boundary.count("page_recorded") == 1


def test_observation_metrics_have_only_fixed_labels() -> None:
    boundary = Boundary()
    boundary.metrics.ci_observation_item("repository-private-provider-error")
    assert boundary.count("other") == 1
    assert "repository-private" not in boundary.metrics.snapshot().content.decode()
