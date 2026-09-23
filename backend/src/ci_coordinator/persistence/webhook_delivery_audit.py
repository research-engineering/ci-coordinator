"""Pair-owned audit projection for durable webhook delivery claims."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Final, Protocol

from sqlalchemy.exc import SQLAlchemyError

from ci_coordinator.audit_replay import (
    AuditAppendResult,
    AuditEventInput,
    PreparedAuditEvent,
    prepare_audit_event,
)
from ci_coordinator.github_ingestion.ports import PreparedDeliveryClaim
from ci_coordinator.kernel import hash_object
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable

WEBHOOK_DELIVERY_CLAIMED_EVENT_TYPE: Final = "webhook-delivery.claimed/v1"
_WEBHOOK_DELIVERY_AUDIT_SCHEMA: Final = "ci-coordinator.audit.webhook-delivery/v1"


class WebhookDeliveryAuditAppender(Protocol):
    async def append_delivery_claim(
        self,
        event: PreparedAuditEvent,
    ) -> AuditAppendResult: ...


def prepare_webhook_delivery_claim_audit(
    claim: PreparedDeliveryClaim,
) -> PreparedAuditEvent:
    """Derive redacted audit input before the delivery row can be mutated."""

    if type(claim) is not PreparedDeliveryClaim:
        raise TypeError("webhook delivery audit requires an exact prepared claim")
    return prepare_existing_webhook_delivery_claim_audit(
        claim,
        delivery_id=claim.delivery_id,
    )


def prepare_existing_webhook_delivery_claim_audit(
    claim: PreparedDeliveryClaim,
    *,
    delivery_id: str,
) -> PreparedAuditEvent:
    """Bind an authenticated body replay to its existing durable delivery identity."""

    if type(claim) is not PreparedDeliveryClaim:
        raise TypeError("webhook delivery audit requires an exact prepared claim")
    if type(delivery_id) is not str or not delivery_id:
        raise ValueError("webhook delivery audit identity must be non-empty")
    delivery_id_hash = hash_object({"deliveryId": delivery_id})
    delivery_key_hash = hash_object(
        {
            "deliveryId": delivery_id,
            "bodySha256": claim.body_sha256,
        }
    )
    return prepare_audit_event(
        AuditEventInput(
            idempotency_key=f"webhook-delivery:{delivery_key_hash}:claimed",
            subject_type="webhook-delivery",
            subject_id=f"webhook-delivery:{delivery_id_hash}",
            event_type=WEBHOOK_DELIVERY_CLAIMED_EVENT_TYPE,
            created_at=_timestamp(claim.verified_at),
            actor="ci-coordinator:webhook-ingestion",
            payload={
                "schemaVersion": _WEBHOOK_DELIVERY_AUDIT_SCHEMA,
                "outcome": "claimed",
                "deliveryKeyHash": delivery_key_hash,
            },
        )
    )


class _PostgresWebhookDeliveryAuditRepository(_PostgresAuditEventRepository):
    """Reserve webhook-claim events for their state-owning repository."""

    async def append(self, event: PreparedAuditEvent) -> AuditAppendResult:
        if (
            type(event) is PreparedAuditEvent
            and event.event_type == WEBHOOK_DELIVERY_CLAIMED_EVENT_TYPE
        ):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation(
                "webhook delivery audit requires its owning state transition"
            )
        return await super().append(event)

    async def append_delivery_claim(
        self,
        event: PreparedAuditEvent,
    ) -> AuditAppendResult:
        self._ensure_active()
        if (
            type(event) is not PreparedAuditEvent
            or event.event_type != WEBHOOK_DELIVERY_CLAIMED_EVENT_TYPE
        ):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("webhook delivery audit event is invalid")
        try:
            return await self._append(event, scope=None)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("webhook delivery audit append failed") from error


def _timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("webhook delivery audit timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
