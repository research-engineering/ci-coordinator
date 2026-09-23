from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Self

import pytest
from production_admission_support import make_production_admission_fixture
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence.production_cutover_adapter import (
    TransactionalProductionCutoverStore,
)
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.persistence.schema import (
    audit_events,
    audit_ledger_head,
    operator_overrides,
    production_admission_authorities,
    production_admission_scope_bindings,
    production_evidence_bundles,
    production_scope_states,
    production_staged_grants,
)
from ci_coordinator.production_admission.cutover_commands import ProductionCutoverRejected

from ._production_cutover_support import cutover_database
from ._reconciliation_support import database_time
from ._temporal_lock_support import wait_for_blocked_operation, wait_for_database_deadline

pytestmark = pytest.mark.persistence

_EXPIRY_SECONDS = 15
_BLOCK_OBSERVATION_SECONDS = 10
_EXPIRY_OBSERVATION_SECONDS = 16
_COMPLETION_SECONDS = 5
_AUDIT_LOCK_SECONDS = _BLOCK_OBSERVATION_SECONDS + _EXPIRY_OBSERVATION_SECONDS + 1


class _ExpiryObservationUnitOfWork(PostgresProductionCutoverUnitOfWork):
    def __init__(self, engine: AsyncEngine, waiter_ready: asyncio.Future[int]) -> None:
        super().__init__(engine)
        self._waiter_ready = waiter_ready

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        try:
            connection = self.cutover._connection
            await connection.scalar(
                select(func.set_config("lock_timeout", f"{_AUDIT_LOCK_SECONDS}s", True))
            )
            limits = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT name, setting::bigint FROM pg_settings WHERE name IN "
                            "('lock_timeout', 'statement_timeout', 'transaction_timeout')"
                        )
                    )
                )
                .tuples()
                .all()
            )
            assert limits["lock_timeout"] == 1000 * _AUDIT_LOCK_SECONDS
            assert 1000 * _EXPIRY_SECONDS < limits["lock_timeout"] < limits["statement_timeout"]
            assert limits["transaction_timeout"] > 1000 * (
                _BLOCK_OBSERVATION_SECONDS + _EXPIRY_OBSERVATION_SECONDS + _COMPLETION_SECONDS
            )
            waiter_pid = await connection.scalar(select(func.pg_backend_pid()))
            assert type(waiter_pid) is int and waiter_pid > 0
            self._waiter_ready.set_result(waiter_pid)
        except BaseException as error:
            await super().__aexit__(type(error), error, error.__traceback__)
            raise
        return self


@pytest.mark.parametrize("operation", ["stage", "activate"])
def test_cutover_expiry_during_audit_insert_rolls_back_every_effect(
    postgres_database_url: str, runtime_postgres_database_url: str, operation: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            before = None if operation == "stage" else await db.begin(await db.stage())
            waiter_ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
            store = TransactionalProductionCutoverStore(
                lambda: _ExpiryObservationUnitOfWork(db.runtime, waiter_ready)
            )
            tables = (
                audit_events,
                audit_ledger_head,
                operator_overrides,
                production_admission_authorities,
                production_admission_scope_bindings,
                production_evidence_bundles,
                production_scope_states,
                production_staged_grants,
            )
            async with db.admin.connect() as blocker:
                transaction = await blocker.begin()
                task = None
                try:
                    rows_before = {
                        table.name: tuple(await blocker.execute(select(table))) for table in tables
                    }
                    await blocker.execute(
                        text("LOCK TABLE ci_coordinator.audit_events IN SHARE MODE")
                    )
                    holder = await blocker.scalar(select(func.pg_backend_pid()))
                    assert type(holder) is int
                    expires_at = await database_time(db.admin) + timedelta(seconds=_EXPIRY_SECONDS)
                    expires_at = expires_at.replace(
                        microsecond=expires_at.microsecond // 1000 * 1000
                    )
                    if operation == "stage":
                        signed = make_production_admission_fixture(
                            db.fixture.staged.scope_grant.subject,
                            now=db.fixture.now,
                            expires_at=expires_at,
                            relation=db.fixture.staged.relation,
                            signing_key=db.fixture.signing_key,
                        )
                        fixture = replace(db.fixture, signed=signed)
                        command = db.stage_command(fixture=fixture)
                        task = asyncio.create_task(
                            store.stage(command, grant=signed.grant, evidence=fixture.staged)
                        )
                        expires_at = signed.grant.not_after
                    else:
                        assert before is not None
                        command, drain, current = await db.activation_inputs(
                            before, drain_expires_at=expires_at
                        )
                        task = asyncio.create_task(
                            store.activate(
                                command,
                                grant=db.fixture.signed.grant,
                                current=current,
                                drain=drain,
                            )
                        )
                        expires_at = drain.statement.expires_at
                    async with asyncio.timeout(_BLOCK_OBSERVATION_SECONDS):
                        await asyncio.wait(
                            (waiter_ready, task), return_when=asyncio.FIRST_COMPLETED
                        )
                        assert not task.done(), task.result()
                        await wait_for_blocked_operation(
                            db.admin,
                            holder,
                            task,
                            waiter_pid=waiter_ready.result(),
                            timeout_seconds=_BLOCK_OBSERVATION_SECONDS,
                        )
                        with pytest.raises(TimeoutError):
                            await wait_for_blocked_operation(
                                db.admin, holder, task, waiter_pid=holder, timeout_seconds=1
                            )
                    await wait_for_database_deadline(
                        db.admin, expires_at, timeout_seconds=_EXPIRY_OBSERVATION_SECONDS
                    )
                    await transaction.rollback()
                    async with asyncio.timeout(_COMPLETION_SECONDS):
                        assert await task == ProductionCutoverRejected("authority_expired")
                    assert await db.store.inspect(db.fixture.draft.scope) == before
                    assert await db.store.resolve(command) is None
                    async with db.admin.connect() as observer:
                        for table in tables:
                            assert (
                                tuple(await observer.execute(select(table)))
                                == rows_before[table.name]
                            ), table.name
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
                    if task is not None:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
