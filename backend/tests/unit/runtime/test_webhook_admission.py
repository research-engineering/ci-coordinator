from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

import pytest
from anyio import BrokenWorkerProcess, CapacityLimiter, to_process
from prometheus_support import prometheus_samples

from ci_coordinator.api.http.body_limits import MAX_CONCURRENT_BODY_REQUESTS
from ci_coordinator.api.http.webhook_ingress import (
    WebhookAdmissionUnavailable,
    WebhookIngressCommand,
    WebhookPreparationOffered,
)
from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryConflict,
    DeliveryDuplicate,
    DeliveryIdempotencyResult,
    DeliveryStoreUnavailable,
    DurableWorkflowObservation,
    IngestionRejection,
    PingDelivery,
    PreparedWebhookIngestion,
    SeedIngestion,
    load_bundled_profile,
)
from ci_coordinator.github_ingestion.ports import PreparedDeliveryClaim
from ci_coordinator.github_ingestion.seeds import DynamicCiSeed
from ci_coordinator.identity_admission import RejectedIdentity
from ci_coordinator.kernel import FixedClock
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.observability.runtime_metrics import WEBHOOK_ADMISSION_OUTCOMES
from ci_coordinator.runtime.webhook_admission import (
    WEBHOOK_VERIFIER_PERMITS,
    ProcessWebhookAdmission,
    _prepare_webhook_in_worker,
)

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
SECRET = "webhook-test-secret"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


@dataclass
class ClaimingDeliveries:
    claims: list[PreparedDeliveryClaim] = field(default_factory=list)

    async def commit(
        self,
        key: PreparedDeliveryClaim,
        observation: DurableWorkflowObservation | None,
    ) -> DeliveryIdempotencyResult:
        self.claims.append(key)
        assert observation is None
        return DeliveryClaimed(key.key)


def test_combined_worker_preserves_identity_and_prepares_no_durable_effect() -> None:
    command = webhook_command()

    prepared = _prepare_webhook_in_worker(
        command.headers,
        command.raw_body,
        SECRET,
        NOW,
        load_bundled_profile(),
    )

    assert isinstance(prepared, PreparedWebhookIngestion)
    assert prepared.provenance.delivery_id == "delivery-1"
    assert prepared.provenance.event_name == "push"


def test_signature_rejection_precedes_numeric_payload_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_: object, **__: object) -> object:
        raise AssertionError("unauthenticated bytes must not reach payload parsing")

    monkeypatch.setattr(
        "ci_coordinator.runtime.webhook_admission.prepare_trusted_webhook_ingestion", fail
    )
    command = replace(webhook_command(), raw_body=b'{"value":1e9999999999999999999}')
    result = _prepare_webhook_in_worker(
        command.headers, command.raw_body, SECRET, NOW, load_bundled_profile()
    )
    assert isinstance(result, RejectedIdentity)


@pytest.mark.parametrize("event_name", ("push", "ping"))
def test_combined_worker_is_picklable_by_the_installed_anyio_runtime(event_name: str) -> None:
    async def scenario() -> object:
        command = webhook_command(event_name)
        return await to_process.run_sync(
            _prepare_webhook_in_worker,
            command.headers,
            command.raw_body,
            SECRET,
            NOW,
            load_bundled_profile(),
            cancellable=True,
            limiter=CapacityLimiter(1),
        )

    prepared = asyncio.run(scenario())
    assert isinstance(prepared, PreparedWebhookIngestion)
    assert prepared.provenance.event_name == event_name
    if event_name == "ping":
        assert isinstance(prepared.outcome, PingDelivery)


def test_process_admission_uses_one_cancellable_pool_worker_then_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = webhook_command()
    profile = load_bundled_profile()
    prepared = _prepare_webhook_in_worker(command.headers, command.raw_body, SECRET, NOW, profile)
    assert isinstance(prepared, PreparedWebhookIngestion)
    calls: list[tuple[object, tuple[object, ...], bool, CapacityLimiter | None]] = []

    async def run_sync(
        function: object,
        *args: object,
        cancellable: bool = False,
        limiter: CapacityLimiter | None = None,
    ) -> object:
        calls.append((function, args, cancellable, limiter))
        return prepared

    monkeypatch.setattr(to_process, "run_sync", run_sync)
    deliveries = ClaimingDeliveries()
    admission = ProcessWebhookAdmission(
        secret=SECRET,
        profile=profile,
        ingestion_store=deliveries,
        clock=FixedClock(NOW),
    )

    result = asyncio.run(admission(command))

    assert isinstance(result, SeedIngestion)
    assert len(deliveries.claims) == 1
    assert len(calls) == 1
    function, args, cancellable, limiter = calls[0]
    assert function is _prepare_webhook_in_worker
    assert args == (command.headers, command.raw_body, SECRET, NOW, profile)
    assert cancellable is True
    assert limiter is not None
    assert limiter.total_tokens == WEBHOOK_VERIFIER_PERMITS == 4


def test_concurrent_deliveries_both_claim_while_the_first_is_held_in_its_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1: kills a retained process-global lock and a single-token worker limiter."""

    async def scenario() -> None:
        prepared = _prepared()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def run_sync(*_: object, **__: object) -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            return prepared

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        deliveries = ClaimingDeliveries()
        admission = _admission(deliveries)
        first = asyncio.create_task(admission(webhook_command()))
        await started.wait()

        second = await admission(webhook_command())

        assert isinstance(second, SeedIngestion)
        assert len(deliveries.claims) == 1
        release.set()
        assert isinstance(await first, SeedIngestion)
        assert len(deliveries.claims) == 2

    asyncio.run(scenario())


def test_delivery_beyond_the_pool_is_refused_without_worker_start_or_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2: kills waiting acquisition and a worker started before the permit."""

    async def scenario() -> None:
        prepared = _prepared()
        release = asyncio.Event()
        workers = 0

        both_started = asyncio.Event()

        async def run_sync(*_: object, **__: object) -> object:
            nonlocal workers
            workers += 1
            if workers == 2:
                both_started.set()
            await release.wait()
            return prepared

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        deliveries = ClaimingDeliveries()
        metrics = RuntimeMetrics()
        admission = _admission(deliveries, permits=2, metrics=metrics)
        held = [asyncio.create_task(admission(webhook_command())) for _ in range(2)]
        await both_started.wait()

        third = asyncio.create_task(admission(webhook_command()))
        await asyncio.sleep(0)

        assert third.done()
        assert isinstance(third.result(), WebhookAdmissionUnavailable)
        assert workers == 2
        assert deliveries.claims == []
        assert _outcomes(metrics)["permit_refused"] == 1
        release.set()
        assert all(isinstance(result, SeedIngestion) for result in await asyncio.gather(*held))

    asyncio.run(scenario())


def test_permit_is_held_through_the_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """P3: kills releasing the permit when verification returns, before the commit."""

    async def scenario() -> None:
        prepared = _prepared()
        workers = 0

        async def run_sync(*_: object, **__: object) -> object:
            nonlocal workers
            workers += 1
            return prepared

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        deliveries = BlockedDeliveries()
        admission = _admission(deliveries, permits=2)
        held = [asyncio.create_task(admission(webhook_command())) for _ in range(2)]
        await deliveries.both_entered.wait()

        refused = await admission(webhook_command())

        assert isinstance(refused, WebhookAdmissionUnavailable)
        assert workers == 2
        deliveries.release.set()
        assert all(isinstance(result, SeedIngestion) for result in await asyncio.gather(*held))
        assert isinstance(await admission(webhook_command()), SeedIngestion)

    asyncio.run(scenario())


def test_broken_worker_fails_only_its_own_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """P4: kills shared failure state and a pool torn down by one broken worker."""

    async def scenario() -> None:
        prepared = _prepared()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def run_sync(*_: object, **__: object) -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                return prepared
            if calls == 2:
                raise BrokenWorkerProcess
            return prepared

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        deliveries = ClaimingDeliveries()
        metrics = RuntimeMetrics()
        admission = _admission(deliveries, permits=2, metrics=metrics)
        sibling = asyncio.create_task(admission(webhook_command()))
        await started.wait()

        broken = await admission(webhook_command())

        assert isinstance(broken, WebhookAdmissionUnavailable)
        release.set()
        assert isinstance(await sibling, SeedIngestion)
        assert isinstance(await admission(webhook_command()), SeedIngestion)
        assert len(deliveries.claims) == 2
        assert _outcomes(metrics)["worker_unavailable"] == 1

    asyncio.run(scenario())


def test_cancelled_request_releases_its_permit(monkeypatch: pytest.MonkeyPatch) -> None:
    """P5 and D1: kills a permit leaked on cancellation or on the webhook deadline."""

    async def scenario() -> None:
        prepared = _prepared()
        never = asyncio.Event()
        workers = 0

        both_started = asyncio.Event()

        async def run_sync(*_: object, **__: object) -> object:
            nonlocal workers
            workers += 1
            if workers == 2:
                both_started.set()
            if workers <= 2:
                await never.wait()
            return prepared

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        deliveries = ClaimingDeliveries()
        admission = _admission(deliveries, permits=2)
        held = [asyncio.create_task(admission(webhook_command())) for _ in range(2)]
        await both_started.wait()
        held[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await held[0]

        admitted = await admission(webhook_command())

        assert isinstance(admitted, SeedIngestion)
        held[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await held[1]

    asyncio.run(scenario())


@pytest.mark.parametrize("permits", [0, 1, 9, True, 4.0])
def test_composition_rejects_a_pool_outside_two_to_body_admission_width(permits: object) -> None:
    """P6: kills an off-by-one bound and a silent fallback to one worker."""
    with pytest.raises(ValueError, match="verifier permits"):
        _admission(ClaimingDeliveries(), permits=cast(int, permits))


@pytest.mark.parametrize("permits", [2, MAX_CONCURRENT_BODY_REQUESTS])
def test_pool_bounds_admit_two_and_body_admission_width(permits: int) -> None:
    assert isinstance(_admission(ClaimingDeliveries(), permits=permits), ProcessWebhookAdmission)


@pytest.mark.parametrize("failure_type", [BrokenWorkerProcess, OSError])
def test_worker_failure_is_typed_unavailability(
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[Exception],
) -> None:
    async def run_sync(*_: object, **__: object) -> object:
        raise failure_type

    monkeypatch.setattr(to_process, "run_sync", run_sync)

    result = asyncio.run(_admission(ClaimingDeliveries())(webhook_command()))

    assert isinstance(result, WebhookAdmissionUnavailable)


@pytest.mark.parametrize(
    ("worker_result", "store_result", "expected"),
    [
        ("prepared", "claimed", "claimed"),
        ("prepared", "duplicate", "duplicate"),
        ("prepared", "conflict", "conflict"),
        ("prepared", "unavailable", "store_unavailable"),
        ("rejected_identity", None, "signature_rejected"),
        ("rejected_body", None, "body_rejected"),
    ],
)
def test_each_result_path_increments_exactly_one_closed_outcome(
    monkeypatch: pytest.MonkeyPatch,
    worker_result: str,
    store_result: str | None,
    expected: str,
) -> None:
    """M1: kills a missing or doubled admission outcome and an unbounded label."""
    command = webhook_command()
    worker_value: object = {
        "prepared": _prepared(),
        "rejected_identity": RejectedIdentity(
            reason_code="invalid_webhook_signature",
            message="redacted",
            verified_at=NOW,
            verifier_version="test",
        ),
        "rejected_body": IngestionRejection("invalid_webhook_body"),
    }[worker_result]

    async def run_sync(*_: object, **__: object) -> object:
        return worker_value

    class Store:
        async def commit(
            self, key: PreparedDeliveryClaim, observation: DurableWorkflowObservation | None
        ) -> DeliveryIdempotencyResult:
            del observation
            if store_result == "duplicate":
                return DeliveryDuplicate(key.key)
            if store_result == "conflict":
                return DeliveryConflict(key.key.delivery_id, "e" * 64)
            if store_result == "unavailable":
                return DeliveryStoreUnavailable()
            return DeliveryClaimed(key.key)

    monkeypatch.setattr(to_process, "run_sync", run_sync)
    metrics = RuntimeMetrics()
    admission = ProcessWebhookAdmission(
        secret=SECRET,
        profile=load_bundled_profile(),
        ingestion_store=Store(),
        clock=FixedClock(NOW),
        metrics=metrics,
        monotonic_clock=_SteppedMonotonicClock(),
    )

    asyncio.run(admission(command))

    outcomes = _outcomes(metrics)
    assert set(outcomes) == set(WEBHOOK_ADMISSION_OUTCOMES)
    assert outcomes == {outcome: int(outcome == expected) for outcome in outcomes}
    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_webhook_permit_hold_seconds_count", ())] == 1
    assert samples[("ci_coordinator_webhook_permit_hold_seconds_sum", ())] == 0.25


@dataclass
class BlockedDeliveries(ClaimingDeliveries):
    entered: list[PreparedDeliveryClaim] = field(default_factory=list)
    both_entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def commit(
        self,
        key: PreparedDeliveryClaim,
        observation: DurableWorkflowObservation | None,
    ) -> DeliveryIdempotencyResult:
        self.entered.append(key)
        if len(self.entered) == 2:
            self.both_entered.set()
        if len(self.entered) <= 2:
            await self.release.wait()
        return await super().commit(key, observation)


class _SteppedMonotonicClock:
    def __init__(self) -> None:
        self._values = iter((10.0, 10.25))

    def now(self) -> float:
        return next(self._values)


def _admission(
    deliveries: ClaimingDeliveries,
    *,
    permits: int = WEBHOOK_VERIFIER_PERMITS,
    metrics: RuntimeMetrics | None = None,
) -> ProcessWebhookAdmission:
    return ProcessWebhookAdmission(
        secret=SECRET,
        profile=load_bundled_profile(),
        ingestion_store=deliveries,
        clock=FixedClock(NOW),
        verifier_permits=permits,
        metrics=metrics,
    )


def _prepared() -> PreparedWebhookIngestion:
    command = webhook_command()
    prepared = _prepare_webhook_in_worker(
        command.headers, command.raw_body, SECRET, NOW, load_bundled_profile()
    )
    assert isinstance(prepared, PreparedWebhookIngestion)
    return prepared


def _outcomes(metrics: RuntimeMetrics) -> dict[str, float]:
    return {
        dict(labels)["outcome"]: value
        for (name, labels), value in prometheus_samples(metrics).items()
        if name == "ci_coordinator_webhook_admission_total"
    }


def webhook_command(event_name: str = "push") -> WebhookIngressCommand:
    body = (
        b'{"installation":{"id":1},"repository":{"id":2,"name":"ci",'
        b'"owner":{"login":"example"}},"ref":"refs/heads/main","before":"'
        + BASE_SHA.encode()
        + b'","after":"'
        + HEAD_SHA.encode()
        + b'","deleted":false}'
    )
    if event_name == "ping":
        body = b'{"zen":"bounded","hook_id":7,"hook":{"type":"App","id":7}}'
    digest = hmac.new(SECRET.encode(), body, sha256).hexdigest()
    return WebhookIngressCommand(
        headers=(
            ("x-github-delivery", ("delivery-1",)),
            ("x-github-event", (event_name,)),
            ("x-hub-signature-256", (f"sha256={digest}",)),
        ),
        raw_body=body,
    )


@pytest.mark.parametrize("storage", ["committed", "duplicate", "unavailable"])
@pytest.mark.parametrize("accepted", [True, False])
@pytest.mark.parametrize("event_name", ["push", "ping"])
def test_preparation_is_offered_only_after_successful_delivery_commit(
    monkeypatch: pytest.MonkeyPatch,
    storage: str,
    accepted: bool,
    event_name: str,
) -> None:
    command = webhook_command(event_name)
    profile = load_bundled_profile()
    prepared = _prepare_webhook_in_worker(command.headers, command.raw_body, SECRET, NOW, profile)
    assert isinstance(prepared, PreparedWebhookIngestion)
    order: list[str] = []

    async def run_sync(*_: object, **__: object) -> object:
        return prepared

    class Store:
        async def commit(
            self, key: PreparedDeliveryClaim, observation: DurableWorkflowObservation | None
        ) -> DeliveryIdempotencyResult:
            del observation
            order.append("commit")
            if storage == "duplicate":
                return DeliveryDuplicate(key.key)
            if storage == "unavailable":
                return DeliveryStoreUnavailable()
            return DeliveryClaimed(key.key)

    def offer(_: DynamicCiSeed) -> bool:
        assert order == ["commit"]
        order.append("offer")
        return accepted

    monkeypatch.setattr(to_process, "run_sync", run_sync)
    ingress = ProcessWebhookAdmission(
        secret=SECRET,
        profile=profile,
        ingestion_store=Store(),
        clock=FixedClock(NOW),
        offer_preparation=offer,
    )
    result = asyncio.run(ingress(command))
    offered = storage == "committed" and event_name == "push"
    assert order == (["commit", "offer"] if offered else ["commit"])
    assert isinstance(result, WebhookPreparationOffered) is (offered and accepted)
    if storage == "committed" and event_name == "ping":
        assert isinstance(result, PingDelivery)
    elif storage == "committed" and not accepted:
        assert isinstance(result, SeedIngestion)
