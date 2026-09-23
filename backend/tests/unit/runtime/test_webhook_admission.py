from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from anyio import BrokenWorkerProcess, CapacityLimiter, to_process

from ci_coordinator.api.http.webhook_ingress import (
    WebhookAdmissionUnavailable,
    WebhookIngressCommand,
    WebhookPreparationOffered,
)
from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryDuplicate,
    DeliveryIdempotencyResult,
    DeliveryStoreUnavailable,
    DurableWorkflowObservation,
    PingDelivery,
    PreparedWebhookIngestion,
    SeedIngestion,
    load_bundled_profile,
)
from ci_coordinator.github_ingestion.ports import PreparedDeliveryClaim
from ci_coordinator.github_ingestion.seeds import DynamicCiSeed
from ci_coordinator.kernel import FixedClock
from ci_coordinator.runtime.webhook_admission import (
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


def test_process_admission_uses_one_cancellable_worker_then_claims(
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
    assert limiter.total_tokens == 1


def test_process_admission_rejects_concurrent_work_without_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        command = webhook_command()
        profile = load_bundled_profile()
        prepared = _prepare_webhook_in_worker(
            command.headers, command.raw_body, SECRET, NOW, profile
        )
        assert isinstance(prepared, PreparedWebhookIngestion)
        started = asyncio.Event()
        release = asyncio.Event()

        async def run_sync(*_: object, **__: object) -> object:
            started.set()
            await release.wait()
            return prepared

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        first = asyncio.create_task(_admission(ClaimingDeliveries())(command))
        await started.wait()

        rejected = await _admission(ClaimingDeliveries())(command)

        assert isinstance(rejected, WebhookAdmissionUnavailable)
        release.set()
        assert isinstance(await first, SeedIngestion)

    asyncio.run(scenario())


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


def _admission(deliveries: ClaimingDeliveries) -> ProcessWebhookAdmission:
    return ProcessWebhookAdmission(
        secret=SECRET,
        profile=load_bundled_profile(),
        ingestion_store=deliveries,
        clock=FixedClock(NOW),
    )


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
