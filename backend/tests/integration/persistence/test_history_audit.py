import asyncio

import pytest
from sqlalchemy import func, select

from ci_coordinator.audit_replay import AuditEventInput, prepare_audit_event
from ci_coordinator.ci_economics.history_commands import HISTORY_CONFIGURED_EVENT_TYPE
from ci_coordinator.ci_economics.history_gap_recovery import HISTORY_GAPS_REQUEUED_EVENT_TYPE
from ci_coordinator.persistence import PersistenceInvariantViolation, PostgresUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import audit_events, audit_ledger_head

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize(
    "event_type", [HISTORY_CONFIGURED_EVENT_TYPE, HISTORY_GAPS_REQUEUED_EVENT_TYPE]
)
def test_generic_audit_append_cannot_forge_a_history_receipt(
    runtime_postgres_database_url: str,
    event_type: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        prepared = prepare_audit_event(
            AuditEventInput(
                idempotency_key="ci-economics-history:101:202:forged",
                subject_type="policy-decision",
                subject_id="ci-history:101:202",
                event_type=event_type,
                created_at="2026-09-12T00:00:00.000Z",
                actor="operator-1",
                installation_id=101,
                repository_id=202,
                payload={"commandDigest": "a" * 64, "snapshot": {}},
            )
        )
        try:
            async with PostgresUnitOfWork(engine) as transaction:
                with pytest.raises(PersistenceInvariantViolation, match="owning state transition"):
                    await transaction.audit_events.append(prepared)
                with pytest.raises(RuntimeError, match="not active"):
                    await transaction.commit()
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 0
                assert await connection.scalar(select(audit_ledger_head.c.revision)) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
