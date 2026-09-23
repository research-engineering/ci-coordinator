from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any, Self, get_type_hints

import pytest

from ci_coordinator.app.audit_persistence import append_audit_event
from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditAppendResult,
    AuditEventError,
    AuditEventInput,
    AuditEventRepository,
    AuditLedgerSnapshot,
    AuditReplayRepository,
    PreparedAuditEvent,
    build_prepared_audit_event,
)
from ci_coordinator.audit_replay.persistence_bytes import MAX_AUDIT_TEXT_UTF8_BYTES_V1


def test_repository_protocol_accepts_only_the_exact_prepared_capability_type() -> None:
    hints = get_type_hints(AuditEventRepository.append)

    assert hints["event"] is PreparedAuditEvent


def test_replay_protocol_exposes_only_snapshot_paged_read_authority() -> None:
    assert hasattr(AuditReplayRepository, "snapshot")
    assert hasattr(AuditReplayRepository, "load_page")
    assert not hasattr(AuditReplayRepository, "append")
    assert not hasattr(AuditEventRepository, "list")


def test_append_audit_event_prepares_before_factory_and_commits() -> None:
    calls: list[str] = []
    repository = RecordingRepository(calls)
    unit_of_work = RecordingUnitOfWork(calls, repository)

    def factory() -> RecordingUnitOfWork:
        calls.append("factory")
        return unit_of_work

    result = asyncio.run(append_audit_event(event_input(), unit_of_work_factory=factory))

    assert isinstance(result, AuditAppendAppended)
    assert calls == ["factory", "enter", "append", "commit", "exit"]
    assert type(repository.received) is PreparedAuditEvent


def test_append_audit_event_rejects_before_unit_of_work_factory() -> None:
    factory_calls = 0

    def factory() -> RecordingUnitOfWork:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("factory must not be invoked")

    rejected = event_input(actor="a" * (MAX_AUDIT_TEXT_UTF8_BYTES_V1 + 1))

    with pytest.raises(AuditEventError):
        asyncio.run(append_audit_event(rejected, unit_of_work_factory=factory))

    assert factory_calls == 0


def test_append_failure_does_not_commit() -> None:
    calls: list[str] = []
    repository = FailingRepository(calls)
    unit_of_work = RecordingUnitOfWork(calls, repository)

    with pytest.raises(RuntimeError, match="append failed"):
        asyncio.run(append_audit_event(event_input(), unit_of_work_factory=lambda: unit_of_work))

    assert calls == ["enter", "append", "exit"]
    assert unit_of_work.exit_exception_type is RuntimeError


class RecordingRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.received: PreparedAuditEvent | None = None

    async def append(self, event: PreparedAuditEvent) -> AuditAppendResult:
        self.calls.append("append")
        self.received = event
        return AuditAppendAppended(record=build_prepared_audit_event(event, None))

    async def snapshot(self) -> AuditLedgerSnapshot:
        raise AssertionError("append must not read an audit snapshot")

    async def load_page(
        self,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[Any, ...]:
        del after_sequence, through_sequence, limit
        raise AssertionError("append must not read audit pages")


class FailingRepository(RecordingRepository):
    async def append(self, event: PreparedAuditEvent) -> AuditAppendResult:
        del event
        self.calls.append("append")
        raise RuntimeError("append failed")


class RecordingUnitOfWork:
    def __init__(self, calls: list[str], repository: RecordingRepository) -> None:
        self.calls = calls
        self.audit_events = repository
        self.exit_exception_type: type[BaseException] | None = None

    async def __aenter__(self) -> Self:
        self.calls.append("enter")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        self.exit_exception_type = exc_type
        self.calls.append("exit")

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")


def event_input(*, actor: str = "ci-coordinator") -> AuditEventInput:
    return AuditEventInput(
        idempotency_key="dynamic-ci-plan:plan-1:raw-append",
        subject_type="dynamic-ci-plan",
        subject_id="plan-1",
        event_type="dynamic-ci-plan.planned",
        created_at="2026-07-11T00:00:00.000Z",
        actor=actor,
        payload={"selectedChecks": ["lint", "test"]},
    )
