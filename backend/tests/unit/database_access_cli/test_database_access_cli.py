from __future__ import annotations

import json
from io import StringIO

import pytest

import ci_coordinator.database_access_cli as database_access_cli
from ci_coordinator.database_access_cli import (
    DatabaseAccessCommand,
    parse_database_access_command,
    run_database_access,
)
from ci_coordinator.persistence.migration_settings import MIGRATION_DATABASE_DSN_ENV
from ci_coordinator.persistence.runtime_principal_access import RuntimePrincipalAccessError

_DSN = "postgresql+psycopg://migration:secret@database/coordinator"


def test_command_admission_binds_operation_role_and_environment_only_dsn() -> None:
    command = parse_database_access_command(
        ["apply", "--runtime-role", "ci_coordinator_runtime"],
        {MIGRATION_DATABASE_DSN_ENV: _DSN},
    )

    assert command == DatabaseAccessCommand(
        operation="apply",
        runtime_role="ci_coordinator_runtime",
        migration_database_dsn=_DSN,
    )
    assert "secret" not in repr(command)


@pytest.mark.parametrize(
    "argv",
    (
        (),
        ("unknown",),
        ("check",),
        ("check", "--runtime-role", "unsafe-role"),
    ),
)
def test_command_admission_rejects_invalid_arguments(argv: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        parse_database_access_command(argv, {MIGRATION_DATABASE_DSN_ENV: _DSN})


def test_run_emits_stable_success_without_connection_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[DatabaseAccessCommand] = []
    monkeypatch.setattr(
        database_access_cli,
        "execute_database_access",
        lambda command, **_kwargs: observed.append(command),
    )
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_database_access(
        ["check", "--runtime-role", "ci_coordinator_runtime"],
        environment={MIGRATION_DATABASE_DSN_ENV: _DSN},
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert len(observed) == 1
    assert json.loads(stdout.getvalue()) == {
        "code": "runtime_database_access_verified",
        "runtimeRole": "ci_coordinator_runtime",
    }
    assert stderr.getvalue() == ""
    assert "secret" not in stdout.getvalue()


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (RuntimePrincipalAccessError("runtime_role_unsafe"), "runtime_role_unsafe"),
        (
            RuntimePrincipalAccessError("runtime_role_not_drained"),
            "runtime_role_not_drained",
        ),
        (RuntimeError("postgresql://migration:secret@database"), "database_operation_failed"),
    ),
)
def test_run_redacts_expected_and_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_code: str,
) -> None:
    def reject(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(database_access_cli, "execute_database_access", reject)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_database_access(
        ["apply", "--runtime-role", "ci_coordinator_runtime"],
        environment={MIGRATION_DATABASE_DSN_ENV: _DSN},
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue()) == {"code": expected_code}
    assert "secret" not in stderr.getvalue()
