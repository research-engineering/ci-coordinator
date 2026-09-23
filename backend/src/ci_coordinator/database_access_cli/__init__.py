"""Deployment CLI for exact PostgreSQL runtime-principal access."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Never, Protocol

import psycopg
from psycopg import Connection

from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.migration_settings import (
    MIGRATION_DATABASE_URL_PLACEHOLDER,
    resolve_migration_database_url,
    to_psycopg_connection_string,
)
from ci_coordinator.persistence.runtime_principal_access import (
    DatabaseRow,
    RuntimePrincipalAccessError,
    admit_runtime_role_name,
    apply_runtime_principal_access,
    check_runtime_principal_access,
)
from ci_coordinator.runtime.environment import snapshot_process_environment
from ci_coordinator.runtime_settings import admit_python_runtime

type DatabaseAccessOperation = Literal["apply", "check"]
type ConnectionFactory = Callable[[str], Connection[DatabaseRow]]
_EMPTY_ENVIRONMENT: Final[Mapping[str, str]] = {}


class TextOutput(Protocol):
    def write(self, text: str, /) -> object: ...


class DatabaseAccessCliError(ValueError):
    """Invalid local command or environment configuration."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise DatabaseAccessCliError(message)


@dataclass(frozen=True, slots=True)
class DatabaseAccessCommand:
    operation: DatabaseAccessOperation
    runtime_role: str
    migration_database_dsn: str = field(repr=False)


def parse_database_access_command(
    argv: Sequence[str],
    environment: Mapping[str, str] = _EMPTY_ENVIRONMENT,
) -> DatabaseAccessCommand:
    arguments = _parser().parse_args(argv)
    role = admit_runtime_role_name(arguments.runtime_role)
    dsn = resolve_migration_database_url(MIGRATION_DATABASE_URL_PLACEHOLDER, environment)
    return DatabaseAccessCommand(
        operation=arguments.operation,
        runtime_role=role,
        migration_database_dsn=dsn,
    )


def execute_database_access(
    command: DatabaseAccessCommand,
    *,
    connection_factory: ConnectionFactory = psycopg.connect,
) -> None:
    dsn = to_psycopg_connection_string(command.migration_database_dsn)
    with connection_factory(dsn) as connection:
        if command.operation == "apply":
            apply_runtime_principal_access(connection, command.runtime_role)
        else:
            check_runtime_principal_access(connection, command.runtime_role)


def run_database_access(
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    stdout: TextOutput,
    stderr: TextOutput,
    connection_factory: ConnectionFactory = psycopg.connect,
) -> int:
    try:
        command = parse_database_access_command(
            argv,
            snapshot_process_environment() if environment is None else environment,
        )
        execute_database_access(command, connection_factory=connection_factory)
    except (DatabaseAccessCliError, RuntimePrincipalAccessError, ValueError) as error:
        code = error.code if isinstance(error, RuntimePrincipalAccessError) else "invalid_command"
        _write_json(stderr, {"code": code})
        return 2
    except Exception:
        _write_json(stderr, {"code": "database_operation_failed"})
        return 2
    _write_json(
        stdout,
        {
            "code": (
                "runtime_database_access_applied"
                if command.operation == "apply"
                else "runtime_database_access_verified"
            ),
            "runtimeRole": command.runtime_role,
        },
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    if admit_python_runtime() is not None:
        _write_json(sys.stderr, {"code": "unsupported_python_runtime"})
        return 2
    return run_database_access(
        sys.argv[1:] if argv is None else argv,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(prog="ci-coordinator-database-access")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("apply", "check"):
        command = subparsers.add_parser(operation)
        command.add_argument("--runtime-role", required=True)
    return parser


def _write_json(output: TextOutput, value: object) -> None:
    output.write(canonical_json(value).decode("utf-8") + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
