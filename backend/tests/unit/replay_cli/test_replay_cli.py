from __future__ import annotations

import asyncio
from dataclasses import replace
from io import StringIO
from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator import replay_cli
from ci_coordinator.audit_replay import (
    AuditEventInput,
    AuditEventRecord,
    AuditLedgerSnapshot,
    build_audit_event,
)
from ci_coordinator.audit_replay.replay import (
    AllAuditReplayFilter,
    AuditEventIdReplayFilter,
    SubjectAuditReplayFilter,
)
from ci_coordinator.replay_cli import (
    ReplayCliConfigurationError,
    ReplayCommand,
    execute_replay,
    parse_replay_command,
    run_replay,
)
from ci_coordinator.runtime_settings import UnsupportedPythonRuntime


@pytest.mark.parametrize(
    "argv",
    (
        ("--all", "--subject", "dynamic-ci-plan:plan-1"),
        ("--all", "--audit-event-id", f"audit_{'0' * 32}"),
        ("--subject", "dynamic-ci-plan:plan-1", "--audit-event-id", f"audit_{'0' * 32}"),
    ),
)
def test_parse_rejects_multiple_replay_filters(argv: tuple[str, ...]) -> None:
    with pytest.raises(ReplayCliConfigurationError):
        parse_replay_command(("--database-dsn", "postgresql+psycopg://example", *argv))


def test_parse_admits_all_supported_filters_and_hides_dsn_from_repr() -> None:
    secret_dsn = "postgresql+psycopg://user:password@example/db"

    all_command = parse_replay_command(("--database-dsn", secret_dsn, "--all"))
    subject_command = parse_replay_command(
        ("--database-dsn", secret_dsn, "--subject", "dynamic-ci-plan:plan-1")
    )
    event_command = parse_replay_command(
        ("--database-dsn", secret_dsn, "--audit-event-id", f"audit_{'0' * 32}")
    )
    environment_command = parse_replay_command(
        ("--all",),
        {"CI_COORDINATOR_DATABASE_DSN": secret_dsn},
    )

    assert isinstance(all_command.replay_filter, AllAuditReplayFilter)
    assert subject_command.replay_filter == SubjectAuditReplayFilter(
        subject_type="dynamic-ci-plan",
        subject_id="plan-1",
    )
    assert event_command.replay_filter == AuditEventIdReplayFilter(
        audit_event_id=f"audit_{'0' * 32}"
    )
    assert environment_command.database_dsn == secret_dsn
    assert secret_dsn not in repr(all_command)


def test_configuration_failure_redacts_dsn_from_all_observable_output() -> None:
    secret_dsn = "postgresql+psycopg://user:password@example/db"
    stdout = StringIO()
    stderr = StringIO()

    exit_code = asyncio.run(
        run_replay(
            ("--database-dsn", secret_dsn, "--all", "--unexpected"),
            stdout=stdout,
            stderr=stderr,
        )
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == '{"code":"configuration_or_storage_failure"}\n'
    assert secret_dsn not in stderr.getvalue()


def test_valid_result_uses_canonical_compact_json_and_zero_exit_code() -> None:
    exit_code, stdout, stderr, _engine, units, _repository = _run(("--all",), ())

    assert exit_code == 0
    assert stdout == (
        '{"events":[],"filter":{"kind":"all"},"ledger":{"lastEventHash":null,'
        '"totalEvents":0,"valid":true},"ok":true,"status":"valid"}\n'
    )
    assert stderr == ""
    assert len(units) == 1
    assert units[0].exited


def test_not_found_result_has_exit_code_three() -> None:
    exit_code, stdout, stderr, _engine, _units, _repository = _run(
        ("--subject", "dynamic-ci-plan:missing"),
        (_valid_event(),),
    )

    assert exit_code == 3
    assert '"status":"not-found"' in stdout
    assert stderr == ""


def test_invalid_ledger_result_has_exit_code_four() -> None:
    invalid_event = replace(_valid_event(), payload_hash="0" * 64)
    exit_code, stdout, stderr, _engine, _units, _repository = _run(("--all",), (invalid_event,))

    assert exit_code == 4
    assert '"status":"invalid-ledger"' in stdout
    assert stderr == ""


def test_storage_failure_has_exit_code_two_and_redacts_exception_text() -> None:
    secret_dsn = "postgresql+psycopg://user:password@example/db"
    stdout = StringIO()
    stderr = StringIO()

    def unavailable_engine_factory(_dsn: str) -> AsyncEngine:
        raise RuntimeError(secret_dsn)

    exit_code = asyncio.run(
        run_replay(
            ("--database-dsn", secret_dsn, "--all"),
            stdout=stdout,
            stderr=stderr,
            engine_factory=unavailable_engine_factory,
        )
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == '{"code":"configuration_or_storage_failure"}\n'
    assert secret_dsn not in stderr.getvalue()


def test_include_payload_controls_report_projection() -> None:
    event = _valid_event()
    without_payload = _run(("--all",), (event,))[1]
    with_payload = _run(("--all", "--include-payload"), (event,))[1]

    assert '"payload":' not in without_payload
    assert '"payload":{"accepted":true,"index":0}' in with_payload


def test_execute_replay_closes_unit_of_work_and_engine_after_database_read() -> None:
    engine = _FakeEngine()
    repository = _FakeAuditEventRepository((_valid_event(),))
    unit_of_work_factory = _FakeUnitOfWorkFactory(repository)
    stdout = StringIO()
    command = ReplayCommand(
        replay_filter=AllAuditReplayFilter(),
        include_payload=False,
        database_dsn="postgresql+psycopg://example/db",
    )

    exit_code = asyncio.run(
        execute_replay(
            command,
            stdout=stdout,
            engine_factory=lambda _dsn: cast(AsyncEngine, engine),
            unit_of_work_factory=unit_of_work_factory,
        )
    )

    assert exit_code == 0
    assert len(unit_of_work_factory.units) == 2
    assert all(unit.entered and unit.exited for unit in unit_of_work_factory.units)
    assert engine.disposed
    assert '"status":"valid"' in stdout.getvalue()


def test_storage_failure_closes_unit_of_work_and_engine() -> None:
    engine = _FakeEngine()
    repository = _FakeAuditEventRepository((), snapshot_error=RuntimeError("database unavailable"))
    unit_of_work_factory = _FakeUnitOfWorkFactory(repository)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = asyncio.run(
        run_replay(
            ("--database-dsn", "postgresql+psycopg://example/db", "--all"),
            stdout=stdout,
            stderr=stderr,
            engine_factory=lambda _dsn: cast(AsyncEngine, engine),
            unit_of_work_factory=unit_of_work_factory,
        )
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == '{"code":"configuration_or_storage_failure"}\n'
    assert len(unit_of_work_factory.units) == 1
    assert unit_of_work_factory.units[0].exited
    assert engine.disposed


def test_non_empty_replay_uses_two_bounded_passes_over_one_frozen_epoch() -> None:
    events = _valid_events(17)

    exit_code, stdout, stderr, _engine, units, repository = _run(("--all",), events)

    assert exit_code == 0
    assert stderr == ""
    assert stdout.count('"auditEventId"') == len(events)
    assert len(units) == 2
    assert repository.page_requests == [
        (0, 17, 16),
        (16, 17, 16),
        (0, 17, 16),
        (16, 17, 16),
    ]


def test_second_pass_failure_publishes_no_partial_replay_document() -> None:
    engine = _FakeEngine()
    repository = _ChangingSecondPassRepository(_valid_events(1))
    unit_of_work_factory = _FakeUnitOfWorkFactory(repository)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = asyncio.run(
        run_replay(
            ("--database-dsn", "postgresql+psycopg://example/db", "--all"),
            stdout=stdout,
            stderr=stderr,
            engine_factory=lambda _dsn: cast(AsyncEngine, engine),
            unit_of_work_factory=unit_of_work_factory,
        )
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == '{"code":"configuration_or_storage_failure"}\n'
    assert len(unit_of_work_factory.units) == 2
    assert all(unit.exited for unit in unit_of_work_factory.units)
    assert engine.disposed


def test_replay_entrypoint_rejects_an_unsupported_interpreter_before_parsing(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        replay_cli,
        "admit_python_runtime",
        lambda: UnsupportedPythonRuntime("PyPy", "3.13.14", "CPython", ("3.13.15",)),
    )
    monkeypatch.setattr(
        replay_cli,
        "run_replay",
        lambda *_args, **_kwargs: pytest.fail(
            "replay must not start on an unsupported interpreter"
        ),
    )

    assert replay_cli.main(("--all",)) == 2

    assert capsys.readouterr().err == (
        '{"actualBuildVariant":"gil-enabled","actualImplementation":"PyPy",'
        '"actualVersion":"3.13.14","code":"unsupported_python_runtime",'
        '"supportedBuildVariant":"gil-enabled","supportedImplementation":"CPython",'
        '"supportedVersions":["3.13.15"]}\n'
    )


def _run(
    filter_argv: tuple[str, ...],
    records: tuple[AuditEventRecord, ...],
) -> tuple[
    int,
    str,
    str,
    _FakeEngine,
    list[_FakeUnitOfWork],
    _FakeAuditEventRepository,
]:
    engine = _FakeEngine()
    repository = _FakeAuditEventRepository(records)
    unit_of_work_factory = _FakeUnitOfWorkFactory(repository)
    stdout = StringIO()
    stderr = StringIO()
    exit_code = asyncio.run(
        run_replay(
            ("--database-dsn", "postgresql+psycopg://example/db", *filter_argv),
            stdout=stdout,
            stderr=stderr,
            engine_factory=lambda _dsn: cast(AsyncEngine, engine),
            unit_of_work_factory=unit_of_work_factory,
        )
    )
    return (
        exit_code,
        stdout.getvalue(),
        stderr.getvalue(),
        engine,
        unit_of_work_factory.units,
        repository,
    )


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeAuditEventRepository:
    def __init__(
        self,
        records: tuple[AuditEventRecord, ...],
        snapshot_error: Exception | None = None,
    ) -> None:
        self._records = records
        self._snapshot_error = snapshot_error
        self.page_requests: list[tuple[int, int, int]] = []

    async def snapshot(self) -> AuditLedgerSnapshot:
        if self._snapshot_error is not None:
            raise self._snapshot_error
        last = self._records[-1] if self._records else None
        return AuditLedgerSnapshot(
            last_sequence=0 if last is None else last.sequence,
            last_event_hash=None if last is None else last.event_hash,
            maximum_sequence=None if last is None else last.sequence,
        )

    async def load_page(
        self,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]:
        self.page_requests.append((after_sequence, through_sequence, limit))
        return tuple(
            event for event in self._records if after_sequence < event.sequence <= through_sequence
        )[:limit]


class _ChangingSecondPassRepository(_FakeAuditEventRepository):
    async def load_page(
        self,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]:
        page = await super().load_page(
            after_sequence=after_sequence,
            through_sequence=through_sequence,
            limit=limit,
        )
        if len(self.page_requests) == 2:
            return (replace(page[0], payload_hash="0" * 64),)
        return page


class _FakeUnitOfWork:
    def __init__(self, repository: _FakeAuditEventRepository) -> None:
        self.audit_events = repository
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeUnitOfWork:
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.exited = True


class _FakeUnitOfWorkFactory:
    def __init__(self, repository: _FakeAuditEventRepository) -> None:
        self._repository = repository
        self.units: list[_FakeUnitOfWork] = []

    def __call__(self, _engine: AsyncEngine) -> _FakeUnitOfWork:
        unit = _FakeUnitOfWork(self._repository)
        self.units.append(unit)
        return unit


def _valid_event() -> AuditEventRecord:
    return _valid_events(1)[0]


def _valid_events(count: int) -> tuple[AuditEventRecord, ...]:
    events: list[AuditEventRecord] = []
    previous: AuditEventRecord | None = None
    for index in range(count):
        event = build_audit_event(
            AuditEventInput(
                idempotency_key=f"replay-cli-test-{index}",
                subject_type="dynamic-ci-plan",
                subject_id="plan-1",
                event_type="dynamic-ci-plan.verified",
                created_at="2026-07-15T00:00:00.000Z",
                actor="test-suite",
                payload={"accepted": True, "index": index},
            ),
            previous,
        )
        events.append(event)
        previous = event
    return tuple(events)
