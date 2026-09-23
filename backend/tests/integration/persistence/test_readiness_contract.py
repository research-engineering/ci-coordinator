from __future__ import annotations

import asyncio
from time import monotonic

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

import ci_coordinator.persistence.readiness as readiness_module
from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditEventInput,
    AuditEventRecord,
    prepare_audit_event,
)
from ci_coordinator.persistence import (
    DatabaseReadinessProbe,
    PostgresUnitOfWork,
    check_database_readiness,
)
from ci_coordinator.persistence.connection import create_postgres_engine

from .conftest import ALEMBIC_CONFIG_PATH

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
        for key in ("batch-first", "batch-second"):
            async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input(key))
                )
                assert isinstance(result, AuditAppendAppended)
                await unit_of_work.commit()
        monkeypatch.setattr(readiness_module, "READINESS_AUDIT_BATCH_SIZE", 1)
        probe = DatabaseReadinessProbe(runtime_engine, ALEMBIC_CONFIG_PATH)
        try:
            first = await probe.check()
            second = await probe.check()
            assert first.reason == "audit_verification_in_progress"
            assert not first.ready
            assert second.ready
        finally:
            await runtime_engine.dispose()

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
