from __future__ import annotations

import asyncio

import pytest

from ci_coordinator.kernel import hash_object
from ci_coordinator.persistence import (
    PostgresIngressIssuanceUnitOfWork,
)
from ci_coordinator.persistence.connection import create_postgres_engine

from ._audit_replay_support import load_test_audit_records
from ._runtime_ingress_issuance_support import (
    _record,
)

pytestmark = pytest.mark.persistence


def test_issuance_replay_retains_the_exact_envelope_and_rejects_key_conflict(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        first = _record("run:7001:selected", "request-1")
        conflicting = _record("run:7001:selected", "request-2")
        try:
            async with PostgresIngressIssuanceUnitOfWork(engine) as unit_of_work:
                assert await unit_of_work.issuance.save(first) is None
                await unit_of_work.commit()

            async with PostgresIngressIssuanceUnitOfWork(engine) as unit_of_work:
                exact_replay = await unit_of_work.issuance.save(first)
                audit_events = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.rollback()
            assert exact_replay == first
            issued_events = tuple(
                event for event in audit_events if event.event_type == "dynamic-ci-plan.issued"
            )
            assert len(issued_events) == 1
            assert issued_events[0].subject_id == first.envelope.payload.plan_id
            assert isinstance(issued_events[0].payload, dict)
            assert issued_events[0].payload == {
                "algorithm": first.envelope.algorithm,
                "envelopeHash": hash_object(
                    {
                        "unsignedEnvelope": first.envelope.unsigned_mapping(),
                        "signature": first.envelope.signature,
                    }
                ),
                "fallback": True,
                "keyId": first.envelope.key_id,
                "planId": first.envelope.payload.plan_id,
                "recordId": first.record_id,
                "requestHash": first.request_hash,
                "schemaVersion": "ci-coordinator.audit.issued-plan/v1",
            }

            async with PostgresIngressIssuanceUnitOfWork(engine) as unit_of_work:
                conflict = await unit_of_work.issuance.save(conflicting)
                await unit_of_work.rollback()
            assert conflict == first
            assert conflict is not None and conflict.request_hash != conflicting.request_hash
        finally:
            await engine.dispose()

    asyncio.run(scenario())
