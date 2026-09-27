from __future__ import annotations

import argparse
import asyncio
import json
import platform
import resource
import sys
import time
import tracemalloc
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal
from unittest.mock import patch

from coverage import Coverage
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import event, select, text
from sqlalchemy.engine import ExceptionContext, ExecutionContext
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import AsyncAdaptedQueuePool

import ci_coordinator.persistence.audit_repository as repository
import ci_coordinator.persistence.readiness as readiness
from ci_coordinator.audit_replay import (
    AuditEventError,
    AuditEventRecord,
    verify_audit_chain_extension,
)
from ci_coordinator.audit_replay.chain import AuditChainVerificationResult
from ci_coordinator.persistence.audit_codec import row_to_record
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.schema import audit_events

CASES = ("small", "scalar", "combined", "wide", "syntax", "depth")
BATCHES = (1, 4, 16, 4096)
MODES = ("timing", "memory")
VALID_CASES = frozenset({"small", "scalar", "combined"})
type Case = Literal["small", "scalar", "combined", "wide", "syntax", "depth"]
type Mode = Literal["timing", "memory"]
type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Request(Evidence):
    database_url: str = Field(min_length=1, max_length=4096, repr=False)
    hashes: list[Digest] = Field(min_length=32, max_length=32)
    work_deadline: float = Field(allow_inf_nan=False)


class Wave(Evidence):
    kind: Literal["wave"] = "wave"
    index: int
    phase: Literal["recovery", "warm"]
    ready: bool
    reason: str
    revision: int | None
    settled_ready: bool | None
    settled_reason: str | None
    settled_revision: int | None
    waiter_ns: int
    settled_ns: int
    process_cpu_ns: int
    thread_cpu_ns: int
    sql_ns: int
    sql_calls: int
    loader_ns: int
    codec_ns: int
    verifier_ns: int
    loads: list[tuple[int, int, int]]
    sql_pages: list[tuple[int, int, int]]
    decoded: list[int]
    attempted: list[int]
    decoded_hashes: list[str]
    previous_hashes: list[str | None]
    returned: list[int]
    codec_failure: Literal["nodes", "syntax", "depth"] | None
    failure_observed: int | None
    json_error_offset: int | None
    verifier_calls: int
    checkins: int
    checked_out: int
    operation_settled: bool
    terminal_observation_ns: int | None = None
    io_unavailable: bool
    heartbeat_ticks: int
    heartbeat_missed: int
    heartbeat_max_lag_ns: int
    heartbeat_max_gap_ns: int
    rss_before: int
    rss_after: int
    traced_before: int | None
    traced_current: int | None
    traced_peak: int | None
    tracer_bytes: int | None


class Cell(Evidence):
    kind: Literal["cell"] = "cell"
    case: Case
    batch: Literal[1, 4, 16, 4096]
    mode: Mode
    status: Literal["NOT_MEASURED", "observed", "censored", "failed"] = "NOT_MEASURED"
    complete: bool = False
    detail: str = "campaign_not_started"
    waves: list[Wave] = Field(default_factory=list, max_length=33)
    worker_entry_ns: int | None = None
    process_wall_ns: int | None = None
    spawn_to_worker_entry_ns: int | None = None
    child_status: int | None = None
    child_failure: str | None = None
    child_signal: str | None = None
    child_quiescent: bool | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    preflight_ns: int | None = None
    role: Literal["ci_coordinator_runtime_test"] | None = None
    server_version: str | None = None
    python_version: str | None = None
    rss_unit: Literal["KiB", "bytes"] | None = None
    baseline_lag_ns: int | None = None
    baseline_ticks: int | None = None
    cleanup_ns: int | None = None
    cleanup_complete: bool = False
    coverage_active: bool | None = None
    timing_traced: bool | None = None
    partial_frame_bytes: int = 0
    connection_state: Literal["role_preflight_warmed_pool"] = "role_preflight_warmed_pool"


FRAME: TypeAdapter[Wave | Cell] = TypeAdapter(Annotated[Wave | Cell, Field(discriminator="kind")])


def emit(value: Wave | Cell) -> None:
    print(value.model_dump_json(), flush=True)


@dataclass
class Counters:
    sql_ns: int = 0
    sql_calls: int = 0
    loader_ns: int = 0
    codec_ns: int = 0
    verifier_ns: int = 0
    verifier_calls: int = 0
    checkins: int = 0
    loads: list[tuple[int, int, int]] = field(default_factory=list)
    sql_pages: list[tuple[int, int, int]] = field(default_factory=list)
    decoded: list[int] = field(default_factory=list)
    attempted: list[int] = field(default_factory=list)
    decoded_hashes: list[str] = field(default_factory=list)
    previous_hashes: list[str | None] = field(default_factory=list)
    returned: list[int] = field(default_factory=list)
    codec_failure: Literal["nodes", "syntax", "depth"] | None = None
    failure_observed: int | None = None
    json_error_offset: int | None = None
    error: PersistenceInvariantViolation | None = None
    io_unavailable: bool = False


class Heartbeat:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.period = 0.01
        self.target = self.loop.time() + self.period
        self.last = self.loop.time()
        self.ticks = self.missed = self.max_lag_ns = self.max_gap_ns = 0
        self.next_tick: asyncio.Future[None] | None = None
        self.handle = self.loop.call_at(self.target, self.tick)

    def tick(self) -> None:
        now = self.loop.time()
        lag = max(0.0, now - self.target)
        self.ticks += 1
        self.max_lag_ns = max(self.max_lag_ns, round(lag * 1e9))
        self.max_gap_ns = max(self.max_gap_ns, round((now - self.last) * 1e9))
        missed = int(lag / self.period)
        self.missed += missed
        self.target += (missed + 1) * self.period
        self.last = now
        if self.next_tick is not None and not self.next_tick.done():
            self.next_tick.set_result(None)
        self.handle = self.loop.call_at(self.target, self.tick)

    async def after_tick(self) -> None:
        self.next_tick = self.loop.create_future()
        await self.next_tick

    def close(self) -> None:
        self.handle.cancel()


def expected_failure(case: str) -> tuple[str, int | None]:
    return {"wide": ("nodes", 10001), "syntax": ("syntax", None), "depth": ("depth", 65)}[case]


def assert_wave(wave: Wave, cell: Cell, hashes: list[str]) -> None:
    """Admit reached semantics even when a later measurement boundary censors cost."""
    recovery_count = (32 + cell.batch - 1) // cell.batch
    assert 1 <= wave.index <= recovery_count + int(cell.case in VALID_CASES)
    assert wave.phase == ("recovery" if wave.index <= recovery_count else "warm")
    start = min((wave.index - 1) * cell.batch, 32)
    end = min(start + cell.batch, 32)
    public = (wave.ready, wave.reason, wave.revision)
    outcomes = [public]
    if wave.operation_settled:
        assert wave.settled_ready is not None and wave.settled_reason is not None
        settled = (wave.settled_ready, wave.settled_reason, wave.settled_revision)
        if wave.reason != "readiness_timeout":
            assert public == settled
        outcomes.append(settled)
    else:
        assert (wave.settled_ready, wave.settled_reason, wave.settled_revision) == (
            None,
            None,
            None,
        )
    for ready, reason, revision in outcomes:
        if reason == "readiness_timeout":
            assert ready is False and revision is None
        elif wave.io_unavailable:
            assert ready is False and revision is None
            assert reason in {
                "store_unavailable_or_invalid",
                "database_compatibility_invalid",
                "database_capability_unavailable",
            }
        elif cell.case not in VALID_CASES and end == 32:
            assert (ready, reason, revision) == (False, "store_unavailable_or_invalid", None)
        else:
            assert (ready, reason, revision) == (
                end == 32,
                "ready" if end == 32 else "audit_verification_in_progress",
                end,
            )
    completed_outcome = any(reason != "readiness_timeout" for _ready, reason, _revision in outcomes)
    if wave.phase == "warm":
        assert not wave.loads and not wave.sql_pages
        assert wave.decoded == wave.returned == wave.attempted == []
        assert wave.verifier_calls == 0
    elif completed_outcome and not wave.io_unavailable:
        assert wave.loads == wave.sql_pages == [(start, 32, cell.batch)]
    assert len(wave.attempted) <= end - start
    assert wave.attempted == list(range(start + 1, start + 1 + len(wave.attempted)))
    assert wave.decoded == list(range(start + 1, start + 1 + len(wave.decoded)))
    assert wave.decoded_hashes == hashes[start : start + len(wave.decoded_hashes)]
    assert len(wave.decoded_hashes) == len(wave.previous_hashes)
    assert len(wave.decoded) == len(wave.decoded_hashes)
    assert wave.previous_hashes == [
        None if sequence == 1 else hashes[sequence - 2] for sequence in wave.decoded
    ]
    if wave.codec_failure is not None:
        assert cell.case not in VALID_CASES
        assert end == 32
        assert (wave.codec_failure, wave.failure_observed) == expected_failure(cell.case)
        assert wave.decoded == list(range(start + 1, 32))
        assert wave.attempted == list(range(start + 1, 33))
        assert wave.returned == [] and wave.verifier_calls == 0
        assert wave.json_error_offset == (1048575 if cell.case == "syntax" else None)
    if not completed_outcome or wave.io_unavailable:
        return
    if wave.phase == "warm":
        return
    elif cell.case not in VALID_CASES and end == 32:
        assert (wave.codec_failure, wave.failure_observed) == expected_failure(cell.case)
    else:
        assert wave.codec_failure is None
        assert wave.decoded == wave.returned == list(range(start + 1, end + 1))
        assert wave.attempted == wave.decoded
        assert wave.verifier_calls == 1


def observe_terminal(
    wave: Wave,
    task: asyncio.Task[tuple[readiness.DatabaseReadiness, AuditEventRecord | None]],
    counters: Counters,
    checked_out: int,
) -> Wave:
    done = task.done()
    result = task.result()[0] if done else None
    return Wave.model_validate(
        wave.model_dump()
        | {
            "operation_settled": done,
            "settled_ready": None if result is None else result.ready,
            "settled_reason": None if result is None else result.reason,
            "settled_revision": None if result is None else result.verified_revision,
            "terminal_observation_ns": time.monotonic_ns(),
            "checked_out": checked_out,
            **{
                name: getattr(counters, name)
                for name in (
                    "sql_ns",
                    "sql_calls",
                    "loader_ns",
                    "codec_ns",
                    "verifier_ns",
                    "loads",
                    "sql_pages",
                    "decoded",
                    "attempted",
                    "decoded_hashes",
                    "previous_hashes",
                    "returned",
                    "codec_failure",
                    "failure_observed",
                    "json_error_offset",
                    "verifier_calls",
                    "checkins",
                    "io_unavailable",
                )
            },
        }
    )


async def measure(cell: Cell, request: Request) -> None:
    url = make_url(request.database_url)
    assert url.drivername == "postgresql+psycopg"
    assert url.username == "ci_coordinator_runtime_test"
    assert not url.query
    assert sys.platform in {"darwin", "linux"}
    assert readiness.READINESS_AUDIT_BATCH_SIZE == 4096
    assert not tracemalloc.is_tracing()
    assert Coverage.current() is None and sys.gettrace() is None
    cell.coverage_active = False
    cell.timing_traced = False
    cell.python_version = platform.python_version()
    cell.rss_unit = "bytes" if sys.platform == "darwin" else "KiB"
    engine = create_postgres_engine(request.database_url)
    pool = engine.sync_engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    probe = readiness.DatabaseReadinessProbe(engine, Path(__file__).parents[3] / "alembic.ini")
    counters = Counters()
    patches = ExitStack()
    retained_task: (
        asyncio.Task[tuple[readiness.DatabaseReadiness, AuditEventRecord | None]] | None
    ) = None
    pending_wave: Wave | None = None
    sql_started = 0
    real_loader = readiness.load_audit_records_after
    real_codec = row_to_record
    real_verify = verify_audit_chain_extension

    async def load(
        connection: AsyncConnection, sequence: int, *, through_sequence: int, limit: int
    ) -> tuple[AuditEventRecord, ...]:
        counters.loads.append((sequence, through_sequence, limit))
        start = time.monotonic_ns()
        try:
            records = await real_loader(
                connection, sequence, through_sequence=through_sequence, limit=limit
            )
            counters.returned.extend(record.sequence for record in records)
            return records
        except PersistenceInvariantViolation as error:
            assert error is counters.error
            counters.error = None
            raise
        finally:
            counters.loader_ns += time.monotonic_ns() - start

    def decode(row: Mapping[str, object]) -> AuditEventRecord:
        start = time.monotonic_ns()
        sequence = row["sequence"]
        assert type(sequence) is int
        counters.attempted.append(sequence)
        try:
            record = real_codec(row)
            counters.decoded.append(record.sequence)
            counters.decoded_hashes.append(record.event_hash)
            counters.previous_hashes.append(record.previous_event_hash)
            return record
        except PersistenceInvariantViolation as error:
            counters.error = error
            cause = error.__cause__
            assert row["sequence"] == 32
            raw = row["payload_canonical_json"]
            assert isinstance(raw, bytes | memoryview) and len(raw) == 1048576
            if isinstance(cause, json.JSONDecodeError):
                counters.codec_failure = "syntax"
                counters.json_error_offset = cause.pos
            else:
                assert isinstance(cause, AuditEventError)
                failure = cause.resource_failure
                assert failure is not None
                assert (failure.code, failure.limit) in {
                    ("json_max_nodes_exceeded", 10000),
                    ("json_max_depth_exceeded", 64),
                }
                counters.codec_failure = (
                    "nodes" if failure.code == "json_max_nodes_exceeded" else "depth"
                )
                counters.failure_observed = failure.observed
            raise
        finally:
            counters.codec_ns += time.monotonic_ns() - start

    def verify(
        previous: AuditEventRecord | None, records: tuple[AuditEventRecord, ...]
    ) -> AuditChainVerificationResult:
        counters.verifier_calls += 1
        start = time.monotonic_ns()
        try:
            return real_verify(previous, records)
        finally:
            counters.verifier_ns += time.monotonic_ns() - start

    def before_sql(
        _connection: object,
        _cursor: object,
        _statement: object,
        _parameters: object,
        context: ExecutionContext,
        _many: object,
    ) -> None:
        nonlocal sql_started
        sql_started = time.monotonic_ns()
        counters.sql_calls += 1
        if counters.loads and context.compiled is not None:
            start, through, limit = counters.loads[-1]
            expected = (
                select(audit_events)
                .where(audit_events.c.sequence > start, audit_events.c.sequence <= through)
                .order_by(audit_events.c.sequence)
                .limit(limit)
            )
            actual = context.compiled.statement
            # Cached SQL keeps its original statement; execution carries the fresh binds.
            if actual is not None and actual.compare(expected, compare_values=False):
                assert _parameters == expected.compile().params
                counters.sql_pages.append((start, through, limit))

    def after_sql(*_args: object) -> None:
        counters.sql_ns += time.monotonic_ns() - sql_started

    def checkin(*_args: object) -> None:
        counters.checkins += 1

    def sql_error(context: ExceptionContext) -> None:
        if isinstance(context.sqlalchemy_exception, OperationalError):
            counters.io_unavailable = True

    try:
        start = time.monotonic_ns()
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT current_user, current_setting('server_version')")
                )
            ).one()
            assert row[0] == "ci_coordinator_runtime_test"
            cell.role = "ci_coordinator_runtime_test"
            cell.server_version = str(row[1])
        cell.preflight_ns = time.monotonic_ns() - start
        assert pool.checkedout() == 0
        baseline = Heartbeat()
        try:
            await baseline.after_tick()
            await baseline.after_tick()
            cell.baseline_lag_ns = baseline.max_lag_ns
            cell.baseline_ticks = baseline.ticks
        finally:
            baseline.close()
        patches.enter_context(patch.object(readiness, "READINESS_AUDIT_BATCH_SIZE", cell.batch))
        patches.enter_context(patch.object(readiness, "load_audit_records_after", load))
        patches.enter_context(patch.object(repository, "row_to_record", decode))
        patches.enter_context(patch.object(readiness, "verify_audit_chain_extension", verify))
        event.listen(engine.sync_engine, "before_cursor_execute", before_sql)
        event.listen(engine.sync_engine, "after_cursor_execute", after_sql)
        event.listen(pool, "checkin", checkin)
        event.listen(engine.sync_engine, "handle_error", sql_error)
        if cell.mode == "memory":
            tracemalloc.start()
            cell.timing_traced = True
        count = (32 + cell.batch - 1) // cell.batch
        for index in range(1, count + 1 + int(cell.case in VALID_CASES)):
            if request.work_deadline - time.monotonic() < 5:
                cell.status, cell.detail = "censored", "campaign_work_deadline"
                return
            counters = Counters()
            heartbeat = Heartbeat()
            traced_before = None
            if cell.mode == "memory":
                traced_before = tracemalloc.get_traced_memory()[0]
                tracemalloc.reset_peak()
            rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            start = time.monotonic_ns()
            cpu, thread = time.process_time_ns(), time.thread_time_ns()
            try:
                result = await probe.check()
                waiter_ns = time.monotonic_ns() - start
                task = probe._inflight
                assert task is not None
                retained_task = task
                done, _pending = await asyncio.wait({task}, timeout=5)
                settled_ns = time.monotonic_ns() - start
                cpu_ns = time.process_time_ns() - cpu
                thread_ns = time.thread_time_ns() - thread
                traced = tracemalloc.get_traced_memory() if cell.mode == "memory" else None
                rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                await heartbeat.after_tick()
                wave = Wave(
                    index=index,
                    phase="warm" if index > count else "recovery",
                    ready=result.ready,
                    reason=result.reason,
                    revision=result.verified_revision,
                    settled_ready=None,
                    settled_reason=None,
                    settled_revision=None,
                    waiter_ns=waiter_ns,
                    settled_ns=settled_ns,
                    process_cpu_ns=cpu_ns,
                    thread_cpu_ns=thread_ns,
                    sql_ns=counters.sql_ns,
                    sql_calls=counters.sql_calls,
                    loader_ns=counters.loader_ns,
                    codec_ns=counters.codec_ns,
                    verifier_ns=counters.verifier_ns,
                    loads=counters.loads,
                    sql_pages=counters.sql_pages,
                    decoded=counters.decoded,
                    attempted=counters.attempted,
                    decoded_hashes=counters.decoded_hashes,
                    previous_hashes=counters.previous_hashes,
                    returned=counters.returned,
                    codec_failure=counters.codec_failure,
                    failure_observed=counters.failure_observed,
                    json_error_offset=counters.json_error_offset,
                    verifier_calls=counters.verifier_calls,
                    checkins=counters.checkins,
                    checked_out=pool.checkedout(),
                    operation_settled=bool(done),
                    io_unavailable=counters.io_unavailable,
                    heartbeat_ticks=heartbeat.ticks,
                    heartbeat_missed=heartbeat.missed,
                    heartbeat_max_lag_ns=heartbeat.max_lag_ns,
                    heartbeat_max_gap_ns=heartbeat.max_gap_ns,
                    rss_before=rss_before,
                    rss_after=rss_after,
                    traced_before=traced_before,
                    traced_current=None if traced is None else traced[0],
                    traced_peak=None if traced is None else traced[1],
                    tracer_bytes=(
                        tracemalloc.get_tracemalloc_memory() if cell.mode == "memory" else None
                    ),
                )
                wave = observe_terminal(wave, task, counters, pool.checkedout())
                if not wave.operation_settled:
                    pending_wave = wave
                    assert_wave(wave, cell, request.hashes)
                    cell.status, cell.detail = "censored", "operation_not_settled"
                    return
                cell.waves.append(wave)
                emit(wave)
                assert_wave(wave, cell, request.hashes)
                if wave.checked_out:
                    cell.status, cell.detail = "censored", "operation_not_settled"
                    return
                if result.reason == "readiness_timeout":
                    cell.status, cell.detail = "censored", "owner_timeout"
                    return
                if counters.io_unavailable:
                    cell.status, cell.detail = "censored", "database_unavailable"
                    return
                if time.monotonic() >= request.work_deadline:
                    cell.status, cell.detail = "censored", "campaign_work_deadline"
                    return
                assert wave.checkins >= 1 and wave.heartbeat_ticks >= 1
            finally:
                heartbeat.close()
        cell.status, cell.detail, cell.complete = "observed", "expected_semantics", True
    except AssertionError:
        cell.status, cell.detail, cell.complete = "failed", "functional_contradiction", False
        raise
    finally:
        first_error = sys.exception()
        start = time.monotonic_ns()
        cleanup_ok = True

        def retain_failure(error: BaseException) -> None:
            nonlocal first_error
            cell.status, cell.detail, cell.complete = "failed", "terminal_or_cleanup_error", False
            if first_error is None:
                first_error = error
            elif first_error is not error:
                category = next(
                    kind.__name__
                    for kind in (
                        AssertionError,
                        asyncio.CancelledError,
                        KeyboardInterrupt,
                        SystemExit,
                        TimeoutError,
                        OperationalError,
                        RuntimeError,
                        Exception,
                        BaseException,
                    )
                    if isinstance(error, kind)
                )
                BaseException.add_note(first_error, f"Readiness diagnostic secondary: {category}")

        try:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
        except BaseException as error:
            retain_failure(error)
        try:
            async with asyncio.timeout_at(request.work_deadline + 20):
                await engine.dispose()
        except BaseException as error:
            cleanup_ok = False
            retain_failure(error)
        try:
            # Only the un-emitted final wave may change at this owned observation cut.
            if pending_wave is not None:
                assert retained_task is not None
                terminal = observe_terminal(
                    pending_wave, retained_task, counters, pool.checkedout()
                )
                cell.waves.append(terminal)
                emit(terminal)
                assert_wave(terminal, cell, request.hashes)
            elif retained_task is not None and retained_task.done():
                retained_task.result()
        except BaseException as error:
            retain_failure(error)
        try:
            cell.cleanup_ns = time.monotonic_ns() - start
            cell.cleanup_complete = (
                cleanup_ok
                and pool.checkedout() == 0
                and (retained_task is None or retained_task.done())
            )
            if not cell.cleanup_complete and first_error is None:
                cell.complete = False
                cell.status, cell.detail = "censored", "cleanup_incomplete"
        except BaseException as error:
            retain_failure(error)
        try:
            patches.close()
        except BaseException as error:
            retain_failure(error)
        if first_error is not None:
            raise first_error


def main() -> int:
    entry = time.monotonic_ns()
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=CASES)
    parser.add_argument("batch", type=int, choices=BATCHES)
    parser.add_argument("mode", choices=MODES)
    arguments = parser.parse_args()
    cell = Cell.model_validate(
        {
            "case": arguments.case,
            "batch": arguments.batch,
            "mode": arguments.mode,
            "worker_entry_ns": entry,
        }
    )
    try:
        raw = sys.stdin.buffer.read(16385)
        assert len(raw) <= 16384
        request = Request.model_validate_json(raw)
        asyncio.run(measure(cell, request))
    except (TimeoutError, OperationalError):
        if cell.status != "failed":
            cell.status, cell.detail, cell.complete = "censored", "measurement_unavailable", False
    except Exception:
        # No raw driver exception, request payload or credential enters diagnostics.
        cell.status, cell.detail, cell.complete = "failed", "functional_or_harness_error", False
    emit(cell)
    return int(cell.status == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
