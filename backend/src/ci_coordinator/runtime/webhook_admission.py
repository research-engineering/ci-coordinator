"""Bounded no-queue verifier pool for GitHub webhook identity and payload admission."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from anyio import BrokenWorkerProcess, CapacityLimiter
from anyio import to_process as _to_process

from ci_coordinator.api.http.body_limits import MAX_CONCURRENT_BODY_REQUESTS
from ci_coordinator.api.http.webhook_ingress import (
    WebhookAdmissionUnavailable,
    WebhookHeaderLines,
    WebhookIngressCommand,
    WebhookIngressResult,
    WebhookPreparationOffered,
)
from ci_coordinator.github_ingestion import (
    DeliveryUnavailable,
    DuplicateDelivery,
    IngestionRejection,
    IngestionResult,
    PreparedWebhookIngestion,
    SeedIngestion,
    WebhookIngestionProfile,
    WebhookIngestionStore,
    complete_webhook_ingestion,
    prepare_trusted_webhook_ingestion,
)
from ci_coordinator.github_ingestion.seeds import DynamicCiSeed
from ci_coordinator.identity_admission import RejectedIdentity, verify_webhook_at
from ci_coordinator.kernel import Clock, MonotonicClock, NoQueueAdmission, SystemMonotonicClock
from ci_coordinator.observability import RuntimeMetrics, WebhookAdmissionOutcome

WEBHOOK_VERIFIER_PERMITS: Final = 4
MINIMUM_WEBHOOK_VERIFIER_PERMITS: Final = 2


def _prepare_webhook_in_worker(
    headers: WebhookHeaderLines,
    raw_body: bytes,
    secret: str,
    verified_at: datetime,
    profile: WebhookIngestionProfile,
) -> PreparedWebhookIngestion | IngestionRejection | RejectedIdentity:
    trusted_webhook = verify_webhook_at(
        dict(headers),
        raw_body,
        secret,
        verified_at,
    )
    if isinstance(trusted_webhook, RejectedIdentity):
        return trusted_webhook
    return prepare_trusted_webhook_ingestion(trusted_webhook, raw_body, profile)


class ProcessWebhookAdmission:
    """Admit at most N cancellable webhook verifications and commits per instance."""

    def __init__(
        self,
        *,
        secret: str,
        profile: WebhookIngestionProfile,
        ingestion_store: WebhookIngestionStore,
        clock: Clock,
        offer_preparation: Callable[[DynamicCiSeed], bool] | None = None,
        verifier_permits: int = WEBHOOK_VERIFIER_PERMITS,
        metrics: RuntimeMetrics | None = None,
        monotonic_clock: MonotonicClock | None = None,
    ) -> None:
        if type(secret) is not str or not secret:
            raise ValueError("webhook admission requires a non-empty secret")
        if (
            type(verifier_permits) is not int
            or not MINIMUM_WEBHOOK_VERIFIER_PERMITS
            <= verifier_permits
            <= MAX_CONCURRENT_BODY_REQUESTS
        ):
            raise ValueError("webhook verifier permits must fit the body admission width")
        self._secret = secret
        self._profile = profile
        self._ingestion_store = ingestion_store
        self._clock = clock
        # The permit is the only waiter-free admission point; the limiter never queues
        # because it has exactly as many tokens as there are permits.
        self._permits = NoQueueAdmission(verifier_permits)
        self._worker_capacity = CapacityLimiter(verifier_permits)
        self._offer_preparation = offer_preparation
        self._metrics = metrics
        self._monotonic_clock = (
            SystemMonotonicClock() if monotonic_clock is None else monotonic_clock
        )

    async def __call__(self, command: WebhookIngressCommand, /) -> WebhookIngressResult:
        permit = self._permits.try_acquire()
        if permit is None:
            self._observe("permit_refused")
            return WebhookAdmissionUnavailable()
        acquired_at = self._monotonic_clock.now()
        try:
            try:
                prepared = await _to_process.run_sync(
                    _prepare_webhook_in_worker,
                    command.headers,
                    command.raw_body,
                    self._secret,
                    self._clock.now(),
                    self._profile,
                    cancellable=True,
                    limiter=self._worker_capacity,
                )
            except (BrokenWorkerProcess, OSError):
                self._observe("worker_unavailable")
                return WebhookAdmissionUnavailable()
            if isinstance(prepared, RejectedIdentity):
                self._observe("signature_rejected")
                return prepared
            if isinstance(prepared, IngestionRejection):
                self._observe("body_rejected")
                return prepared
            if not isinstance(prepared, PreparedWebhookIngestion):
                raise RuntimeError("webhook worker returned an unsupported admission result")
            result = await complete_webhook_ingestion(prepared, self._ingestion_store)
            outcome = _committed_outcome(result)
            if outcome is not None:
                self._observe(outcome)
        finally:
            try:
                permit.release()
            finally:
                if self._metrics is not None:
                    self._metrics.webhook_permit_held(
                        max(0.0, float(self._monotonic_clock.now() - acquired_at))
                    )
        if (
            isinstance(result, SeedIngestion)
            and self._offer_preparation is not None
            and self._offer_preparation(result.seed)
        ):
            return WebhookPreparationOffered()
        return result

    def _observe(self, outcome: WebhookAdmissionOutcome) -> None:
        if self._metrics is not None:
            self._metrics.webhook_admission(outcome)


def _committed_outcome(result: IngestionResult) -> WebhookAdmissionOutcome | None:
    if isinstance(result, DeliveryUnavailable):
        return "store_unavailable"
    if isinstance(result, DuplicateDelivery):
        return "duplicate"
    if isinstance(result, IngestionRejection):
        if result.code == "delivery_id_conflict":
            return "conflict"
        if result.code == "delivery_idempotency_unavailable":
            return "store_unavailable"
        if result.code == "delivery_idempotency_contract_violation":
            # An unclassified defect maps to a redacted 500, not an admission outcome.
            return None
        return "body_rejected"
    return "claimed"
