from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from sqlalchemy import func, select, text

from ci_coordinator.kernel import FixedClock
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.persistence.schema import audit_events, issued_plan_envelopes
from ci_coordinator.plan_issuance import IssuanceGuardRejected, SignedPlanSigner

from ._reconciliation_support import database_time
from ._runtime_ingress_issuance_support import (
    _config_draft,
    _initialize_selected_state,
    _save_selected,
    _selected_record_and_guard,
)
from ._temporal_lock_support import wait_for_blocked_operation, wait_for_database_deadline

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("boundary", ["scope-lock", "insert-statement", "audit-insert"])
def test_selected_issuance_rechecks_plan_expiry_after_database_wait(
    postgres_database_url: str, runtime_postgres_database_url: str, boundary: str
) -> None:
    async def scenario() -> None:
        draft = _config_draft("main")
        record, guard, registration = await _selected_record_and_guard(datetime.now(UTC), draft)
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        save = None
        try:
            await _initialize_selected_state(admin, runtime, draft, guard, registration)
            signer = SignedPlanSigner(
                key_id="expiry-witness",
                private_key_pem=Ed25519PrivateKey.generate().private_bytes(
                    Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
                ),
                ttl_seconds=5,
                clock=FixedClock(await database_time(admin)),
            )
            record = replace(
                record, envelope=signer.sign(record.envelope.payload, not_after=guard.not_after)
            )
            async with admin.connect() as blocker:
                transaction = await blocker.begin()
                try:
                    audit_count = await blocker.scalar(
                        select(func.count()).select_from(audit_events)
                    )
                    if boundary == "scope-lock":
                        await lock_repository_scope(blocker, guard.scope)
                    elif boundary == "insert-statement":
                        await blocker.execute(
                            text(
                                "LOCK TABLE ci_coordinator.issued_plan_envelopes "
                                "IN ACCESS EXCLUSIVE MODE"
                            )
                        )
                    else:
                        await blocker.execute(
                            text("LOCK TABLE ci_coordinator.audit_events IN SHARE MODE")
                        )
                    holder = await blocker.scalar(select(func.pg_backend_pid()))
                    assert type(holder) is int
                    save = asyncio.create_task(_save_selected(runtime, record, guard))
                    await wait_for_blocked_operation(admin, holder, save)
                    await wait_for_database_deadline(admin, record.envelope.expires_at)
                finally:
                    await transaction.rollback()
                assert save is not None
                async with asyncio.timeout(5):
                    result = await save
                assert result == IssuanceGuardRejected("plan_expired")
            async with admin.connect() as connection:
                assert await connection.scalar(select(issued_plan_envelopes.c.record_id)) is None
                assert (
                    await connection.scalar(select(func.count()).select_from(audit_events))
                    == audit_count
                )
        finally:
            if save is not None and not save.done():
                save.cancel()
                await asyncio.gather(save, return_exceptions=True)
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
