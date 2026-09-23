from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

pytestmark = pytest.mark.persistence
_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def clean_migrated_database() -> None:
    # This bootstrap witness owns an unmigrated cluster, not the runtime ledger.
    pass


@pytest.fixture(scope="module")
def provisioner(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[PostgresContainer, Path]]:
    secret_dir = tmp_path_factory.mktemp("provisioning-secrets")
    (secret_dir / "bin").mkdir()
    services = YAML(typ="safe").load(_ROOT / "compose.yaml")["services"]
    with (
        PostgresContainer(services["database-provision"]["image"])
        .with_volume_mapping(secret_dir, "/run/secrets", mode="ro")
        .with_volume_mapping(_ROOT / "scripts/dev_environment", "/bootstrap", mode="ro")
    ) as postgres:
        yield postgres, secret_dir


@pytest.mark.parametrize("listen_addresses,expected", [("", 2), ("*", 0)])
def test_compose_readiness_distinguishes_socket_only_and_tcp_servers(
    listen_addresses: str, expected: int
) -> None:
    postgres = YAML(typ="safe").load(_ROOT / "compose.yaml")["services"]["postgres"]
    probe = postgres["healthcheck"]["test"]
    assert probe[0] == "CMD"
    with (
        DockerContainer(postgres["image"])
        .with_env("POSTGRES_PASSWORD", "fixture-value-not-a-secret")
        .with_command(["postgres", "-c", f"listen_addresses={listen_addresses}"])
        .waiting_for(
            LogMessageWaitStrategy(
                "database system is ready to accept connections",
                predicate_streams_and=True,
            ).with_startup_timeout(30)
        )
    ) as database:
        socket_status, _ = database.exec(
            [
                "timeout",
                "5s",
                "pg_isready",
                "--host=/var/run/postgresql",
                "--dbname=postgres",
                "--username=postgres",
            ]
        )
        assert socket_status == 0
        status, _ = database.exec(["timeout", "5s", *probe[1:]])
        assert status == expected


@pytest.mark.parametrize("role", ["migration", "runtime"])
@pytest.mark.parametrize("invalid", ["empty", "unavailable", "partial-read"])
def test_invalid_role_file_rejects_before_any_provisioning_ddl(
    provisioner: tuple[PostgresContainer, Path], role: str, invalid: str
) -> None:
    postgres, secret_dir = provisioner
    for name in ("migration", "runtime"):
        (secret_dir / f"postgres-{name}-password").write_text("fixture-value-not-a-secret")
    invalid_file = secret_dir / f"postgres-{role}-password"
    if invalid == "empty":
        invalid_file.write_text("")
    elif invalid == "unavailable":
        invalid_file.unlink()
    else:
        cat = secret_dir / "bin" / "cat"
        cat.write_text(
            f'#!/bin/sh\nif [ "$1" = "/run/secrets/postgres-{role}-password" ]; then\n'
            '  printf fixture-value-not-a-secret\n  exit 1\nfi\nexec /bin/cat "$@"\n'
        )
        cat.chmod(0o755)
    command = [
        "timeout",
        "15s",
        "psql",
        "--no-psqlrc",
        "--username",
        postgres.username,
        "--dbname",
        postgres.dbname,
    ]

    probe_command = [*command, "--file=/bootstrap/bootstrap_database.sql"]
    if invalid == "partial-read":
        probe_command = ["env", "PATH=/run/secrets/bin:/usr/bin:/bin", *probe_command]
    status, output = postgres.exec(probe_command)

    assert status == 3
    assert b"Required role credential file is empty or unavailable" in output
    assert b"fixture-value-not-a-secret" not in output
    state_status, state = postgres.exec(
        [
            *command,
            "--tuples-only",
            "--no-align",
            "--command",
            "SELECT (SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname IN "
            "('ci_coordinator_migration', 'ci_coordinator_runtime')), "
            "(SELECT count(*) FROM pg_catalog.pg_database WHERE datname = 'ci_coordinator')",
        ]
    )
    assert state_status == 0
    assert state.strip() == b"0|0"
