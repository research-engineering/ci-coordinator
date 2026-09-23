"""Bounded process isolation for GitHub webhook identity and payload admission."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Lock

from anyio import BrokenWorkerProcess, CapacityLimiter
from anyio import to_process as _to_process

from ci_coordinator.api.http.webhook_ingress import (
    WebhookAdmissionUnavailable,
    WebhookHeaderLines,
    WebhookIngressCommand,
    WebhookIngressResult,
    WebhookPreparationOffered,
)
from ci_coordinator.github_ingestion import (
    IngestionRejection,
    PreparedWebhookIngestion,
    SeedIngestion,
    WebhookIngestionProfile,
    WebhookIngestionStore,
    complete_webhook_ingestion,
    prepare_trusted_webhook_ingestion,
)
from ci_coordinator.github_ingestion.seeds import DynamicCiSeed
from ci_coordinator.identity_admission import RejectedIdentity, verify_webhook_at
from ci_coordinator.kernel import Clock

_PROCESS_ADMISSION_GATE = Lock()


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
    """Run at most one cancellable webhook admission per backend process."""

    def __init__(
        self,
        *,
        secret: str,
        profile: WebhookIngestionProfile,
        ingestion_store: WebhookIngestionStore,
        clock: Clock,
        offer_preparation: Callable[[DynamicCiSeed], bool] | None = None,
    ) -> None:
        if type(secret) is not str or not secret:
            raise ValueError("webhook admission requires a non-empty secret")
        self._secret = secret
        self._profile = profile
        self._ingestion_store = ingestion_store
        self._clock = clock
        self._worker_capacity = CapacityLimiter(1)
        self._offer_preparation = offer_preparation

    async def __call__(self, command: WebhookIngressCommand, /) -> WebhookIngressResult:
        if not _PROCESS_ADMISSION_GATE.acquire(blocking=False):
            return WebhookAdmissionUnavailable()
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
                return WebhookAdmissionUnavailable()
        finally:
            _PROCESS_ADMISSION_GATE.release()

        if isinstance(prepared, RejectedIdentity | IngestionRejection):
            return prepared
        if not isinstance(prepared, PreparedWebhookIngestion):
            raise RuntimeError("webhook worker returned an unsupported admission result")
        result = await complete_webhook_ingestion(prepared, self._ingestion_store)
        if (
            isinstance(result, SeedIngestion)
            and self._offer_preparation is not None
            and self._offer_preparation(result.seed)
        ):
            return WebhookPreparationOffered()
        return result
