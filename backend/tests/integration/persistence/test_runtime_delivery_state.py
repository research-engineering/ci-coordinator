from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import insert, select

from ci_coordinator.audit_replay import AuditEventInput, prepare_audit_event
from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryConflict,
    DeliveryDuplicate,
    DeliveryStoreUnavailable,
)
from ci_coordinator.github_ingestion.events import NormalizedWorkflowJobEvent
from ci_coordinator.github_ingestion.ports import prepare_delivery_claim
from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance
from ci_coordinator.kernel import hash_object
from ci_coordinator.persistence import (
    PostgresUnitOfWork,
    PostgresWebhookIngestionUnitOfWork,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_delivery_inbox,
    ci_workflow_observations,
    webhook_deliveries,
)
from ci_coordinator.persistence.webhook_delivery_audit import (
    prepare_webhook_delivery_claim_audit,
)

from ._audit_replay_support import load_test_audit_records
from ._runtime_ingress_issuance_support import (
    _NOW,
    _delivery_claim,
)

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("labels", [("linux", "self-hosted"), ("self-hosted", "linux")])
def test_workflow_delivery_observation_and_audit_commit_as_one_replayable_pair(
    runtime_postgres_database_url: str,
    labels: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        provenance = WebhookProvenance(
            delivery_id="workflow-job-delivery",
            event_name="workflow_job",
            body_sha256="f" * 64,
            verified_at=_NOW,
            verifier_version="test-verifier/v1",
        )
        claim = prepare_delivery_claim(provenance)
        observation = NormalizedWorkflowJobEvent(
            provenance=provenance,
            repository=GitHubRepository(100, 200, "example-org", "ci-coordinator"),
            action="completed",
            workflow_run_id=7001,
            run_attempt=1,
            head_sha="b" * 40,
            workflow_job_id=8001,
            job_name="Backend tests",
            status="completed",
            conclusion="success",
            created_at=_NOW,
            started_at=_NOW + timedelta(seconds=2),
            completed_at=_NOW + timedelta(seconds=5),
            labels=labels,
            runner=None,
        )
        try:
            for expected_type in (DeliveryClaimed, DeliveryDuplicate):
                async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                    result = await unit_of_work.webhook_ingestion.commit(claim, observation)
                    assert isinstance(result, expected_type)
                    await unit_of_work.commit()

            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(ci_history_delivery_inbox.c.delivery_id)) is None
                )
                delivery = await connection.scalar(
                    select(webhook_deliveries.c.delivery_id).where(
                        webhook_deliveries.c.delivery_id == provenance.delivery_id
                    )
                )
                retained = (
                    (
                        await connection.execute(
                            select(ci_workflow_observations).where(
                                ci_workflow_observations.c.delivery_id == provenance.delivery_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                events = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.rollback()

            assert delivery == provenance.delivery_id
            assert retained["observation_kind"] == "workflow_job"
            assert retained["provider_job_id"] == observation.workflow_job_id
            assert (
                len(
                    tuple(
                        event
                        for event in events
                        if event.event_type == "webhook-delivery.claimed/v1"
                    )
                )
                == 1
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_delivery_claim_distinguishes_exact_replay_from_conflict_after_restart(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        key = _delivery_claim("delivery-1", "a" * 64, _NOW)
        replay = _delivery_claim("delivery-1", "a" * 64, _NOW + timedelta(minutes=1))
        body_replay = _delivery_claim("delivery-2", "a" * 64, _NOW + timedelta(minutes=2))
        conflicting_key = _delivery_claim("delivery-1", "b" * 64, _NOW)
        try:
            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                assert isinstance(
                    await unit_of_work.webhook_ingestion.commit(key, None), DeliveryClaimed
                )
                await unit_of_work.commit()

            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                assert isinstance(
                    await unit_of_work.webhook_ingestion.commit(replay, None), DeliveryDuplicate
                )
                audit_events = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.commit()
            delivery_events = tuple(
                event for event in audit_events if event.event_type == "webhook-delivery.claimed/v1"
            )
            assert len(delivery_events) == 1
            assert delivery_events[0].payload == {
                "deliveryKeyHash": hash_object(
                    {"deliveryId": "delivery-1", "bodySha256": "a" * 64}
                ),
                "outcome": "claimed",
                "schemaVersion": "ci-coordinator.audit.webhook-delivery/v1",
            }
            assert "delivery-1" not in delivery_events[0].subject_id

            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                duplicate_body = await unit_of_work.webhook_ingestion.commit(body_replay, None)
                body_replay_events = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.commit()
            assert isinstance(duplicate_body, DeliveryDuplicate)
            assert (
                len(
                    tuple(
                        event
                        for event in body_replay_events
                        if event.event_type == "webhook-delivery.claimed/v1"
                    )
                )
                == 1
            )

            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                conflict = await unit_of_work.webhook_ingestion.commit(conflicting_key, None)
                await unit_of_work.commit()
            assert isinstance(conflict, DeliveryConflict)
            assert conflict.delivery_id == key.key.delivery_id
            assert conflict.existing_body_sha256 == key.key.body_sha256
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_exact_replay_repairs_a_delivery_row_without_its_audit_counterpart(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        claim = _delivery_claim("delivery-repair", "c" * 64, _NOW)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    insert(webhook_deliveries).values(
                        delivery_id=claim.key.delivery_id,
                        body_sha256=claim.key.body_sha256,
                    )
                )

            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.webhook_ingestion.commit(claim, None)
                events = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.commit()

            assert isinstance(result, DeliveryDuplicate)
            repaired = tuple(
                event for event in events if event.event_type == "webhook-delivery.claimed/v1"
            )
            assert len(repaired) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_body_replay_repairs_the_original_delivery_audit_pair(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        original = _delivery_claim("delivery-body-original", "e" * 64, _NOW)
        replay = _delivery_claim(
            "delivery-body-replay",
            original.key.body_sha256,
            _NOW + timedelta(minutes=1),
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    insert(webhook_deliveries).values(
                        delivery_id=original.key.delivery_id,
                        body_sha256=original.key.body_sha256,
                    )
                )

            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.webhook_ingestion.commit(replay, None)
                events = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.commit()

            assert isinstance(result, DeliveryDuplicate)
            repaired = tuple(
                event for event in events if event.event_type == "webhook-delivery.claimed/v1"
            )
            assert len(repaired) == 1
            assert repaired[0].payload == {
                "deliveryKeyHash": hash_object(
                    {
                        "deliveryId": original.key.delivery_id,
                        "bodySha256": original.key.body_sha256,
                    }
                ),
                "outcome": "claimed",
                "schemaVersion": "ci-coordinator.audit.webhook-delivery/v1",
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_delivery_id_conflict_has_priority_over_cross_body_collision(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        first = _delivery_claim("delivery-cross-1", "1" * 64, _NOW)
        second = _delivery_claim("delivery-cross-2", "2" * 64, _NOW)
        cross_collision = _delivery_claim(
            first.key.delivery_id,
            second.key.body_sha256,
            _NOW + timedelta(minutes=1),
        )
        try:
            for claim in (first, second):
                async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                    assert isinstance(
                        await unit_of_work.webhook_ingestion.commit(claim, None),
                        DeliveryClaimed,
                    )
                    await unit_of_work.commit()

            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.webhook_ingestion.commit(cross_collision, None)
                await unit_of_work.commit()

            assert result == DeliveryConflict(
                delivery_id=first.key.delivery_id,
                existing_body_sha256=first.key.body_sha256,
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_audit_conflict_rolls_back_a_new_delivery_row(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        claim = _delivery_claim("delivery-conflict", "d" * 64, _NOW)
        expected = prepare_webhook_delivery_claim_audit(
            _delivery_claim("delivery-conflict", "d" * 64, _NOW)
        )
        conflicting = prepare_audit_event(
            AuditEventInput(
                idempotency_key=expected.idempotency_key,
                subject_type="webhook-delivery",
                subject_id="webhook-delivery:conflict-fixture",
                event_type="webhook-delivery.conflict-fixture",
                created_at="2026-07-15T12:00:00.000Z",
                actor="test",
                payload={"outcome": "conflict"},
            )
        )
        try:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                await unit_of_work.audit_events.append(conflicting)
                await unit_of_work.commit()

            async with PostgresWebhookIngestionUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.webhook_ingestion.commit(claim, None)
                assert isinstance(result, DeliveryStoreUnavailable)

            async with engine.connect() as connection:
                retained = await connection.scalar(
                    select(webhook_deliveries.c.delivery_id).where(
                        webhook_deliveries.c.delivery_id == claim.key.delivery_id
                    )
                )
            assert retained is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
