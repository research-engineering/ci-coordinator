from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.github_ingestion.events import NormalizedWorkflowRunEvent
from ci_coordinator.github_ingestion.ports import DeliveryIdempotencyResult, prepare_delivery_claim
from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance
from ci_coordinator.persistence import PostgresWebhookIngestionUnitOfWork
from ci_coordinator.persistence.ci_economics_codec import encode_workflow_observation
from ci_coordinator.persistence.schema import ci_workflow_observations, webhook_deliveries


def workflow_observation(delivery_id: str, now: datetime) -> NormalizedWorkflowRunEvent:
    return NormalizedWorkflowRunEvent(
        provenance=WebhookProvenance(
            delivery_id=delivery_id,
            event_name="workflow_run",
            body_sha256=sha256(delivery_id.encode()).hexdigest(),
            verified_at=now,
            verifier_version="test-verifier/v1",
        ),
        repository=GitHubRepository(101, 202, "example-org", "ci-coordinator"),
        action="completed",
        workflow_run_id=7001,
        run_attempt=1,
        workflow_id=10,
        workflow_file=".github/workflows/ci.yml",
        workflow_name="Full Check",
        head_branch="master",
        head_sha="b" * 40,
        workflow_event="push",
        status="completed",
        conclusion="success",
        created_at=now - timedelta(minutes=2),
        run_started_at=now - timedelta(minutes=1),
        updated_at=now,
    )


async def ingest_workflow_observation(
    engine: AsyncEngine, observation: NormalizedWorkflowRunEvent
) -> DeliveryIdempotencyResult:
    async with PostgresWebhookIngestionUnitOfWork(engine) as transaction:
        result = await transaction.webhook_ingestion.commit(
            prepare_delivery_claim(observation.provenance), observation
        )
        await transaction.commit()
        return result


async def seed_workflow_observation(
    connection: AsyncConnection, observation: NormalizedWorkflowRunEvent, retain_until: datetime
) -> None:
    provenance = observation.provenance
    await connection.execute(
        insert(webhook_deliveries).values(
            delivery_id=provenance.delivery_id, body_sha256=provenance.body_sha256
        )
    )
    await connection.execute(
        insert(ci_workflow_observations).values(
            **encode_workflow_observation(observation, delivery_id=provenance.delivery_id),
            recorded_at=retain_until - timedelta(days=90),
            retain_until=retain_until,
        )
    )
