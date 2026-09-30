from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from types import TracebackType

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncTransaction

import ci_coordinator.persistence.readiness as readiness_module
from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditEventInput,
    AuditEventRecord,
    prepare_audit_event,
)
from ci_coordinator.persistence import (
    DatabaseReadiness,
    DatabaseReadinessProbe,
    PostgresUnitOfWork,
    check_database_readiness,
)
from ci_coordinator.persistence.connection import create_postgres_engine

from .conftest import ALEMBIC_CONFIG_PATH, RUNTIME_ROLE

pytestmark = pytest.mark.persistence


def test_append_after_final_head_observation_preserves_the_verified_prefix(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        probe = DatabaseReadinessProbe(engine, ALEMBIC_CONFIG_PATH)
        loader = readiness_module._load_head_row
        load_records = readiness_module.load_audit_records_after
        checkpoints: list[int] = []
        reads = 0

        async def observe_checkpoint(
            connection: AsyncConnection,
            sequence: int,
            *,
            through_sequence: int,
            limit: int,
        ) -> tuple[AuditEventRecord, ...]:
            checkpoints.append(sequence)
            return await load_records(
                connection, sequence, through_sequence=through_sequence, limit=limit
            )

        async def append(key: str) -> None:
            async with PostgresUnitOfWork(engine) as unit:
                result = await unit.audit_events.append(prepare_audit_event(_event_input(key)))
                assert isinstance(result, AuditAppendAppended)
                await unit.commit()

        async def append_between_observations(
            connection: AsyncConnection, **options: bool
        ) -> RowMapping | None:
            nonlocal reads
            row = await loader(connection, **options)
            reads += 1
            if reads == 2:
                await append("readiness-concurrent-append")
            return row

        try:
            await append("readiness-initial-prefix")
            monkeypatch.setattr(readiness_module, "_load_head_row", append_between_observations)
            # noinspection PyUnresolvedReferences
            monkeypatch.setattr(readiness_module, "load_audit_records_after", observe_checkpoint)
            async with asyncio.timeout(5):
                first = await probe.check()
                second = await probe.check()
            assert reads >= 2
            assert first.ready and first.verified_revision == 1
            assert second.ready and second.verified_revision == 2
            assert checkpoints == [0, 1]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mutation", "restoration"),
    (
        (
            "ALTER TABLE ci_coordinator.audit_events ALTER COLUMN created_at TYPE varchar(1)",
            "ALTER TABLE ci_coordinator.audit_events ALTER COLUMN created_at TYPE varchar(24)",
        ),
        (
            (
                "ALTER TABLE ci_coordinator.audit_events ADD CONSTRAINT stealth_event_type "
                "CHECK (octet_length(event_type) = 0)"
            ),
            "ALTER TABLE ci_coordinator.audit_events DROP CONSTRAINT stealth_event_type",
        ),
        (
            "CREATE INDEX stealth_raw_key_idx ON ci_coordinator.audit_events (idempotency_key)",
            "DROP INDEX ci_coordinator.stealth_raw_key_idx",
        ),
        (
            "ALTER TABLE ci_coordinator.audit_events ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE ci_coordinator.audit_events DISABLE ROW LEVEL SECURITY",
        ),
    ),
)
def test_readiness_rejects_write_affecting_schema_drift(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    mutation: str,
    restoration: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with migration_engine.begin() as connection:
                await connection.execute(text(mutation))
            result = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
            assert not result.ready
            assert result.reason == "database_capability_unavailable"
        finally:
            async with migration_engine.begin() as connection:
                await connection.execute(text(restoration))
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_readiness_rejects_unexpected_user_trigger(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text(
                        "CREATE FUNCTION ci_coordinator.passthrough_audit_event() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TRIGGER stealth_audit_event BEFORE INSERT ON "
                        "ci_coordinator.audit_events FOR EACH ROW EXECUTE FUNCTION "
                        "ci_coordinator.passthrough_audit_event()"
                    )
                )
            result = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
            assert not result.ready
            assert result.reason == "database_capability_unavailable"
        finally:
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text("DROP TRIGGER stealth_audit_event ON ci_coordinator.audit_events")
                )
                await connection.execute(
                    text("DROP FUNCTION ci_coordinator.passthrough_audit_event()")
                )
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_readiness_rejects_unlogged_audit_tables(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text("ALTER TABLE ci_coordinator.audit_ledger_head SET UNLOGGED")
                )
            result = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
            assert not result.ready
            assert result.reason == "database_capability_unavailable"
        finally:
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text("ALTER TABLE ci_coordinator.audit_ledger_head SET LOGGED")
                )
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_readiness_is_bounded_while_table_lock_is_held(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        async with migration_engine.connect() as blocker:
            transaction = await blocker.begin()
            await blocker.execute(
                text("LOCK TABLE ci_coordinator.audit_events IN ACCESS EXCLUSIVE MODE")
            )
            started = monotonic()
            result = await check_database_readiness(
                runtime_engine,
                ALEMBIC_CONFIG_PATH,
                timeout_ms=100,
            )
            elapsed = monotonic() - started
            await transaction.rollback()
        await runtime_engine.dispose()
        await migration_engine.dispose()
        assert not result.ready
        assert result.reason in {"readiness_timeout", "store_unavailable_or_invalid"}
        assert elapsed < 1

    asyncio.run(scenario())


def test_readiness_does_not_take_a_row_lock_that_blocks_audit_append(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with migration_engine.connect() as blocker:
                transaction = await blocker.begin()
                await blocker.execute(
                    text(
                        "SELECT revision FROM ci_coordinator.audit_ledger_head "
                        "WHERE head_id = 1 FOR UPDATE"
                    )
                )
                started = monotonic()
                result = await check_database_readiness(
                    runtime_engine,
                    ALEMBIC_CONFIG_PATH,
                    timeout_ms=1_000,
                )
                elapsed = monotonic() - started
                await transaction.rollback()
            assert result.ready
            assert elapsed < 1
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_readiness_rejects_parser_recursion_without_escaping(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
            result = await unit_of_work.audit_events.append(
                prepare_audit_event(_event_input("deep-json"))
            )
            assert isinstance(result, AuditAppendAppended)
            await unit_of_work.commit()
        deeply_nested = (b"[" * 2_000) + b"0" + (b"]" * 2_000)
        async with migration_engine.begin() as connection:
            await connection.execute(
                text("UPDATE ci_coordinator.audit_events SET payload_canonical_json = :payload"),
                {"payload": deeply_nested},
            )
        readiness = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
        await runtime_engine.dispose()
        await migration_engine.dispose()
        assert not readiness.ready
        assert readiness.reason == "store_unavailable_or_invalid"

    asyncio.run(scenario())


def test_readiness_rejects_constraint_valid_inconsistent_head(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        records = []
        for key in ("head-first", "head-second"):
            async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input(key))
                )
                assert isinstance(result, AuditAppendAppended)
                records.append(result.record)
                await unit_of_work.commit()
        async with migration_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE ci_coordinator.audit_ledger_head "
                    "SET revision = 1, last_sequence = 1, "
                    "last_event_hash = :event_hash WHERE head_id = 1"
                ),
                {"event_hash": bytes.fromhex(records[0].event_hash)},
            )
        readiness = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
        await runtime_engine.dispose()
        await migration_engine.dispose()
        assert not readiness.ready
        assert readiness.reason == "audit_head_invalid"

    asyncio.run(scenario())


def test_readiness_rejects_current_head_hash_mismatch(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        records = []
        for key in ("hash-first", "hash-second"):
            async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input(key))
                )
                assert isinstance(result, AuditAppendAppended)
                records.append(result.record)
                await unit_of_work.commit()
        async with migration_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE ci_coordinator.audit_ledger_head "
                    "SET last_event_hash = :event_hash WHERE head_id = 1"
                ),
                {"event_hash": bytes.fromhex(records[0].event_hash)},
            )
        readiness = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
        await runtime_engine.dispose()
        await migration_engine.dispose()
        assert not readiness.ready
        assert readiness.reason == "audit_head_invalid"

    asyncio.run(scenario())


def test_incremental_readiness_reuses_the_verified_immutable_prefix(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        probe = DatabaseReadinessProbe(runtime_engine, ALEMBIC_CONFIG_PATH)

        original_loader = readiness_module.load_audit_records_after
        observed_checkpoints: list[int] = []

        async def recording_loader(
            connection: AsyncConnection,
            sequence: int,
            *,
            through_sequence: int,
            limit: int,
        ) -> tuple[AuditEventRecord, ...]:
            observed_checkpoints.append(sequence)
            return await original_loader(
                connection,
                sequence,
                through_sequence=through_sequence,
                limit=limit,
            )

        try:
            assert (await probe.check()).ready is True
            # noinspection PyUnresolvedReferences
            monkeypatch.setattr(readiness_module, "load_audit_records_after", recording_loader)

            assert (await probe.check()).ready is True

            async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
                appended = await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input("incremental-readiness"))
                )
                assert isinstance(appended, AuditAppendAppended)
                await unit_of_work.commit()

            assert (await probe.check()).ready is True
            assert (await probe.check()).ready is True
            assert observed_checkpoints == [0]
        finally:
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_readiness_replay_progresses_in_bounded_batches(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        probe = DatabaseReadinessProbe(runtime_engine, ALEMBIC_CONFIG_PATH, audit_batch_size=1)
        records: list[AuditEventRecord] = []
        loads: list[tuple[int, int, int]] = []
        original_loader = readiness_module.load_audit_records_after

        async def recording_loader(
            connection: AsyncConnection,
            sequence: int,
            *,
            through_sequence: int,
            limit: int,
        ) -> tuple[AuditEventRecord, ...]:
            loaded = await original_loader(
                connection, sequence, through_sequence=through_sequence, limit=limit
            )
            loads.append((sequence, limit, len(loaded)))
            return loaded

        try:
            for key in ("batch-first", "batch-second"):
                async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
                    result = await unit_of_work.audit_events.append(
                        prepare_audit_event(_event_input(key))
                    )
                    assert isinstance(result, AuditAppendAppended)
                    records.append(result.record)
                    await unit_of_work.commit()
            monkeypatch.setattr(readiness_module, "load_audit_records_after", recording_loader)
            for _ in range(2):
                assert await check_database_readiness(
                    runtime_engine, ALEMBIC_CONFIG_PATH
                ) == DatabaseReadiness(True, "ready", 2)
            assert loads == [(0, 4096, 2), (0, 4096, 2)]
            loads.clear()
            first = await probe.check()
            assert probe._last_record == records[0]
            second = await probe.check()
            assert first.reason == "audit_verification_in_progress"
            assert not first.ready
            assert first.verified_revision == 1
            assert second.ready
            assert second == DatabaseReadiness(True, "ready", 2)
            assert probe._last_record == records[1]
            assert loads == [(0, 1, 1), (1, 1, 1)]
            assert await probe.check() == second
            assert loads == [(0, 1, 1), (1, 1, 1)]
        finally:
            try:
                await probe.drain()
            finally:
                await runtime_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "corruption",
    [
        "canonical",
        "gap",
        "predecessor",
        "payload_hash",
        "input_hash",
        "event_hash",
        "head_rollback",
        "head_hash",
        "maximum",
    ],
)
def test_incremental_prefix_rejects_independent_ledger_corruption(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        migration = create_postgres_engine(postgres_database_url)
        probe = DatabaseReadinessProbe(engine, ALEMBIC_CONFIG_PATH, audit_batch_size=1)
        records: list[AuditEventRecord] = []
        try:
            for index in range(3):
                async with PostgresUnitOfWork(engine) as unit:
                    appended = await unit.audit_events.append(
                        prepare_audit_event(_event_input(f"prefix-{index}"))
                    )
                    assert isinstance(appended, AuditAppendAppended)
                    records.append(appended.record)
                    await unit.commit()
            first = await probe.check()
            assert first.reason == "audit_verification_in_progress"
            assert first.verified_revision == 1
            assert probe._last_record == records[0]
            statements = {
                "canonical": (
                    "UPDATE ci_coordinator.audit_events SET payload_canonical_json = :bad_json "
                    "WHERE sequence = 2"
                ),
                "gap": "UPDATE ci_coordinator.audit_events SET sequence = 4 WHERE sequence = 2",
                "predecessor": (
                    "UPDATE ci_coordinator.audit_events SET previous_event_hash = :first_hash "
                    "WHERE sequence = 3"
                ),
                "payload_hash": (
                    "UPDATE ci_coordinator.audit_events SET payload_hash = :bad_hash "
                    "WHERE sequence = 2"
                ),
                "input_hash": (
                    "UPDATE ci_coordinator.audit_events SET input_hash = :bad_hash "
                    "WHERE sequence = 2"
                ),
                "event_hash": (
                    "WITH changed AS (UPDATE ci_coordinator.audit_events SET event_hash = "
                    ":bad_hash WHERE sequence = 3 RETURNING event_hash) "
                    "UPDATE ci_coordinator.audit_ledger_head "
                    "SET last_event_hash = (SELECT event_hash FROM changed)"
                ),
                "head_rollback": (
                    "UPDATE ci_coordinator.audit_ledger_head SET revision = 0, "
                    "last_sequence = NULL, last_event_hash = NULL"
                ),
                "head_hash": (
                    "UPDATE ci_coordinator.audit_ledger_head SET last_event_hash = :first_hash"
                ),
                "maximum": (
                    "UPDATE ci_coordinator.audit_ledger_head SET revision = 2, "
                    "last_sequence = 2, last_event_hash = :second_hash"
                ),
            }
            async with migration.begin() as connection:
                await connection.execute(
                    text(statements[corruption]),
                    {
                        "bad_json": b'{ "value":1}',
                        "bad_hash": bytes(32),
                        "first_hash": bytes.fromhex(records[0].event_hash),
                        "second_hash": bytes.fromhex(records[1].event_hash),
                    },
                )
            if corruption in {"predecessor", "event_hash", "head_hash"}:
                continuation = await probe.check()
                assert continuation == DatabaseReadiness(False, "audit_verification_in_progress", 2)
                assert probe._last_record == records[1]
            result = await probe.check()
            assert not result.ready and result.verified_revision is None
            assert result.reason in {
                "audit_chain_invalid",
                "audit_head_invalid",
                "store_unavailable_or_invalid",
            }
            if result.reason == "store_unavailable_or_invalid":
                assert probe._last_record == records[0]
            else:
                assert probe._last_record is None and not probe._verified
            await probe.drain()
        finally:
            try:
                await probe.drain()
            finally:
                try:
                    await engine.dispose()
                finally:
                    await migration.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["transaction", "connection"])
@pytest.mark.parametrize("cut", ["none", "waiter_cancel", "deadline", "stop"])
def test_real_connection_exit_cuts_preserve_prefix_and_return_checkout(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    cut: str,
) -> None:
    _exercise_connection_exit_cut(runtime_postgres_database_url, monkeypatch, boundary, cut)


def _exercise_connection_exit_cut(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    cut: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        entered, release = asyncio.Event(), asyncio.Event()
        observed: list[str] = []
        original_close = AsyncConnection.close
        original_exit = AsyncTransaction.__aexit__
        probe = DatabaseReadinessProbe(engine, ALEMBIC_CONFIG_PATH, timeout_ms=5_000)
        retained: list[AsyncConnection] = []

        def checkin(_connection: object, _record: object) -> None:
            observed.append("checkin")

        event.listen(engine.sync_engine, "checkin", checkin)

        async def close(connection: AsyncConnection) -> None:
            retained.append(connection)
            if boundary == "connection":
                entered.set()
                await release.wait()
            await original_close(connection)
            observed.append("closed")

        async def transaction_exit(
            transaction: AsyncTransaction,
            error_type: type[BaseException] | None,
            error: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            if boundary == "transaction":
                entered.set()
                await release.wait()
            await original_exit(transaction, error_type, error, traceback)

        try:
            monkeypatch.setattr(AsyncConnection, "close", close)
            monkeypatch.setattr(AsyncTransaction, "__aexit__", transaction_exit)
            waiter = asyncio.create_task(probe.check())
            async with asyncio.timeout(10):
                await entered.wait()
                worker = probe._inflight
                assert worker is not None
                assert not worker.done(), "readiness worker settled before connection close"
                assert not probe._verified and observed == []
                if cut == "waiter_cancel":
                    waiter.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await waiter
                elif cut == "deadline":
                    assert (await waiter).reason == "readiness_timeout"
                elif cut == "stop":
                    probe.stop()
                await asyncio.sleep(0)
                if boundary == "connection":
                    assert not worker.done() and observed == []
                    assert probe._connection_finalizer is not None
                    assert not probe._connection_finalizer.done()
                release.set()
                await asyncio.gather(worker, waiter, return_exceptions=True)
                assert retained and all(connection.closed for connection in retained)
                assert observed == ["checkin", "closed"]
                assert probe._verified is (cut in {"none", "waiter_cancel"})
                await probe.drain()
        finally:
            release.set()
            await probe.drain()
            if probe._connection_finalizer is not None:
                await probe._connection_finalizer
            await engine.dispose()

    asyncio.run(scenario())


def test_connection_exit_oracle_rejects_an_unjoined_finalizer(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def unjoined_connection(probe: DatabaseReadinessProbe) -> AsyncIterator[AsyncConnection]:
        connection = await probe._engine.connect()
        try:
            yield connection
        finally:
            probe._connection_finalizer = asyncio.create_task(connection.close())

    monkeypatch.setattr(DatabaseReadinessProbe, "_connect", unjoined_connection)
    with pytest.raises(AssertionError, match="readiness worker settled before connection close"):
        _exercise_connection_exit_cut(
            runtime_postgres_database_url, monkeypatch, "connection", "none"
        )


@pytest.mark.parametrize("failed_close", [False, True])
def test_real_query_failure_does_not_hide_failed_close(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    failed_close: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        original_close = AsyncConnection.close
        original_loader = readiness_module._load_head_row
        retained: list[AsyncConnection] = []
        probe = DatabaseReadinessProbe(engine, ALEMBIC_CONFIG_PATH)

        async def fail_query(connection: AsyncConnection, **_: bool) -> RowMapping | None:
            retained.append(connection)
            raise SQLAlchemyError("private query canary")

        async def close(connection: AsyncConnection) -> None:
            if failed_close:
                raise SQLAlchemyError("private close canary")
            await original_close(connection)

        monkeypatch.setattr(readiness_module, "_load_head_row", fail_query)
        monkeypatch.setattr(AsyncConnection, "close", close)
        try:
            first = await probe.check()
            assert first.reason == "store_unavailable_or_invalid"
            monkeypatch.setattr(readiness_module, "_load_head_row", original_loader)
            second = await probe.check()
            assert second.ready is (not failed_close)
            assert retained[0].closed is (not failed_close)
            if failed_close:
                assert second == first
                for _ in range(2):
                    with pytest.raises(RuntimeError, match="database readiness cleanup failed"):
                        await probe.drain()
            else:
                await probe.drain()
        finally:
            for connection in retained:
                await original_close(connection)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", ["schema", "acl"])
def test_warm_probe_rechecks_current_database_admission(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    drift: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        migration = create_postgres_engine(postgres_database_url)
        probe = DatabaseReadinessProbe(engine, ALEMBIC_CONFIG_PATH)
        mutation, restore = (
            (
                "ALTER TABLE ci_coordinator.audit_events ALTER COLUMN created_at TYPE varchar(1)",
                "ALTER TABLE ci_coordinator.audit_events ALTER COLUMN created_at TYPE varchar(24)",
            )
            if drift == "schema"
            else (
                f"REVOKE SELECT ON ci_coordinator.audit_events FROM {RUNTIME_ROLE}",
                f"GRANT SELECT ON ci_coordinator.audit_events TO {RUNTIME_ROLE}",
            )
        )
        try:
            assert (await probe.check()).ready
            assert probe._verified
            async with migration.begin() as connection:
                await connection.execute(text(mutation))
            result = await probe.check()
            assert not result.ready
            assert result.reason == "database_capability_unavailable"
            async with migration.begin() as connection:
                await connection.execute(text(restore))
            assert (await probe.check()).ready
        finally:
            async with migration.begin() as connection:
                await connection.execute(text(restore))
            await probe.drain()
            await engine.dispose()
            await migration.dispose()

    asyncio.run(scenario())


def _event_input(key: str) -> AuditEventInput:
    return AuditEventInput(
        idempotency_key=key,
        subject_type="dynamic-ci-plan",
        subject_id="plan",
        event_type="plan.persisted",
        created_at="2026-07-11T12:00:00.000Z",
        actor="test-suite",
        payload={"value": 1},
    )
