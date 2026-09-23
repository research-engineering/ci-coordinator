from datetime import timedelta

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence.schema import (
    ci_economics_budget_signals,
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshots,
)


async def shift_retention_epoch(admin: AsyncEngine, source_id: str, age: timedelta) -> None:
    columns = (
        (
            ci_workflow_attempt_collections,
            (
                "source_created_at",
                "deadline_at",
                "evidence_retain_until",
                "tombstone_retain_until",
                "next_attempt_at",
                "lease_acquired_at",
                "lease_expires_at",
                "completed_at",
                "expired_at",
                "created_at",
                "updated_at",
            ),
        ),
        (ci_workflow_attempt_snapshots, ("recorded_at", "retain_until")),
        (ci_job_measurement_reports, ("received_at", "retain_until")),
        (ci_economics_budget_signals, ("received_at", "retain_until")),
    )
    async with admin.begin() as connection:
        # Only the privileged fixture advances this epoch; runtime witnesses
        # below it must use normal trigger enforcement.
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        for table, names in columns:
            await connection.execute(
                update(table)
                .where(table.c.subject_id == source_id)
                .values({name: table.c[name] - age for name in names})
            )
