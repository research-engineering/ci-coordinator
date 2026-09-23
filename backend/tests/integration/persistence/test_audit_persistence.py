from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from alembic import command
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditAppendConflict,
    AuditAppendDuplicate,
    AuditEventInput,
    AuditEventRecord,
    prepare_audit_event,
)
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence import (
    PersistenceInvariantViolation,
    PostgresUnitOfWork,
    check_database_readiness,
)
from ci_coordinator.persistence import audit_codec as audit_codec_module
from ci_coordinator.persistence import audit_repository as audit_repository_module
from ci_coordinator.persistence.audit_codec import prepared_record_to_row
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.schema import audit_events

from ._audit_replay_support import load_test_audit_records
from .conftest import ALEMBIC_CONFIG_PATH, alembic_config

pytestmark = pytest.mark.persistence


def test_commit_round_trips_exact_domain_record(runtime_postgres_database_url: str) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        event_input = _event_input(
            "key-\u0000-e\u0301",
            payload={"nul": "\u0000", "composed": "\u00e9", "decomposed": "e\u0301"},
        )
        try:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(prepare_audit_event(event_input))
                assert isinstance(result, AuditAppendAppended)
                await unit_of_work.commit()

            async with PostgresUnitOfWork(engine) as unit_of_work:
                records = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.rollback()
            async with engine.connect() as connection:
                stored_payload = await connection.scalar(
                    select(audit_events.c.payload_canonical_json).where(
                        audit_events.c.sequence == 1
                    )
                )
        finally:
            await engine.dispose()

        assert records == (result.record,)
        assert type(records[0]) is AuditEventRecord
        assert stored_payload == canonical_json(event_input.payload)

    asyncio.run(scenario())


def test_append_writes_prepared_payload_bytes_without_recanonicalizing(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_audit_event(
        _event_input("prepared-codec", payload={"z": "e\u0301", "a": "\u00e9"})
    )

    def forbidden_canonicalizer(_value: object) -> bytes:
        raise AssertionError("write codec must not canonicalize an admitted payload")

    monkeypatch.setattr(
        audit_codec_module,
        "canonical_audit_payload_bytes",
        forbidden_canonicalizer,
    )

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        async with PostgresUnitOfWork(engine) as unit_of_work:
            result = await unit_of_work.audit_events.append(prepared)
            assert isinstance(result, AuditAppendAppended)
            await unit_of_work.commit()
        async with engine.connect() as connection:
            stored = await connection.scalar(
                select(audit_events.c.payload_canonical_json).where(audit_events.c.sequence == 1)
            )
        await engine.dispose()
        assert stored == prepared.payload_canonical_bytes

    asyncio.run(scenario())


def test_durable_repository_rejects_raw_input_and_requires_rollback(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        async with PostgresUnitOfWork(engine) as unit_of_work:
            with pytest.raises(PersistenceInvariantViolation, match="exact prepared event"):
                await unit_of_work.audit_events.append(
                    cast(Any, _event_input("raw-repository-input", payload={"value": 1}))
                )
            with pytest.raises(RuntimeError, match="not active"):
                await unit_of_work.commit()
        await engine.dispose()

    asyncio.run(scenario())


def test_scope_exit_without_commit_rolls_back_event_and_head(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input("rollback", payload={"value": 1}))
                )
                assert isinstance(result, AuditAppendAppended)

            async with PostgresUnitOfWork(engine) as unit_of_work:
                assert await load_test_audit_records(unit_of_work.audit_events) == ()
                await unit_of_work.rollback()
            async with engine.connect() as connection:
                head = (
                    await connection.execute(
                        text(
                            "SELECT revision, last_sequence, last_event_hash "
                            "FROM ci_coordinator.audit_ledger_head WHERE head_id = 1"
                        )
                    )
                ).one()
        finally:
            await engine.dispose()
        assert tuple(head) == (0, None, None)

    asyncio.run(scenario())


def test_duplicate_and_conflict_are_typed_and_write_nothing(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        original = _event_input("stable-key", payload={"value": 1})
        async with PostgresUnitOfWork(engine) as unit_of_work:
            first = await unit_of_work.audit_events.append(prepare_audit_event(original))
            await unit_of_work.commit()
        async with PostgresUnitOfWork(engine) as unit_of_work:
            duplicate = await unit_of_work.audit_events.append(prepare_audit_event(original))
            await unit_of_work.commit()
        async with PostgresUnitOfWork(engine) as unit_of_work:
            conflict = await unit_of_work.audit_events.append(
                prepare_audit_event(_event_input("stable-key", payload={"value": 2}))
            )
            await unit_of_work.commit()
        async with PostgresUnitOfWork(engine) as unit_of_work:
            records = await load_test_audit_records(unit_of_work.audit_events)
            await unit_of_work.rollback()
        await engine.dispose()

        assert isinstance(first, AuditAppendAppended)
        assert isinstance(duplicate, AuditAppendDuplicate)
        assert isinstance(conflict, AuditAppendConflict)
        assert records == (first.record,)

    asyncio.run(scenario())


def test_concurrent_genesis_appends_are_serialized(runtime_postgres_database_url: str) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)

        async def append_one(key: str) -> AuditAppendAppended:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input(key, payload={"value": 1}))
                )
                assert isinstance(result, AuditAppendAppended)
                await unit_of_work.commit()
                return result

        results = await asyncio.gather(append_one("concurrent-a"), append_one("concurrent-b"))
        async with PostgresUnitOfWork(engine) as unit_of_work:
            records = await load_test_audit_records(unit_of_work.audit_events)
            await unit_of_work.rollback()
        await engine.dispose()

        assert sorted(result.record.sequence for result in results) == [1, 2]
        assert tuple(record.sequence for record in records) == (1, 2)

    asyncio.run(scenario())


def test_append_waits_for_the_ledger_head_lock(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)

        async def append_one() -> AuditAppendAppended:
            async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input("blocked-append", payload={"value": 1}))
                )
                assert isinstance(result, AuditAppendAppended)
                await unit_of_work.commit()
                return result

        async with migration_engine.connect() as blocker:
            transaction = await blocker.begin()
            await blocker.execute(
                text(
                    "SELECT head_id FROM ci_coordinator.audit_ledger_head "
                    "WHERE head_id = 1 FOR UPDATE"
                )
            )
            task = asyncio.create_task(append_one())
            await _wait_until_head_lock_is_blocked(migration_engine, task)
            await transaction.commit()
            result = await asyncio.wait_for(task, timeout=2)
        await runtime_engine.dispose()
        await migration_engine.dispose()
        assert result.record.sequence == 1

    asyncio.run(scenario())


def test_concurrent_same_input_is_append_plus_duplicate(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        event_input = _event_input("concurrent-duplicate", payload={"value": 1})

        async def append_one() -> AuditAppendAppended | AuditAppendDuplicate:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(prepare_audit_event(event_input))
                assert isinstance(result, AuditAppendAppended | AuditAppendDuplicate)
                await unit_of_work.commit()
                return result

        results = await asyncio.gather(append_one(), append_one())
        await engine.dispose()
        assert sorted(result.kind for result in results) == ["appended", "duplicate"]

    asyncio.run(scenario())


def test_concurrent_same_key_different_input_is_append_plus_conflict(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)

        async def append_one(value: int) -> AuditAppendAppended | AuditAppendConflict:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(
                    prepare_audit_event(
                        _event_input("concurrent-conflict", payload={"value": value})
                    )
                )
                assert isinstance(result, AuditAppendAppended | AuditAppendConflict)
                await unit_of_work.commit()
                return result

        results = await asyncio.gather(append_one(1), append_one(2))
        async with PostgresUnitOfWork(engine) as unit_of_work:
            records = await load_test_audit_records(unit_of_work.audit_events)
            await unit_of_work.rollback()
        await engine.dispose()
        assert sorted(result.kind for result in results) == ["appended", "conflict"]
        assert len(records) == 1
        assert records[0].sequence == 1

    asyncio.run(scenario())


def test_failure_between_event_and_head_update_rolls_back_both(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE FUNCTION reject_audit_head_update() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'forced head failure'; END $$"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER reject_audit_head_update BEFORE UPDATE ON "
                    "ci_coordinator.audit_ledger_head "
                    "FOR EACH ROW EXECUTE FUNCTION reject_audit_head_update()"
                )
            )
        try:
            with pytest.raises(DatabaseCapabilityUnavailable, match="schema facts"):
                await PostgresUnitOfWork(engine).__aenter__()
            async with engine.connect() as connection:
                event_count = await connection.scalar(
                    text("SELECT count(*) FROM ci_coordinator.audit_events")
                )
                head_revision = await connection.scalar(
                    text("SELECT revision FROM ci_coordinator.audit_ledger_head WHERE head_id = 1")
                )
            assert event_count == 0
            assert head_revision == 0
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "DROP TRIGGER reject_audit_head_update ON ci_coordinator.audit_ledger_head"
                    )
                )
                await connection.execute(text("DROP FUNCTION reject_audit_head_update()"))
            await engine.dispose()

    asyncio.run(scenario())


def test_readiness_rejects_corrupt_chain_and_missing_head(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
                result = await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input("readiness-corruption", payload={"value": 1}))
                )
                assert isinstance(result, AuditAppendAppended)
                await unit_of_work.commit()
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE ci_coordinator.audit_events SET payload_canonical_json = :payload"
                    ),
                    {"payload": b'{"corrupt":true}'},
                )
            corrupt = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
            assert corrupt == type(corrupt)(False, "audit_chain_invalid")
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE ci_coordinator.audit_events SET payload_canonical_json = :payload, "
                        "idempotency_key_digest = :digest"
                    ),
                    {"payload": b'{"value":1}', "digest": b"x" * 32},
                )
            invalid_digest = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
            assert invalid_digest == type(invalid_digest)(False, "store_unavailable_or_invalid")
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text("UPDATE ci_coordinator.audit_events SET idempotency_key_digest = :digest"),
                    {"digest": audit_codec_module.idempotency_key_digest("readiness-corruption")},
                )
                await connection.execute(
                    text("DELETE FROM ci_coordinator.audit_ledger_head WHERE head_id = 1")
                )
            missing = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
            assert missing == type(missing)(False, "database_capability_unavailable")
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_readiness_rejects_wrong_shaped_constraint(
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
                        "ALTER TABLE ci_coordinator.audit_events DROP CONSTRAINT "
                        "uq_audit_events_idempotency_digest"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.audit_events ADD CONSTRAINT "
                        "uq_audit_events_idempotency_digest UNIQUE (input_hash)"
                    )
                )
            result = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
            assert result == type(result)(False, "database_capability_unavailable")
        finally:
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.audit_events DROP CONSTRAINT "
                        "uq_audit_events_idempotency_digest"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.audit_events ADD CONSTRAINT "
                        "uq_audit_events_idempotency_digest UNIQUE (idempotency_key_digest)"
                    )
                )
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_digest_collision_fails_as_integrity_violation(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collision = b"x" * 32
    monkeypatch.setattr(audit_codec_module, "idempotency_key_digest", lambda _value: collision)
    monkeypatch.setattr(audit_repository_module, "idempotency_key_digest", lambda _value: collision)

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        async with PostgresUnitOfWork(engine) as unit_of_work:
            await unit_of_work.audit_events.append(
                prepare_audit_event(_event_input("collision-a", payload={"value": 1}))
            )
            await unit_of_work.commit()
        with pytest.raises(PersistenceInvariantViolation, match="digest collision"):
            async with PostgresUnitOfWork(engine) as unit_of_work:
                await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input("collision-b", payload={"value": 1}))
                )
        await engine.dispose()

    asyncio.run(scenario())


def test_exact_round_trip_covers_empty_values_and_safe_integer_boundaries(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        payloads: tuple[JsonValue, ...] = (
            None,
            {},
            [],
            9_007_199_254_740_991,
            -9_007_199_254_740_991,
        )
        for index, payload in enumerate(payloads):
            async with PostgresUnitOfWork(engine) as unit_of_work:
                await unit_of_work.audit_events.append(
                    prepare_audit_event(_event_input(f"boundary-{index}", payload=payload))
                )
                await unit_of_work.commit()
        async with PostgresUnitOfWork(engine) as unit_of_work:
            records = await load_test_audit_records(unit_of_work.audit_events)
            await unit_of_work.rollback()
        await engine.dispose()
        assert tuple(record.payload for record in records) == payloads

    asyncio.run(scenario())


def test_database_constraints_reject_malformed_hash(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        prepared = prepare_audit_event(_event_input("constraint-source", payload={"value": 1}))
        async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
            appended = await unit_of_work.audit_events.append(prepared)
            assert isinstance(appended, AuditAppendAppended)
            await unit_of_work.rollback()

        malformed = prepared_record_to_row(appended.record, prepared)
        malformed["payload_hash"] = b"too-short"
        with pytest.raises(IntegrityError):
            async with migration_engine.begin() as connection:
                await connection.execute(insert(audit_events).values(malformed))
        await runtime_engine.dispose()
        await migration_engine.dispose()

    asyncio.run(scenario())


def test_readiness_requires_exact_migration_head(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def ready() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        result = await check_database_readiness(engine, ALEMBIC_CONFIG_PATH)
        await engine.dispose()
        assert result.ready
        assert result.reason == "ready"

    asyncio.run(ready())
    config = alembic_config(postgres_database_url)
    command.stamp(config, "base", purge=True)
    try:

        async def behind() -> None:
            engine = create_postgres_engine(runtime_postgres_database_url)
            result = await check_database_readiness(engine, ALEMBIC_CONFIG_PATH)
            await engine.dispose()
            assert not result.ready
            assert result.reason == "database_compatibility_invalid"

        asyncio.run(behind())
    finally:
        command.stamp(config, "head", purge=True)


def _event_input(
    key: str,
    *,
    payload: JsonValue,
) -> AuditEventInput:
    return AuditEventInput(
        idempotency_key=key,
        subject_type="dynamic-ci-plan",
        subject_id="plan-\u0000-e\u0301",
        event_type="plan.persisted",
        created_at="2026-07-11T12:00:00.000Z",
        actor="test-suite",
        payload=payload,
    )


async def _wait_until_head_lock_is_blocked(
    engine: AsyncEngine,
    task: asyncio.Task[object],
) -> None:
    async with asyncio.timeout(2):
        while True:
            if task.done():
                raise AssertionError("append completed without waiting for the ledger head lock")
            async with engine.connect() as observer:
                blocked = await observer.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' "
                        "AND query LIKE 'SELECT%audit_ledger_head%FOR UPDATE%'"
                        ")"
                    )
                )
            if blocked:
                return
            await asyncio.sleep(0.01)
