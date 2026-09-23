"""PostgreSQL-backed audit replay command-line adapter."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from tempfile import TemporaryFile
from types import MappingProxyType, TracebackType
from typing import Final, Protocol, Self, TextIO

from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.audit_replay import AuditReplayRepository
from ci_coordinator.audit_replay.paged_replay import (
    AuditReplayScan,
    iter_verified_replay_events,
    scan_audit_replay,
)
from ci_coordinator.audit_replay.replay import (
    AllAuditReplayFilter,
    AuditEventIdReplayFilter,
    AuditReplayFilter,
    SubjectAuditReplayFilter,
    audit_replay_event_summary_to_mapping,
    audit_replay_filter_to_mapping,
    audit_replay_ledger_to_mapping,
    audit_replay_report_to_mapping,
)
from ci_coordinator.audit_replay.subjects import is_audit_subject_type
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork
from ci_coordinator.runtime.environment import snapshot_process_environment
from ci_coordinator.runtime_settings import admit_python_runtime

_AUDIT_EVENT_ID_PATTERN = re.compile(r"audit_[0-9a-f]{32}")
_FAILURE_CODE = "configuration_or_storage_failure"
_OUTPUT_COPY_CHARS: Final = 65_536
_EMPTY_ENVIRONMENT: Final[Mapping[str, str]] = MappingProxyType({})


class ReplayCliConfigurationError(ValueError):
    """Raised when arguments or database configuration cannot be admitted."""


class TextOutput(Protocol):
    def write(self, text: str, /) -> object: ...


class ReplayUnitOfWork(Protocol):
    @property
    def audit_events(self) -> AuditReplayRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


type EngineFactory = Callable[[str], AsyncEngine]
type UnitOfWorkFactory = Callable[[AsyncEngine], ReplayUnitOfWork]


@dataclass(frozen=True, slots=True)
class ReplayCommand:
    replay_filter: AuditReplayFilter
    include_payload: bool
    database_dsn: str = field(repr=False)


def parse_replay_command(
    argv: Sequence[str],
    environ: Mapping[str, str] = _EMPTY_ENVIRONMENT,
) -> ReplayCommand:
    database_dsn: str | None = None
    replay_filter: AuditReplayFilter | None = None
    include_payload = False
    index = 0

    while index < len(argv):
        argument = argv[index]
        if type(argument) is not str:
            raise ReplayCliConfigurationError()
        if argument == "--database-dsn":
            if database_dsn is not None:
                raise ReplayCliConfigurationError()
            database_dsn = _option_value(argv, index)
            index += 2
            continue
        if argument == "--all":
            replay_filter = _single_filter(replay_filter, AllAuditReplayFilter())
        elif argument == "--subject":
            replay_filter = _single_filter(
                replay_filter,
                _subject_filter(_option_value(argv, index)),
            )
            index += 1
        elif argument == "--audit-event-id":
            replay_filter = _single_filter(
                replay_filter,
                _audit_event_filter(_option_value(argv, index)),
            )
            index += 1
        elif argument == "--include-payload":
            include_payload = True
        else:
            raise ReplayCliConfigurationError()
        index += 1

    if replay_filter is None:
        raise ReplayCliConfigurationError()
    if database_dsn is None:
        database_dsn = environ.get("CI_COORDINATOR_DATABASE_DSN")
    if type(database_dsn) is not str or not database_dsn.strip():
        raise ReplayCliConfigurationError()
    return ReplayCommand(
        replay_filter=replay_filter,
        include_payload=include_payload,
        database_dsn=database_dsn,
    )


async def execute_replay(
    command: ReplayCommand,
    *,
    stdout: TextOutput,
    engine_factory: EngineFactory = create_postgres_engine,
    unit_of_work_factory: UnitOfWorkFactory = PostgresUnitOfWork,
) -> int:
    engine = engine_factory(command.database_dsn)
    try:
        async with unit_of_work_factory(engine) as unit_of_work:
            scan = await scan_audit_replay(
                unit_of_work.audit_events,
                command.replay_filter,
            )
        if scan.report.status != "valid" or scan.matched_event_count == 0:
            _write_json(stdout, audit_replay_report_to_mapping(scan.report))
            return _exit_code(scan.report.status)
        with TemporaryFile(mode="w+", encoding="utf-8", newline="\n") as staged:
            async with unit_of_work_factory(engine) as unit_of_work:
                await _write_streamed_report(
                    staged,
                    repository=unit_of_work.audit_events,
                    command=command,
                    scan=scan,
                )
            _publish_staged_report(staged, stdout)
        return 0
    finally:
        await engine.dispose()


async def run_replay(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextOutput,
    stderr: TextOutput,
    engine_factory: EngineFactory = create_postgres_engine,
    unit_of_work_factory: UnitOfWorkFactory = PostgresUnitOfWork,
) -> int:
    try:
        command = parse_replay_command(
            argv,
            snapshot_process_environment() if environ is None else environ,
        )
        return await execute_replay(
            command,
            stdout=stdout,
            engine_factory=engine_factory,
            unit_of_work_factory=unit_of_work_factory,
        )
    except ReplayCliConfigurationError:
        _write_json(stderr, {"code": _FAILURE_CODE})
        return 2
    except PersistenceInvariantViolation:
        _write_json(stderr, {"code": "persisted_audit_invalid"})
        return 4
    except Exception:
        _write_json(stderr, {"code": _FAILURE_CODE})
        return 2


async def _write_streamed_report(
    output: TextOutput,
    *,
    repository: AuditReplayRepository,
    command: ReplayCommand,
    scan: AuditReplayScan,
) -> None:
    output.write('{"events":[')
    separator = ""
    async for event in iter_verified_replay_events(
        repository,
        scan,
        include_payload=command.include_payload,
    ):
        output.write(separator)
        output.write(canonical_json(audit_replay_event_summary_to_mapping(event)).decode("utf-8"))
        separator = ","
    output.write('],"filter":')
    output.write(
        canonical_json(audit_replay_filter_to_mapping(scan.report.replay_filter)).decode("utf-8")
    )
    output.write(',"ledger":')
    output.write(canonical_json(audit_replay_ledger_to_mapping(scan.report.ledger)).decode("utf-8"))
    output.write(',"ok":true,"status":"valid"}\n')


def _publish_staged_report(staged: TextIO, output: TextOutput) -> None:
    staged.seek(0)
    while chunk := staged.read(_OUTPUT_COPY_CHARS):
        output.write(chunk)


def _exit_code(status: str) -> int:
    if status == "valid":
        return 0
    if status == "not-found":
        return 3
    return 4


def main(argv: Sequence[str] | None = None) -> int:
    python_rejection = admit_python_runtime()
    if python_rejection is not None:
        _write_json(
            sys.stderr,
            {
                "actualImplementation": python_rejection.actual_implementation,
                "actualVersion": python_rejection.actual_version,
                "actualBuildVariant": python_rejection.actual_build_variant,
                "code": "unsupported_python_runtime",
                "supportedImplementation": python_rejection.supported_implementation,
                "supportedVersions": list(python_rejection.supported_versions),
                "supportedBuildVariant": python_rejection.supported_build_variant,
            },
        )
        return 2
    return asyncio.run(
        run_replay(
            sys.argv[1:] if argv is None else argv,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    )


def _option_value(argv: Sequence[str], index: int) -> str:
    if index + 1 >= len(argv):
        raise ReplayCliConfigurationError()
    value = argv[index + 1]
    if type(value) is not str or not value or value.startswith("-"):
        raise ReplayCliConfigurationError()
    return value


def _single_filter(
    current: AuditReplayFilter | None,
    candidate: AuditReplayFilter,
) -> AuditReplayFilter:
    if current is not None:
        raise ReplayCliConfigurationError()
    return candidate


def _subject_filter(value: str) -> SubjectAuditReplayFilter:
    subject_type, separator, subject_id = value.partition(":")
    if not separator or not subject_id or not is_audit_subject_type(subject_type):
        raise ReplayCliConfigurationError()
    return SubjectAuditReplayFilter(subject_type=subject_type, subject_id=subject_id)


def _audit_event_filter(value: str) -> AuditEventIdReplayFilter:
    if _AUDIT_EVENT_ID_PATTERN.fullmatch(value) is None:
        raise ReplayCliConfigurationError()
    return AuditEventIdReplayFilter(audit_event_id=value)


def _write_json(output: TextOutput, value: object) -> None:
    output.write(canonical_json(value).decode("utf-8") + "\n")
