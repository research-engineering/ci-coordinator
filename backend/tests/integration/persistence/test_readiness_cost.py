from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest
from coverage import Coverage
from scripts.bounded_process import spawn
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

import ci_coordinator.persistence.readiness as readiness_module
from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditEventInput,
    AuditEventRecord,
    prepare_audit_event,
)
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.persistence import PostgresUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import (
    CommitOutcomeUnknown,
    PersistenceInvariantViolation,
    StoreUnavailable,
)
from ci_coordinator.persistence.readiness import DatabaseReadiness, DatabaseReadinessProbe

from . import _readiness_cost_worker as worker
from ._readiness_cost_worker import (
    BATCHES,
    CASES,
    FRAME,
    MODES,
    VALID_CASES,
    Case,
    Cell,
    Wave,
    assert_wave,
)
from .conftest import POSTGRES_IMAGE

pytestmark = pytest.mark.persistence
ROOT = Path(__file__).resolve().parents[4]
WORKER = Path(__file__).with_name("_readiness_cost_worker.py")


class Pilot:
    def __init__(self, path: Path) -> None:
        self.started = time.monotonic()
        self.work_deadline = self.started + 340
        self.final_deadline = self.started + 360
        self.path = path
        self.cells = [
            Cell.model_validate({"case": case, "batch": batch, "mode": mode})
            for case in CASES
            for batch in BATCHES
            for mode in MODES
        ]
        self.setup: dict[str, dict[str, int | str | bool]] = {}
        self.save()

    def save(self) -> None:
        assert len(self.cells) == 48
        assert {(cell.case, cell.batch, cell.mode) for cell in self.cells} == {
            (case, batch, mode)
            for case in ("small", "scalar", "combined", "wide", "syntax", "depth")
            for batch in (1, 4, 16, 4096)
            for mode in ("timing", "memory")
        }
        report = {
            "schemaVersion": "readiness-cost-diagnostic/v1",
            "status": "observed",
            "completeness": "complete"
            if all(cell.complete for cell in self.cells)
            else "incomplete",
            "functionalOrHarnessFailure": any(cell.status == "failed" for cell in self.cells),
            "qualification": "not_assessed",
            "productionBatchSelection": None,
            "workSeconds": 340,
            "settlementReportSeconds": 20,
            "elapsedSeconds": time.monotonic() - self.started,
            "remainingFinalSeconds": max(0.0, self.final_deadline - time.monotonic()),
            "sourceSha": os.environ.get("GITHUB_SHA"),
            "postgresImage": POSTGRES_IMAGE,
            "lockSha256": hashlib.sha256((ROOT / "backend/uv.lock").read_bytes()).hexdigest(),
            "setup": self.setup,
            "expectedCellCount": 48,
            "cellCounts": {
                status: sum(cell.status == status for cell in self.cells)
                for status in ("NOT_MEASURED", "observed", "censored", "failed")
            },
            "cells": [cell.model_dump(mode="json") for cell in self.cells],
            "nonClaims": [
                "No production performance or R1 qualification",
                "No all-input worst case or absolute process/drain bound",
                "RSS high-water includes startup; unchanged does not mean zero readiness cost",
                "SQL, loader, codec and verifier intervals overlap and must not be summed",
                "SQL counters cover cursor events, not all driver pre-ping or server CPU",
                "Ten-millisecond heartbeat is resolution, not an acceptance threshold",
                "Fixture, seed, child startup, IPC and teardown are not readiness latency",
                "Role preflight warms the pool; recovery means an unverified prefix, not cold I/O",
                "Delegating observation hooks are included; no hook-free timing is claimed",
                "Late terminal observations are semantic; censored timing keeps its initial cut",
            ],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


@pytest.fixture(scope="module")
def cost_pilot() -> Iterator[Pilot]:
    shard = os.environ.get("NATIVE_SHARD", "standalone")
    assert shard in {"standalone", "serial", "postgres-1", "postgres-2", "postgres-3", "postgres-4"}
    directory = ROOT / ".ci-native/diagnostics" / shard
    directory.mkdir(parents=True, exist_ok=True)
    pilot = Pilot(directory / "readiness-cost.json")
    try:
        yield pilot
    finally:
        for cell in pilot.cells:
            if cell.status == "NOT_MEASURED" and cell.detail == "campaign_not_started":
                cell.detail = (
                    "work_budget_exhausted"
                    if time.monotonic() >= pilot.work_deadline
                    else "not_reached"
                )
        pilot.save()
        print(f"readiness-cost diagnostic: {pilot.path.relative_to(ROOT)}; no R1 qualification")


def payload(case: str) -> tuple[JsonValue, bytes]:
    if case == "small":
        return {"value": 1}, b'{"value":1}'
    if case == "scalar":
        return "a" * 1048574, b'"' + b"a" * 1048574 + b'"'
    nested: JsonValue = 0
    for _ in range(63):
        nested = [nested]
    value: JsonValue = [nested, *([0] * 9934), "a" * 1028576]
    expected = b"[" + b"[" * 63 + b"0" + b"]" * 63 + b"," + b"0," * 9934
    expected += b'"' + b"a" * 1028576 + b'"]'
    assert len(expected) == 1048576
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


def malformed(case: str) -> bytes:
    raw = {
        "wide": lambda: b"[" + b"0," * 524286 + b"0] ",
        "syntax": lambda: b"[" + b"0," * 524287 + b"?",
        "depth": lambda: b"[" * 65 + b'"' + b"a" * 1048444 + b'"' + b"]" * 65,
    }[case]()
    assert len(raw) == 1048576
    return raw


async def seed(case: str, runtime_url: str, admin_url: str, deadline: float) -> list[str]:
    engine = create_postgres_engine(runtime_url)
    admin = create_postgres_engine(admin_url)
    hashes: list[str] = []
    try:
        async with asyncio.timeout_at(deadline):
            for sequence in range(1, 33):
                if time.monotonic() >= deadline:
                    raise TimeoutError
                value, expected = payload(case)
                key = f"r1:{case}:{sequence:06d}"
                event_input = AuditEventInput(
                    idempotency_key=key if case == "small" else key.ljust(4096, "k"),
                    subject_type="dynamic-ci-plan",
                    subject_id="plan" if case == "small" else "s" * 4096,
                    event_type="plan.persisted" if case == "small" else "e" * 4096,
                    created_at="2026-07-11T12:00:00.000Z",
                    actor="test-suite" if case == "small" else "a" * 4096,
                    payload=value,
                )
                prepared = prepare_audit_event(event_input)
                assert prepared.payload_canonical_bytes == expected
                assert prepared.payload_hash == hashlib.sha256(expected).hexdigest()
                if case != "small":
                    assert all(
                        len(item.encode()) == 4096
                        for item in (
                            prepared.idempotency_key,
                            prepared.subject_id,
                            prepared.event_type,
                            prepared.actor,
                        )
                    )
                async with PostgresUnitOfWork(engine) as unit:
                    result = await unit.audit_events.append(prepared)
                    assert isinstance(result, AuditAppendAppended)
                    assert result.record.sequence == sequence
                    assert result.record.previous_event_hash == (hashes[-1] if hashes else None)
                    assert result.record.payload_hash == hashlib.sha256(expected).hexdigest()
                    await unit.commit()
                    hashes.append(result.record.event_hash)
                del prepared, event_input, value, expected, result
            if time.monotonic() >= deadline:
                raise TimeoutError
            if case not in VALID_CASES:
                async with admin.begin() as connection:
                    changed = await connection.execute(
                        text(
                            "UPDATE ci_coordinator.audit_events SET payload_canonical_json = :raw "
                            "WHERE sequence = 32"
                        ),
                        {"raw": malformed(case)},
                    )
                    assert changed.rowcount == 1
        return hashes
    finally:
        pending_error = sys.exception()
        try:
            async with asyncio.timeout_at(deadline + 20):
                await engine.dispose()
                await admin.dispose()
        except Exception:
            if pending_error is not None:
                raise pending_error from None
            raise


def admit_frames(stdout: str, expected: Cell, hashes: list[str]) -> Cell | None:
    final = None
    for line in stdout.splitlines():
        frame = FRAME.validate_json(line)
        assert final is None
        if isinstance(frame, Wave):
            assert len(expected.waves) < 33
            assert frame.index == len(expected.waves) + 1
            assert_wave(frame, expected, hashes)
            expected.waves.append(frame)
        else:
            assert (frame.case, frame.batch, frame.mode) == (
                expected.case,
                expected.batch,
                expected.mode,
            )
            assert frame.waves == expected.waves
            assert frame.status in {"observed", "censored", "failed"}
            final = frame
    if final is not None and final.complete:
        assert final.status == "observed" and final.detail == "expected_semantics"
        assert final.cleanup_complete and final.role == "ci_coordinator_runtime_test"
        assert final.coverage_active is False
        assert final.timing_traced == (final.mode == "memory")
        count = (32 + expected.batch - 1) // expected.batch
        assert len(final.waves) == count + int(expected.case in VALID_CASES)
        for wave in final.waves:
            assert wave.operation_settled and wave.checked_out == 0 and wave.checkins >= 1
            assert wave.heartbeat_ticks >= 1
            if final.mode == "timing":
                assert wave.traced_before is wave.traced_current is wave.traced_peak is None
                assert wave.tracer_bytes is None
            else:
                assert wave.traced_before is not None and wave.traced_peak is not None
                assert wave.traced_peak >= wave.traced_before
    return final


def run_cell(pilot: Pilot, cell: Cell, url: str, hashes: list[str]) -> Cell:
    remaining = pilot.work_deadline - time.monotonic()
    if remaining <= 0:
        cell.detail = "work_budget_exhausted"
        return cell
    cell.status, cell.detail = "censored", "child_started_no_terminal_report"
    pilot.save()
    remaining = pilot.work_deadline - time.monotonic()
    if remaining <= 0:
        cell.status, cell.detail = "NOT_MEASURED", "work_budget_exhausted"
        return cell
    waves = (32 + cell.batch - 1) // cell.batch + int(cell.case in VALID_CASES)
    started = time.monotonic_ns()
    result = spawn(
        sys.executable,
        ("-B", str(WORKER), cell.case, str(cell.batch), cell.mode),
        cwd=ROOT / "backend",
        max_buffer=262144,
        timeout_seconds=min(remaining, 10 + 5 * waves + 5),
        decode_errors="strict",
        env={
            "PATH": os.defpath,
            "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "backend/src"))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        },
        input_text=json.dumps(
            {
                "database_url": url,
                "hashes": hashes,
                "work_deadline": pilot.work_deadline,
            }
        ),
    )
    elapsed = time.monotonic_ns() - started
    stdout = result.stdout
    if result.error is not None and stdout and not stdout.endswith("\n"):
        stdout, _separator, tail = stdout.rpartition("\n")
        cell.partial_frame_bytes = len(tail.encode("utf-8"))
    final = admit_frames(stdout, cell, hashes)
    target = final if final is not None else cell
    target.process_wall_ns = elapsed
    target.child_status = result.status
    target.child_failure = result.failure_kind
    target.child_signal = result.signal
    target.child_quiescent = result.process_group_quiescent
    target.stdout_bytes = len(result.stdout.encode("utf-8"))
    target.stderr_bytes = len(result.stderr.encode("utf-8"))
    if target.worker_entry_ns is not None:
        target.spawn_to_worker_entry_ns = target.worker_entry_ns - started
        assert 0 <= target.spawn_to_worker_entry_ns <= elapsed
    if final is not None and final.status == "failed":
        return final
    if result.error is not None or result.status != 0:
        target.complete = False
        target.status, target.detail = "censored", f"child_{result.failure_kind or 'exit'}"
        if result.failure_kind not in {"timeout", "cancelled", "signal", "spawn"}:
            target.status, target.detail = "failed", "worker_output_or_lifecycle_failure"
        return target
    assert final is not None
    assert not result.stderr
    assert result.process_group_quiescent is True
    final.process_wall_ns = elapsed
    return final


@pytest.mark.parametrize("case", CASES)
def test_readiness_cost_diagnostic(
    case: Case,
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    cost_pilot: Pilot,
) -> None:
    cells = [cell for cell in cost_pilot.cells if cell.case == case]
    if time.monotonic() >= cost_pilot.work_deadline:
        for cell in cells:
            cell.detail = "work_budget_exhausted"
        cost_pilot.save()
        return
    started = time.monotonic_ns()
    cost_pilot.setup[case] = {"outcome": "seed_started_no_terminal_receipt"}
    cost_pilot.save()
    try:
        hashes = asyncio.run(
            seed(
                case, runtime_postgres_database_url, postgres_database_url, cost_pilot.work_deadline
            )
        )
    except Exception as error:
        if isinstance(error, TimeoutError | OperationalError) or (
            type(error) is StoreUnavailable and isinstance(error.__cause__, OperationalError)
        ):
            cost_pilot.setup[case] = {
                "outcome": "unavailable",
                "elapsedNs": time.monotonic_ns() - started,
            }
            for cell in cells:
                cell.detail = "seed_unavailable_no_negative_observed"
            cost_pilot.save()
            return
        for cell in cells:
            cell.status, cell.detail = "failed", "public_seed_contradiction_or_harness_error"
        cost_pilot.save()
        pytest.fail("readiness-cost public seed failed; no accepted negative", pytrace=False)
    finally:
        cost_pilot.setup[case]["elapsedNs"] = time.monotonic_ns() - started
        cost_pilot.save()
    cost_pilot.setup[case] = {"outcome": "committed32", "elapsedNs": time.monotonic_ns() - started}
    offset = CASES.index(case) % 4
    order = (*BATCHES[offset:], *BATCHES[:offset])
    try:
        for mode in MODES:
            for batch in order:
                cell = next(item for item in cells if item.batch == batch and item.mode == mode)
                index = cost_pilot.cells.index(cell)
                try:
                    measured = run_cell(cost_pilot, cell, runtime_postgres_database_url, hashes)
                except Exception:
                    cell.status, cell.detail = "failed", "observation_contract_contradiction"
                    cost_pilot.save()
                    pytest.fail(
                        "readiness-cost observation contradicted its contract", pytrace=False
                    )
                cost_pilot.cells[index] = measured
                cost_pilot.save()
                if measured.status == "failed":
                    pytest.fail("readiness-cost reached a functional contradiction", pytrace=False)
    finally:
        for item in cost_pilot.cells:
            if item.case == case and item.detail == "campaign_not_started":
                item.detail = "not_reached"
        cost_pilot.save()


def _literal_wave(case: Literal["small", "wide"]) -> tuple[Wave, list[str]]:
    hashes = [f"{index:064x}" for index in range(1, 33)]
    valid = case == "small"
    decoded = list(range(1, 33 if valid else 32))
    return Wave(
        index=1,
        phase="recovery",
        ready=valid,
        reason="ready" if valid else "store_unavailable_or_invalid",
        revision=32 if valid else None,
        settled_ready=valid,
        settled_reason="ready" if valid else "store_unavailable_or_invalid",
        settled_revision=32 if valid else None,
        waiter_ns=100,
        settled_ns=101,
        process_cpu_ns=80,
        thread_cpu_ns=70,
        sql_ns=20,
        sql_calls=4,
        loader_ns=40,
        codec_ns=30,
        verifier_ns=20 if valid else 0,
        loads=[(0, 32, 4096)],
        sql_pages=[(0, 32, 4096)],
        decoded=decoded,
        attempted=list(range(1, 33)),
        decoded_hashes=hashes[: len(decoded)],
        previous_hashes=[None, *hashes[: len(decoded) - 1]],
        returned=decoded if valid else [],
        codec_failure=None if valid else "nodes",
        failure_observed=None if valid else 10001,
        json_error_offset=None,
        verifier_calls=1 if valid else 0,
        checkins=1,
        checked_out=0,
        operation_settled=True,
        io_unavailable=False,
        heartbeat_ticks=2,
        heartbeat_missed=0,
        heartbeat_max_lag_ns=3,
        heartbeat_max_gap_ns=10000003,
        rss_before=1000,
        rss_after=1001,
        traced_before=None,
        traced_current=None,
        traced_peak=None,
        tracer_bytes=None,
    ), hashes


@pytest.mark.parametrize("case", ["small", "wide"])
@pytest.mark.parametrize(
    ("change", "accepted"),
    [
        ({}, True),
        ({"ready": False, "reason": "readiness_timeout", "revision": None}, True),
        ({"reason": "other"}, False),
        ({"revision": 31}, False),
        ({"reason": "readiness_timeout", "ready": True, "revision": None}, False),
        ({"reason": "readiness_timeout", "ready": False, "revision": 32}, False),
        ({"flip_public": True}, False),
        ({"flip_settled_after_timeout": True}, False),
    ],
)
def test_cost_public_outcome_controls(
    case: Literal["small", "wide"], change: dict[str, object], accepted: bool
) -> None:
    wave, hashes = _literal_wave(case)
    fields = wave.model_dump()
    if change == {"flip_public": True}:
        fields |= {
            "ready": case != "small",
            "reason": "store_unavailable_or_invalid" if case == "small" else "ready",
            "revision": None if case == "small" else 32,
        }
    elif change == {"flip_settled_after_timeout": True}:
        fields |= {
            "ready": False,
            "reason": "readiness_timeout",
            "revision": None,
            "settled_ready": case != "small",
            "settled_reason": "store_unavailable_or_invalid" if case == "small" else "ready",
            "settled_revision": None if case == "small" else 32,
        }
    else:
        fields |= change
    report = json.dumps(fields)
    cell = Cell(case=case, batch=4096, mode="timing")
    if accepted:
        assert admit_frames(report, cell, hashes) is None
        assert [item.model_dump(mode="json") for item in cell.waves] == [json.loads(report)]
    else:
        with pytest.raises(AssertionError):
            admit_frames(report, cell, hashes)


@pytest.mark.parametrize(
    ("fault", "unavailable"),
    [
        ("operational", True),
        ("assertion", False),
        ("invariant", False),
        ("unknown-commit", False),
        ("missing-cause", False),
    ],
)
def test_cost_seed_error_controls(
    fault: str,
    unavailable: bool,
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pilot = Pilot(tmp_path / "cost.json")
    original_seed = seed
    operation = OperationalError("SELECT 1", {}, OSError("controlled unavailable"))
    assertion = AssertionError("controlled assertion")
    reached: list[str] = []

    def connect(_engine: AsyncEngine) -> None:
        reached.append("connect")
        raise operation if fault == "operational" else assertion

    async def selected_seed(*args: object) -> list[str]:
        del args
        if fault in {"operational", "assertion"}:
            try:
                return await original_seed(
                    "small",
                    runtime_postgres_database_url,
                    postgres_database_url,
                    pilot.work_deadline,
                )
            except StoreUnavailable as error:
                assert type(error) is StoreUnavailable
                assert error.__cause__ is (operation if fault == "operational" else assertion)
                reached.append("public-wrapper")
                raise
        reached.append("typed-error")
        if fault == "invariant":
            raise PersistenceInvariantViolation("controlled invariant") from operation
        if fault == "unknown-commit":
            raise CommitOutcomeUnknown("controlled unknown commit") from operation
        raise StoreUnavailable("controlled absent cause") from None

    def no_launch(*_args: object, **_kwargs: object) -> None:
        pytest.fail("seed failure must not launch a measurement child")

    monkeypatch.setattr(AsyncEngine, "connect", connect)
    monkeypatch.setattr(sys.modules[__name__], "seed", selected_seed)
    monkeypatch.setattr(sys.modules[__name__], "spawn", no_launch)
    if unavailable:
        test_readiness_cost_diagnostic(
            "small", runtime_postgres_database_url, postgres_database_url, pilot
        )
    else:
        with pytest.raises(pytest.fail.Exception, match="public seed failed"):
            test_readiness_cost_diagnostic(
                "small", runtime_postgres_database_url, postgres_database_url, pilot
            )
    assert reached == (
        ["connect", "public-wrapper"] if fault in {"operational", "assertion"} else ["typed-error"]
    )
    report = json.loads(pilot.path.read_text())
    selected = [cell for cell in report["cells"] if cell["case"] == "small"]
    assert len(selected) == 8
    assert {cell["status"] for cell in selected} == (
        {"NOT_MEASURED"} if unavailable else {"failed"}
    )
    assert all(cell["waves"] == [] and cell["complete"] is False for cell in report["cells"])
    assert report["functionalOrHarnessFailure"] is (not unavailable)
    assert report["expectedCellCount"] == 48 and report["completeness"] == "incomplete"


@pytest.mark.parametrize(
    ("cut", "outcome", "primary_kind", "cleanup_failure"),
    [
        ("heartbeat", "good", "none", False),
        ("heartbeat", "bad", "none", False),
        ("heartbeat", "assertion", "none", False),
        ("cleanup", "good", "none", False),
        ("cleanup", "bad", "none", False),
        ("cleanup", "assertion", "none", False),
        ("pending", "good", "none", False),
        ("pending", "bad", "none", False),
        ("pending", "assertion", "none", False),
        ("cleanup", "bad", "assertion", False),
        ("cleanup", "assertion", "assertion", False),
        ("cleanup", "good", "assertion", True),
        ("cleanup", "bad", "cancellation", False),
        ("cleanup", "assertion", "cancellation", False),
        ("cleanup", "good", "cancellation", True),
        ("cleanup", "good", "none", True),
    ],
)
def test_cost_final_owned_observation_controls(
    cut: str,
    outcome: str,
    primary_kind: str,
    cleanup_failure: bool,
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        hashes = await seed(
            "small", runtime_postgres_database_url, postgres_database_url, time.monotonic() + 60
        )
        entered, release, terminal = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_wait = asyncio.wait
        original_tick = worker.Heartbeat.after_tick
        original_dispose = AsyncEngine.dispose
        original_assert_wave = worker.assert_wave
        retained: asyncio.Task[tuple[DatabaseReadiness, AuditEventRecord | None]] | None = None
        frames: list[str] = []
        held_frames: list[tuple[str, ...]] = []
        failure = AssertionError("controlled late terminal assertion")
        cleanup_error = RuntimeError("controlled private cleanup failure")
        primary: BaseException | None = (
            AssertionError("controlled private primary assertion")
            if primary_kind == "assertion"
            else asyncio.CancelledError("controlled private primary cancellation")
            if primary_kind == "cancellation"
            else None
        )
        primary_reached = asyncio.Event()

        async def delayed() -> tuple[DatabaseReadiness, AuditEventRecord | None]:
            entered.set()
            try:
                await release.wait()
                if outcome == "assertion":
                    raise failure
                return (
                    DatabaseReadiness(True, "ready", 32)
                    if outcome == "good"
                    else DatabaseReadiness(False, "store_unavailable_or_invalid")
                ), None
            finally:
                terminal.set()

        class ControlledProbe(DatabaseReadinessProbe):
            async def check(self) -> DatabaseReadiness:
                nonlocal retained
                result = await super().check()
                if result.verified_revision != 32:
                    return result
                assert result == DatabaseReadiness(True, "ready", 32)
                retained = asyncio.create_task(delayed())
                self._inflight = retained
                await entered.wait()
                return DatabaseReadiness(False, "readiness_timeout")

        async def wait_cut(
            tasks: set[asyncio.Task[tuple[DatabaseReadiness, AuditEventRecord | None]]],
            *,
            timeout: float,  # noqa: ASYNC109 - preserve the controlled asyncio.wait boundary
        ) -> tuple[
            set[asyncio.Task[tuple[DatabaseReadiness, AuditEventRecord | None]]],
            set[asyncio.Task[tuple[DatabaseReadiness, AuditEventRecord | None]]],
        ]:
            if retained is not None and retained in tasks:
                assert timeout == 5 and not retained.done()
                return set(), tasks
            return await original_wait(tasks, timeout=timeout)

        async def tick(heartbeat: worker.Heartbeat) -> None:
            await original_tick(heartbeat)
            if retained is not None:
                held_frames.append(tuple(frames))
                if cut == "heartbeat":
                    release.set()
                    await terminal.wait()

        async def dispose(engine: AsyncEngine, close: bool = True) -> None:
            await original_dispose(engine, close=close)
            if retained is not None:
                if cut in {"cleanup", "pending"}:
                    assert len(frames) == 1
                if cut == "cleanup":
                    if primary is not None:
                        assert primary_reached.is_set()
                    release.set()
                    await terminal.wait()
                    if cleanup_failure:
                        raise cleanup_error

        def pending_primary(wave: Wave, observed: Cell, receipts: list[str]) -> None:
            original_assert_wave(wave, observed, receipts)
            if primary is not None and not wave.operation_settled:
                assert retained is not None and not retained.done()
                assert not primary_reached.is_set()
                primary_reached.set()
                raise primary

        cell = Cell(case="small", batch=16, mode="timing")
        request = worker.Request(
            database_url=runtime_postgres_database_url,
            hashes=hashes,
            work_deadline=time.monotonic() + 60,
        )
        # These cuts test the harness, not uninstrumented cost or coverage isolation.
        with monkeypatch.context() as controls:
            controls.setattr(Coverage, "current", lambda: None)
            controls.setattr(sys, "gettrace", lambda: None)
            controls.setattr(readiness_module, "DatabaseReadinessProbe", ControlledProbe)
            controls.setattr(asyncio, "wait", wait_cut)
            controls.setattr(worker.Heartbeat, "after_tick", tick)
            controls.setattr(AsyncEngine, "dispose", dispose)
            controls.setattr(worker, "emit", lambda value: frames.append(value.model_dump_json()))
            controls.setattr(worker, "assert_wave", pending_primary)
            try:
                if primary is not None:
                    with pytest.raises(type(primary)) as caught_primary:
                        async with asyncio.timeout_at(request.work_deadline + 20):
                            await worker.measure(cell, request)
                    assert caught_primary.value is primary
                    assert primary_reached.is_set() and terminal.is_set()
                    assert primary.__notes__ == [
                        "Readiness diagnostic secondary: RuntimeError"
                        if cleanup_failure
                        else "Readiness diagnostic secondary: AssertionError"
                    ]
                    assert cell.status == "failed" and not cell.complete
                elif cleanup_failure:
                    with pytest.raises(RuntimeError) as caught_cleanup:
                        async with asyncio.timeout_at(request.work_deadline + 20):
                            await worker.measure(cell, request)
                    assert caught_cleanup.value is cleanup_error
                    assert cell.status == "failed" and not cell.complete
                elif cut != "pending" and outcome != "good":
                    with pytest.raises(AssertionError) as caught:
                        async with asyncio.timeout_at(request.work_deadline + 20):
                            await worker.measure(cell, request)
                    if outcome == "assertion":
                        assert caught.value is failure
                    assert cell.status == "failed" and not cell.complete
                else:
                    async with asyncio.timeout_at(request.work_deadline + 20):
                        await worker.measure(cell, request)
                    assert cell.status == "censored" and not cell.complete
                assert retained is not None
                assert readiness_module.READINESS_AUDIT_BATCH_SIZE == 4096
                assert held_frames and len(held_frames[0]) == 1
                assert frames[0] == held_frames[0][0]
                assert [json.loads(frame) for frame in frames] == [
                    wave.model_dump(mode="json") for wave in cell.waves
                ]
                assert cell.waves[0].revision == 16
                if outcome != "assertion" or cut == "pending":
                    last = cell.waves[-1]
                    assert (last.ready, last.reason, last.revision) == (
                        False,
                        "readiness_timeout",
                        None,
                    )
                    assert last.operation_settled is (cut != "pending")
                    if cut != "pending":
                        assert (last.settled_ready, last.settled_reason, last.settled_revision) == (
                            (True, "ready", 32)
                            if outcome == "good"
                            else (False, "store_unavailable_or_invalid", None)
                        )
                    else:
                        assert (last.settled_ready, last.settled_reason, last.settled_revision) == (
                            None,
                            None,
                            None,
                        )
                if outcome == "good" or cut == "pending":
                    captured = "\n".join([*frames, cell.model_dump_json()])
                    final = admit_frames(
                        captured, Cell(case="small", batch=16, mode="timing"), hashes
                    )
                    assert final is not None and not final.complete
                    assert final.status == (
                        "failed" if primary is not None or cleanup_failure else "censored"
                    )
            finally:
                if retained is not None:
                    if not retained.done():
                        retained.cancel()
                    await asyncio.gather(retained, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["none", "phase", "zero-index", "extra-index", "invalid-warm", "population"]
)
def test_cost_literal_phase_and_population_controls(fault: str, tmp_path: Path) -> None:
    pilot = Pilot(tmp_path / "cost.json")
    initial = json.loads(pilot.path.read_text())
    assert {(item["case"], item["batch"], item["mode"]) for item in initial["cells"]} == {
        (shape, batch, mode)
        for shape in ("small", "scalar", "combined", "wide", "syntax", "depth")
        for batch in (1, 4, 16, 4096)
        for mode in ("timing", "memory")
    }
    if fault == "population":
        original = pilot.cells[-1]
        pilot.cells[-1] = original.model_copy(update={"batch": 2})
        assert len(pilot.cells) == len({(c.case, c.batch, c.mode) for c in pilot.cells}) == 48
        with pytest.raises(AssertionError):
            pilot.save()
        assert json.loads(pilot.path.read_text())["expectedCellCount"] == 48
        return
    recovery, hashes = _literal_wave("small")
    warm = recovery.model_copy(
        update={
            "index": 2,
            "phase": "warm",
            "loads": [],
            "sql_pages": [],
            "decoded": [],
            "attempted": [],
            "decoded_hashes": [],
            "previous_hashes": [],
            "returned": [],
            "verifier_calls": 0,
        }
    )
    cell = Cell(case="wide" if fault == "invalid-warm" else "small", batch=4096, mode="timing")
    if fault == "none":
        assert (
            admit_frames(
                "\n".join((recovery.model_dump_json(), warm.model_dump_json())), cell, hashes
            )
            is None
        )
        assert [(w.index, w.phase) for w in cell.waves] == [(1, "recovery"), (2, "warm")]
    else:
        index = {"phase": 1, "zero-index": 0, "extra-index": 3, "invalid-warm": 2}[fault]
        with pytest.raises(AssertionError):
            assert_wave(warm.model_copy(update={"index": index}), cell, hashes)
