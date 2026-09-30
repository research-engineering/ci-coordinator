from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from time import perf_counter, process_time
from typing import cast

import pytest
from coverage import Coverage
from sqlalchemy import Select, event, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import QueuePool

import ci_coordinator.persistence.audit_repository as audit_repository
import ci_coordinator.persistence.readiness as readiness_module
from ci_coordinator.audit_replay import (
    MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    MAX_AUDIT_TEXT_UTF8_BYTES_V1,
    AuditAppendAppended,
    AuditEventInput,
    AuditEventRecord,
    prepare_audit_event,
)
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.persistence import DatabaseReadiness, DatabaseReadinessProbe, PostgresUnitOfWork
from ci_coordinator.persistence.audit_codec import row_to_record
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import audit_events

from .conftest import ALEMBIC_CONFIG_PATH

pytestmark = pytest.mark.persistence


def _payload(shape: str) -> tuple[JsonValue, bytes]:
    if shape == "small":
        return {"value": 1}, b'{"value":1}'
    if shape == "scalar":
        return "a" * 1048574, b'"' + b"a" * 1048574 + b'"'
    nested: JsonValue = 0
    for _ in range(63):
        nested = [nested]
    value: JsonValue = [nested, *([0] * 9934), "a" * 1028576]
    expected = b"[" + b"[" * 63 + b"0" + b"]" * 63 + b"," + b"0," * 9934
    expected += b'"' + b"a" * 1028576 + b'"]'
    assert len(expected) == MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1 == 1048576
    stack = [(value, 0)]
    nodes = deepest = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        deepest = max(deepest, depth)
        if isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    assert nodes == 10000 and deepest == 64
    return value, expected


def _malformed(shape: str) -> bytes:
    raw = {
        "wide": lambda: b"[" + b"0," * 524286 + b"0] ",
        "syntax": lambda: b"[" + b"0," * 524287 + b"?",
        "depth": lambda: b"[" * 65 + b'"' + b"a" * 1048444 + b'"' + b"]" * 65,
    }[shape]()
    assert len(raw) == MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1 == 1048576
    return raw


@pytest.mark.parametrize("shape", ["small", "scalar", "deep", "wide", "syntax", "depth"])
def test_readiness_b1_recovers_fixed_head_or_refuses_malformed_row_32(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    shape: str,
) -> None:
    async def scenario() -> None:
        setup_started = perf_counter()
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        probe = DatabaseReadinessProbe(
            engine, ALEMBIC_CONFIG_PATH, timeout_ms=5000, audit_batch_size=1
        )
        primary_error: BaseException | None = None
        coverage: Coverage | None = None
        coverage_stopped = False
        hooks_installed = False
        heartbeat: asyncio.TimerHandle | None = None
        hashes: list[str] = []
        raw_sequences: list[int] = []
        checkpoints: list[int] = []
        observations: list[dict[str, object]] = []
        suffix_queries = cursor_events = 0
        original_loader = readiness_module.load_audit_records_after
        original_decode = row_to_record

        def before_execute(_connection: object, statement: object, *_: object) -> None:
            nonlocal suffix_queries
            if isinstance(statement, Select) and statement.get_final_froms() == [audit_events]:
                assert getattr(statement._limit_clause, "value", None) == 1
                suffix_queries += 1

        def before_cursor_execute(*_: object) -> None:
            nonlocal cursor_events
            cursor_events += 1

        def decode(row: Mapping[str, object]) -> AuditEventRecord:
            raw_sequences.append(cast(int, row["sequence"]))
            return original_decode(row)

        async def load(
            connection: AsyncConnection,
            sequence: int,
            *,
            through_sequence: int,
            limit: int,
        ) -> tuple[AuditEventRecord, ...]:
            assert limit == 1 and through_sequence == 32
            assert sequence == len(checkpoints)
            checkpoints.append(sequence)
            loaded = await original_loader(
                connection, sequence, through_sequence=through_sequence, limit=limit
            )
            assert len(loaded) == 1 and loaded[0].sequence == sequence + 1
            assert loaded[0].event_hash == hashes[sequence]
            return loaded

        try:
            for sequence in range(1, 33):
                value, expected = _payload(shape)
                assert len(expected) <= MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1
                key = f"b1:{shape}:{sequence:06d}"
                prepared = prepare_audit_event(
                    AuditEventInput(
                        idempotency_key=key if shape == "small" else key.ljust(4096, "k"),
                        subject_type="dynamic-ci-plan",
                        subject_id="plan" if shape == "small" else "s" * 4096,
                        event_type="plan.persisted" if shape == "small" else "e" * 4096,
                        created_at="2026-07-11T12:00:00.000Z",
                        actor="test-suite" if shape == "small" else "a" * 4096,
                        payload=value,
                    )
                )
                assert prepared.payload_canonical_bytes == expected
                assert prepared.payload_hash == hashlib.sha256(expected).hexdigest()
                if shape != "small":
                    assert len(expected) == MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1
                    assert all(
                        len(item.encode()) == MAX_AUDIT_TEXT_UTF8_BYTES_V1 == 4096
                        for item in (
                            prepared.idempotency_key,
                            prepared.subject_id,
                            prepared.event_type,
                            prepared.actor,
                        )
                    )
                async with PostgresUnitOfWork(engine) as unit:
                    appended = await unit.audit_events.append(prepared)
                    assert isinstance(appended, AuditAppendAppended)
                    assert appended.record.sequence == sequence
                    assert appended.record.previous_event_hash == (hashes[-1] if hashes else None)
                    assert appended.record.payload_hash == hashlib.sha256(expected).hexdigest()
                    await unit.commit()
                    hashes.append(appended.record.event_hash)
                del prepared, appended, value, expected
            if shape in {"wide", "syntax", "depth"}:
                async with admin.begin() as connection:
                    changed = await connection.execute(
                        text(
                            "UPDATE ci_coordinator.audit_events SET payload_canonical_json = :raw "
                            "WHERE sequence = 32"
                        ),
                        {"raw": _malformed(shape)},
                    )
                    assert changed.rowcount == 1
            async with admin.connect() as connection:
                head = (
                    (
                        await connection.execute(
                            text(
                                "SELECT revision, last_sequence, last_event_hash, "
                                "(SELECT max(sequence) FROM ci_coordinator.audit_events) "
                                "AS maximum FROM ci_coordinator.audit_ledger_head WHERE head_id = 1"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert head["revision"] == head["last_sequence"] == head["maximum"] == 32
                assert bytes(head["last_event_hash"]).hex() == hashes[-1]
            setup_seconds = perf_counter() - setup_started
            event.listen(engine.sync_engine, "before_execute", before_execute)
            event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
            hooks_installed = True
            monkeypatch.setattr(readiness_module, "load_audit_records_after", load)
            monkeypatch.setattr(audit_repository, "row_to_record", decode)
            # Only this fixed block is untraced; covered semantic/lifetime tests stay intact.
            coverage = Coverage.current()
            if coverage is not None:
                coverage.stop()
                coverage_stopped = True
            loop = asyncio.get_running_loop()
            for sequence in range(1, 33):
                lag = 0.0
                next_tick = loop.time() + 0.01

                def tick() -> None:
                    nonlocal lag, next_tick, heartbeat
                    now = loop.time()
                    lag = max(lag, max(0.0, now - next_tick))
                    next_tick = now + 0.01
                    heartbeat = loop.call_at(next_tick, tick)

                heartbeat = loop.call_at(next_tick, tick)
                wall, cpu = perf_counter(), process_time()
                cursors_before = cursor_events
                result = await probe.check()
                await asyncio.sleep(0)
                elapsed, process_cpu = perf_counter() - wall, process_time() - cpu
                lag = max(lag, max(0.0, loop.time() - next_tick))
                heartbeat.cancel()
                heartbeat = None
                assert suffix_queries == sequence
                assert checkpoints == list(range(sequence))
                assert raw_sequences == list(range(1, sequence + 1))
                assert probe._inflight is not None and probe._inflight.done()
                assert (
                    probe._connection_finalizer is not None and probe._connection_finalizer.done()
                )
                assert cast(QueuePool, engine.pool).checkedout() == 0
                if sequence == 32 and shape in {"wide", "syntax", "depth"}:
                    assert result == DatabaseReadiness(False, "store_unavailable_or_invalid")
                    assert probe._verified and probe._last_record is not None
                    assert probe._last_record.sequence == 31
                    assert probe._last_record.event_hash == hashes[30]
                else:
                    assert result == DatabaseReadiness(
                        sequence == 32,
                        "ready" if sequence == 32 else "audit_verification_in_progress",
                        sequence,
                    )
                    assert probe._verified and probe._last_record is not None
                    assert probe._last_record.sequence == sequence
                    assert probe._last_record.event_hash == hashes[sequence - 1]
                observations.append(
                    {
                        "wave": sequence,
                        "reason": result.reason,
                        "verifiedRevision": result.verified_revision,
                        "wallSeconds": elapsed,
                        "processCpuSeconds": process_cpu,
                        "sampledLoopLagSeconds": lag,
                        "cursorEvents": cursor_events - cursors_before,
                        "settled": True,
                    }
                )
            if shape in {"small", "scalar", "deep"}:
                assert await probe.check() == DatabaseReadiness(True, "ready", 32)
                assert suffix_queries == len(raw_sequences) == len(checkpoints) == 32
            measurement = json.dumps(
                {
                    "shape": shape,
                    "setupAndSeedingSeconds": setup_seconds,
                    "fixtureStartupExcluded": True,
                    "timing": "untraced_with_delegating_hooks",
                    "heartbeatResolutionSeconds": 0.01,
                    "rss": "not_measured",
                    "nonClaims": ["server_cpu", "total_heap", "latency_bound", "live_capacity"],
                    "waves": observations,
                },
                sort_keys=True,
            )
            with capsys.disabled():
                print(measurement, flush=True)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
            cleanup_errors: list[BaseException] = []
            try:
                if coverage_stopped:
                    assert coverage is not None
                    coverage.start()
            except BaseException as error:
                cleanup_errors.append(error)
            if hooks_installed:
                for name, handler in (
                    ("before_execute", before_execute),
                    ("before_cursor_execute", before_cursor_execute),
                ):
                    try:
                        event.remove(engine.sync_engine, name, handler)
                    except BaseException as error:
                        cleanup_errors.append(error)
            for close in (probe.drain, engine.dispose, admin.dispose):
                try:
                    await close()
                except BaseException as error:
                    cleanup_errors.append(error)
            if primary_error is not None:
                for cleanup_failure in cleanup_errors:
                    primary_error.add_note(
                        f"readiness witness cleanup also failed: {type(cleanup_failure).__name__}"
                    )
            elif cleanup_errors:
                for cleanup_failure in cleanup_errors[1:]:
                    cleanup_errors[0].add_note(
                        f"additional cleanup failed: {type(cleanup_failure).__name__}"
                    )
                raise cleanup_errors[0]

    asyncio.run(scenario())
