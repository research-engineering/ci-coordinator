from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from ci_coordinator.persistence import check_database_readiness
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import audit_events

from .conftest import ALEMBIC_CONFIG_PATH

pytestmark = pytest.mark.persistence

_text_limit = 4_096
_payload_limit = 1_048_576
_profiled_columns = {
    "idempotency_key": ("ck_audit_events_idempotency_key_byte_limit", _text_limit),
    "subject_id": ("ck_audit_events_subject_id_byte_limit", _text_limit),
    "event_type": ("ck_audit_events_event_type_byte_limit", _text_limit),
    "actor": ("ck_audit_events_actor_byte_limit", _text_limit),
    "payload_canonical_json": (
        "ck_audit_events_payload_canonical_json_byte_limit",
        _payload_limit,
    ),
}


def test_direct_sql_accepts_all_profiled_columns_at_their_upper_bounds(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        async with engine.begin() as connection:
            await connection.execute(insert(audit_events).values(_boundary_row()))
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("column", tuple(_profiled_columns))
@pytest.mark.parametrize("size_kind", ("zero", "over"))
def test_direct_sql_rejects_each_profiled_column_outside_its_interval(
    postgres_database_url: str,
    column: str,
    size_kind: str,
) -> None:
    constraint, limit = _profiled_columns[column]

    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        async with engine.connect() as connection:
            transaction = await connection.begin()
            row = _boundary_row()
            row[column] = b"" if size_kind == "zero" else b"x" * (limit + 1)
            with pytest.raises(IntegrityError, match=constraint):
                await connection.execute(insert(audit_events).values(row))
            await transaction.rollback()
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("drift_sql", "restore_sql"),
    (
        (
            (
                (
                    "ALTER TABLE ci_coordinator.audit_events ALTER COLUMN "
                    "payload_canonical_json SET STORAGE PLAIN"
                ),
            ),
            (
                (
                    "ALTER TABLE ci_coordinator.audit_events ALTER COLUMN "
                    "payload_canonical_json SET STORAGE EXTENDED"
                ),
            ),
        ),
        (
            (
                (
                    "ALTER TABLE ci_coordinator.audit_events DROP CONSTRAINT "
                    "ck_audit_events_actor_byte_limit"
                ),
                (
                    "ALTER TABLE ci_coordinator.audit_events ADD CONSTRAINT "
                    "ck_audit_events_actor_byte_limit "
                    "CHECK (octet_length(actor) >= 1)"
                ),
            ),
            (
                (
                    "ALTER TABLE ci_coordinator.audit_events DROP CONSTRAINT "
                    "ck_audit_events_actor_byte_limit"
                ),
                (
                    "ALTER TABLE ci_coordinator.audit_events ADD CONSTRAINT "
                    "ck_audit_events_actor_byte_limit "
                    "CHECK (octet_length(actor) >= 1 AND octet_length(actor) <= 4096)"
                ),
            ),
        ),
    ),
)
def test_readiness_rejects_byte_constraint_or_storage_drift(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    drift_sql: tuple[str, ...],
    restore_sql: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with migration_engine.begin() as connection:
                for statement in drift_sql:
                    await connection.execute(text(statement))
            readiness = await check_database_readiness(runtime_engine, ALEMBIC_CONFIG_PATH)
            assert readiness == type(readiness)(False, "database_capability_unavailable")
        finally:
            async with migration_engine.begin() as connection:
                for statement in restore_sql:
                    await connection.execute(text(statement))
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def _boundary_row() -> dict[str, object]:
    row = _small_row()
    for column, (_, limit) in _profiled_columns.items():
        row[column] = b"x" * limit
    return row


def _small_row() -> dict[str, object]:
    digest = bytes(range(32))
    return {
        "sequence": 1,
        "idempotency_key": b"idempotency",
        "idempotency_key_digest": digest,
        "subject_type": "dynamic-ci-plan",
        "subject_id": b"subject",
        "event_type": b"event.checked",
        "created_at": "2026-07-11T00:00:00.000Z",
        "actor": b"test-suite",
        "payload_canonical_json": b"null",
        "schema_version": "ci-audit-event/v1",
        "audit_event_id": f"audit_{'1' * 32}",
        "previous_event_hash": None,
        "payload_hash": digest,
        "input_hash": bytes(reversed(digest)),
        "event_hash": b"e" * 32,
    }
