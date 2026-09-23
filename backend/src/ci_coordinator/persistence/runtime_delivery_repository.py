"""PostgreSQL webhook-delivery claim algebra within one admitted transaction."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditAppendConflict,
    AuditAppendDuplicate,
    PreparedAuditEvent,
)
from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryConflict,
    DeliveryDuplicate,
    DeliveryIdempotencyResult,
    DeliveryStoreUnavailable,
)
from ci_coordinator.github_ingestion.events import DurableWorkflowObservation
from ci_coordinator.github_ingestion.ports import PreparedDeliveryClaim
from ci_coordinator.persistence.ci_economics_codec import encode_workflow_observation
from ci_coordinator.persistence.ci_history_delivery_ingress import ensure_history_delivery_inbox
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.schema import ci_workflow_observations, webhook_deliveries
from ci_coordinator.persistence.webhook_delivery_audit import (
    WebhookDeliveryAuditAppender,
    prepare_existing_webhook_delivery_claim_audit,
    prepare_webhook_delivery_claim_audit,
)
from ci_coordinator.persistence.workflow_observation_lock import try_lock_workflow_observations


class _PostgresWebhookIngestionRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        audit_events: WebhookDeliveryAuditAppender,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit_events = audit_events
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def commit(
        self,
        key: PreparedDeliveryClaim,
        observation: DurableWorkflowObservation | None,
    ) -> DeliveryIdempotencyResult:
        self._ensure_active()
        if type(key) is not PreparedDeliveryClaim:
            self._mark_rollback_required()
            return DeliveryStoreUnavailable()
        prepared_claim = key
        if observation is not None and not prepared_claim.matches_observation(observation):
            self._mark_rollback_required()
            return DeliveryStoreUnavailable()
        durable_key = prepared_claim.key
        try:
            audit_event = prepare_webhook_delivery_claim_audit(prepared_claim)
            inserted = await self._connection.scalar(
                postgres_insert(webhook_deliveries)
                .values(
                    delivery_id=durable_key.delivery_id,
                    body_sha256=durable_key.body_sha256,
                )
                .on_conflict_do_nothing()
                .returning(webhook_deliveries.c.delivery_id)
            )
            if inserted == durable_key.delivery_id:
                if await self._claim_pair_is_complete(
                    audit_event,
                    observation,
                    durable_key.delivery_id,
                    delivery_was_inserted=True,
                ):
                    return DeliveryClaimed(durable_key)
                return DeliveryStoreUnavailable()
            row = (
                await self._connection.execute(
                    select(webhook_deliveries.c.body_sha256).where(
                        webhook_deliveries.c.delivery_id == durable_key.delivery_id
                    )
                )
            ).one_or_none()
            if row is not None:
                existing_body_sha256 = row[0]
                if type(existing_body_sha256) is not str:
                    self._mark_rollback_required()
                    return DeliveryStoreUnavailable()
                if existing_body_sha256 != durable_key.body_sha256:
                    return DeliveryConflict(durable_key.delivery_id, existing_body_sha256)
                if await self._claim_pair_is_complete(
                    audit_event,
                    observation,
                    durable_key.delivery_id,
                    delivery_was_inserted=False,
                ):
                    return DeliveryDuplicate(durable_key)
                return DeliveryStoreUnavailable()

            body_row = (
                await self._connection.execute(
                    select(webhook_deliveries.c.delivery_id).where(
                        webhook_deliveries.c.body_sha256 == durable_key.body_sha256
                    )
                )
            ).one_or_none()
            if body_row is None or type(body_row[0]) is not str:
                self._mark_rollback_required()
                return DeliveryStoreUnavailable()
            existing_delivery_id = body_row[0]
            existing_audit_event = prepare_existing_webhook_delivery_claim_audit(
                prepared_claim,
                delivery_id=existing_delivery_id,
            )
            if await self._claim_pair_is_complete(
                existing_audit_event,
                observation,
                existing_delivery_id,
                delivery_was_inserted=False,
            ):
                return DeliveryDuplicate(durable_key)
            return DeliveryStoreUnavailable()
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceError:
            self._mark_rollback_required()
            return DeliveryStoreUnavailable()
        except SQLAlchemyError:
            self._mark_rollback_required()
            return DeliveryStoreUnavailable()

    async def _claim_pair_is_complete(
        self,
        event: PreparedAuditEvent,
        observation: DurableWorkflowObservation | None,
        delivery_id: str,
        *,
        delivery_was_inserted: bool,
    ) -> bool:
        result = await self._audit_events.append_delivery_claim(event)
        if not isinstance(result, AuditAppendAppended | AuditAppendDuplicate):
            if isinstance(result, AuditAppendConflict):
                self._mark_rollback_required()
            else:
                self._mark_rollback_required()
            return False
        if await self._observation_is_complete(
            observation,
            delivery_id,
            delivery_was_inserted=delivery_was_inserted,
        ):
            return True
        self._mark_rollback_required()
        return False

    async def _observation_is_complete(
        self,
        observation: DurableWorkflowObservation | None,
        delivery_id: str,
        *,
        delivery_was_inserted: bool,
    ) -> bool:
        if observation is None and delivery_was_inserted:
            return True
        if not await try_lock_workflow_observations(self._connection, (delivery_id,)):
            return False
        existing = (
            (
                await self._connection.execute(
                    select(ci_workflow_observations).where(
                        ci_workflow_observations.c.delivery_id == delivery_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if observation is None:
            return existing is None
        attempted = encode_workflow_observation(observation, delivery_id=delivery_id)
        if existing is not None:
            return all(
                existing[name] == value for name, value in attempted.items()
            ) and await ensure_history_delivery_inbox(self._connection, existing)
        inserted = (
            (
                await self._connection.execute(
                    postgres_insert(ci_workflow_observations)
                    .values(**attempted)
                    .on_conflict_do_nothing()
                    .returning(ci_workflow_observations)
                )
            )
            .mappings()
            .one_or_none()
        )
        if inserted is not None:
            return await ensure_history_delivery_inbox(self._connection, inserted)
        raced = (
            (
                await self._connection.execute(
                    select(ci_workflow_observations).where(
                        ci_workflow_observations.c.delivery_id == delivery_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if raced is not None:
            return all(
                raced[name] == value for name, value in attempted.items()
            ) and await ensure_history_delivery_inbox(self._connection, raced)
        return False
