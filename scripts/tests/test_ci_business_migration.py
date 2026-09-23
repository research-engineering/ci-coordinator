from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
DSN_ENV = "CI_COORDINATOR_MIGRATION_DATABASE_DSN"


class ProcessReplaced(BaseException):
    pass


@pytest.mark.parametrize("failure", ["none", "secret", "migration", "access"])
def test_connected_migration_uses_python_and_preserves_fail_closed_order(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = YAML(typ="safe").load(ROOT / "docker/ci/connected-administrator.compose.yaml")
    command = source["services"]["migrate"]["command"]
    assert command[:2] == ["python", "-c"] and len(command) == 3
    assert "/bin/sh" not in command[2]
    calls: list[str] = []
    monkeypatch.setenv(DSN_ENV, "prior")

    def secret(path: Path) -> str:
        assert path == Path("/run/secrets/migration-dsn")
        calls.append("secret")
        if failure == "secret":
            raise FileNotFoundError
        return "postgresql+psycopg://fixture/database\n"

    def migrate(argv: list[str], *, check: bool) -> None:
        assert argv == ["alembic", "upgrade", "head"] and check
        assert os.environ[DSN_ENV] == "postgresql+psycopg://fixture/database"
        calls.append("migration")
        if failure == "migration":
            raise subprocess.CalledProcessError(9, argv)

    def access(file: str, argv: list[str]) -> None:
        assert file == "ci-coordinator-database-access"
        assert argv == [file, "apply", "--runtime-role", "ci_coordinator_runtime"]
        calls.append("access")
        if failure == "access":
            raise OSError
        raise ProcessReplaced

    monkeypatch.setattr(Path, "read_text", secret)
    monkeypatch.setattr(subprocess, "run", migrate)
    monkeypatch.setattr(os, "execvp", access)
    expected = {
        "none": ProcessReplaced,
        "secret": FileNotFoundError,
        "migration": subprocess.CalledProcessError,
        "access": OSError,
    }[failure]
    with pytest.raises(expected):
        exec(compile(command[2], "<owned-migration-bootstrap>", "exec"), {})  # noqa: S102 -- exact repository-owned Compose bootstrap
    assert (
        calls == ["secret", "migration", "access"][: {"secret": 1, "migration": 2}.get(failure, 3)]
    )
