from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from ci_coordinator.persistence import PostgresShadowReconciliationUnitOfWork
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.persistence.production_registration_adapter import (
    TransactionalProductionRegistrationStore,
)
from ci_coordinator.persistence.schema import reconciliation_subjects
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverApplied,
    ProductionCutoverRejected,
)
from ci_coordinator.reconciliation import (
    ReconciliationResult,
    ReconciliationSubject,
    ResultRecorded,
)
from ci_coordinator.reconciliation.findings import ReconciliationState

from ._production_cutover_support import CutoverDatabase, cutover_database
from ._reconciliation_support import POLICY, claim, contract, database_time, register
from ._temporal_lock_support import wait_for_blocked_operation

pytestmark = pytest.mark.persistence


def _subject(db: CutoverDatabase, run_id: int = 901) -> ReconciliationSubject:
    scope = db.fixture.draft.scope
    return ReconciliationSubject.create(
        installation_id=scope.installation_id,
        repository_id=scope.repository_id,
        event_name="pull_request",
        ref="refs/pull/1/merge",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=run_id,
        run_attempt=1,
    )


@pytest.mark.parametrize("terminal", ("success", "failure", "conflict"))
def test_legacy_registration_blocks_until_exact_terminal_result_releases_lease(
    postgres_database_url: str, runtime_postgres_database_url: str, terminal: ReconciliationState
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            subject = _subject(db)
            await register(db.runtime, subject)
            latched = await db.begin(await db.stage())
            assert await db.activate(latched) == ProductionCutoverRejected("drain_incomplete")
            acquired = await claim(db.runtime, "1" * 64)
            assert acquired is not None and acquired.subject == subject
            assert await db.activate(latched) == ProductionCutoverRejected("drain_incomplete")
            async with PostgresShadowReconciliationUnitOfWork(db.runtime) as transaction:
                result = await transaction.reconciliation.record_result(
                    acquired,
                    acquired.revision,
                    ReconciliationResult(subject.subject_id, terminal, ()),
                )
                assert isinstance(result, ResultRecorded)
                await transaction.commit()
            assert isinstance(await db.activate(latched), ProductionCutoverApplied)

    asyncio.run(scenario())


def test_explicit_full_ci_provenance_is_required_to_exclude_an_unfinished_registration(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            store = TransactionalProductionRegistrationStore(
                lambda: PostgresProductionCutoverUnitOfWork(db.runtime), POLICY
            )
            assert await store.register_full_ci(_subject(db), contract())
            async with db.admin.connect() as connection:
                row = (await connection.execute(select(reconciliation_subjects))).mappings().one()
                assert row["execution_origin"] == "full_ci"
                assert row["production_generation"] is None
                assert row["production_authority_id"] is None
            latched = await db.begin(await db.stage())
            assert isinstance(await db.activate(latched), ProductionCutoverApplied)

    asyncio.run(scenario())


@pytest.mark.parametrize("blocker", ["old-terminal-revision", "retained-lease"])
def test_a_terminal_result_cannot_hide_a_new_revision_or_a_retained_lease(
    postgres_database_url: str, runtime_postgres_database_url: str, blocker: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            subject = _subject(db)
            await register(db.runtime, subject)
            acquired = await claim(db.runtime, "1" * 64)
            assert acquired is not None and acquired.subject == subject
            async with PostgresShadowReconciliationUnitOfWork(db.runtime) as transaction:
                assert isinstance(
                    await transaction.reconciliation.record_result(
                        acquired,
                        acquired.revision,
                        ReconciliationResult(subject.subject_id, "success", ()),
                    ),
                    ResultRecorded,
                )
                await transaction.commit()
            latched = await db.begin(await db.stage())
            now = await database_time(db.runtime)
            change = update(reconciliation_subjects).where(
                reconciliation_subjects.c.subject_id == subject.subject_id
            )
            if blocker == "old-terminal-revision":
                change = change.values(revision=reconciliation_subjects.c.revision + 1)
            else:
                change = change.values(
                    lease_token="2" * 64,
                    lease_acquired_at=now,
                    lease_expires_at=now + timedelta(seconds=1),
                )
            async with db.admin.begin() as connection:
                await connection.execute(change)
            assert await db.activate(latched) == ProductionCutoverRejected("drain_incomplete")
            assert await db.store.inspect(db.fixture.draft.scope) == latched

    asyncio.run(scenario())


def test_drain_scans_beyond_its_first_page_without_discarding_pending_work(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            subjects = sorted(
                (_subject(db, run) for run in range(901, 906)), key=lambda item: item.subject_id
            )
            for subject in subjects[:-1]:
                await register(db.runtime, subject)
                acquired = await claim(db.runtime, "1" * 64)
                assert acquired is not None and acquired.subject == subject
                async with PostgresShadowReconciliationUnitOfWork(db.runtime) as transaction:
                    assert isinstance(
                        await transaction.reconciliation.record_result(
                            acquired,
                            acquired.revision,
                            ReconciliationResult(subject.subject_id, "success", ()),
                        ),
                        ResultRecorded,
                    )
                    await transaction.commit()
            await register(db.runtime, subjects[-1])
            latched = await db.begin(await db.stage())
            assert await db.activate(latched) == ProductionCutoverRejected("drain_incomplete")
            assert await db.store.inspect(db.fixture.draft.scope) == latched

    asyncio.run(scenario())


def test_a_locked_reconciliation_row_is_not_skipped_and_cancellation_preserves_latch(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            subject = _subject(db)
            await register(db.runtime, subject)
            latched = await db.begin(await db.stage())
            command, drain, current = await db.activation_inputs(latched)
            async with db.admin.begin() as blocker:
                await blocker.execute(
                    select(reconciliation_subjects)
                    .where(reconciliation_subjects.c.subject_id == subject.subject_id)
                    .with_for_update()
                )
                holder = await blocker.scalar(select(func.pg_backend_pid()))
                assert type(holder) is int
                task = asyncio.create_task(
                    db.store.activate(
                        command, grant=db.fixture.signed.grant, current=current, drain=drain
                    )
                )
                try:
                    await wait_for_blocked_operation(db.admin, holder, task)
                    assert not task.done()
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        async with asyncio.timeout(3):
                            await task
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert await db.store.inspect(db.fixture.draft.scope) == latched
            assert await db.store.resolve(command) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ("revision", "current_expiry", "drain_before_latch"))
def test_current_and_remote_drain_coordinates_are_independent_activation_requirements(
    postgres_database_url: str, runtime_postgres_database_url: str, mutation: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            latched = await db.begin(await db.stage())
            command, drain, current = await db.activation_inputs(latched)
            if mutation == "revision":
                command = replace(command, expected_revision=latched.revision + 1)
                expected = ProductionCutoverRejected("revision_changed")
            elif mutation == "current_expiry":
                current = db.fixture.activation(
                    latched, at=current.database_started_at - timedelta(seconds=30)
                )
                expected = ProductionCutoverRejected("current_evidence_invalid")
            else:
                from hashlib import sha256

                from production_admission_support import PRODUCTION_KEY_ID

                from ci_coordinator.production_admission.cutover_drain import admit_production_drain

                content = db.fixture.drain_bytes(latched, at=db.fixture.now - timedelta(seconds=1))
                signed = admit_production_drain(
                    content,
                    public_key_pem=db.fixture.signed.public_key_pem,
                    expected_key_id=PRODUCTION_KEY_ID,
                )
                assert signed is not None
                drain = signed
                command = replace(command, input_digest=sha256(content).hexdigest())
                expected = ProductionCutoverRejected("drain_incomplete")
            result = await db.store.activate(
                command, grant=db.fixture.signed.grant, current=current, drain=drain
            )
            assert result == expected
            assert await db.store.inspect(db.fixture.draft.scope) == latched
            assert await db.store.resolve(command) is None

    asyncio.run(scenario())
