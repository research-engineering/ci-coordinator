"""Exact storage and Unix identity contracts for disposable fixture consumers."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

from scripts.ci_business_witness.oracle import require
from scripts.ci_business_witness.storage import VolatileWorkspace

# This is a Linux container mount destination, independent of the host's temporary directory.
_CONTAINER_TMP = str(PurePosixPath("/") / "tmp")
_APP_SECRETS = (
    "runtime-dsn",
    "github-key.pem",
    "signing-key.pem",
    "webhook-secret",
    "metrics-token",
    "break-glass-token",
    "browser-secret",
    "reviewer-secret",
    "session-key",
)
_USERS = {
    "postgres": "",
    "database-provision": "",
    "migrate": "10001:10001",
    "keycloak": "1000",
    "proxy": "",
    "backend": "10001:10001",
    "browser": "",
    "readiness": "",
}
_RUNTIME_DIRECTORIES = {
    "postgres": (999, 999, 0o700),
    "postgres-run": (999, 999, 0o3775),
    "database-provision-postgres": (0, 0, 0o700),
    "keycloak": (1000, 0, 0o700),
    "keycloak/import": (1000, 0, 0o700),
    "proxy-cache": (101, 101, 0o700),
    "proxy-run": (0, 0, 0o700),
    "postgres-tmp": (999, 999, 0o1777),
    "database-provision-tmp": (0, 0, 0o1777),
    "migrate-tmp": (10001, 10001, 0o1777),
    "keycloak-tmp": (1000, 0, 0o1777),
    "proxy-tmp": (0, 0, 0o1777),
    "backend-tmp": (10001, 10001, 0o1777),
    "browser-tmp": (0, 0, 0o1777),
    "readiness-tmp": (0, 0, 0o1777),
}


def expected_user(service: str) -> str:
    """Return Config.User, before an image entrypoint may drop privileges."""
    require(service in _USERS, "volatile-consumer-service")
    return _USERS[service]


def expected_mounts(
    service: str, directory: Path, repository: Path
) -> dict[str, tuple[Path, bool]]:
    """Return the complete bind population; True means writable."""
    require(service in _USERS, "volatile-consumer-service")
    runtime = directory / "runtime"
    mounts = {
        "postgres": {
            "/run/secrets/postgres-superuser-password": (
                directory / "postgres-superuser-password",
                False,
            ),
            "/var/lib/postgresql": (runtime / "postgres", True),
            "/var/run/postgresql": (runtime / "postgres-run", True),
            _CONTAINER_TMP: (runtime / "postgres-tmp", True),
        },
        "database-provision": {
            "/bootstrap.sql": (
                repository / "scripts/dev_environment/bootstrap_database.sql",
                False,
            ),
            **{
                f"/run/secrets/{name}": (directory / name, False)
                for name in (
                    "postgres-superuser-password",
                    "postgres-migration-password",
                    "postgres-runtime-password",
                )
            },
            "/var/lib/postgresql": (runtime / "database-provision-postgres", True),
            _CONTAINER_TMP: (runtime / "database-provision-tmp", True),
        },
        "migrate": {
            "/run/secrets/migration-dsn": (directory / "migration-dsn", False),
            _CONTAINER_TMP: (runtime / "migrate-tmp", True),
        },
        "keycloak": {
            "/opt/keycloak/data/import/coordinator-realm.json": (directory / "realm.json", False),
            "/opt/keycloak/data": (runtime / "keycloak", True),
            "/opt/keycloak/lib/quarkus": (runtime / "keycloak-quarkus", True),
            _CONTAINER_TMP: (runtime / "keycloak-tmp", True),
        },
        "proxy": {
            "/etc/nginx/nginx.conf": (repository / "docker/ci/connected-proxy.conf", False),
            "/fixture/tls.pem": (directory / "tls.pem", False),
            "/fixture/tls-key.pem": (directory / "tls-key.pem", False),
            "/var/cache/nginx": (runtime / "proxy-cache", True),
            "/var/run": (runtime / "proxy-run", True),
            _CONTAINER_TMP: (runtime / "proxy-tmp", True),
        },
        "backend": {
            **{f"/run/secrets/{name}": (directory / name, False) for name in _APP_SECRETS},
            "/etc/ssl/certs/ca-certificates.crt": (directory / "ca-bundle.pem", False),
            _CONTAINER_TMP: (runtime / "backend-tmp", True),
        },
        "browser": {
            "/fixture/ca.pem": (directory / "ca.pem", False),
            "/fixture/browser.json": (directory / "browser.json", False),
            "/evidence": (directory / "evidence", True),
            _CONTAINER_TMP: (runtime / "browser-tmp", True),
        },
        "readiness": {
            "/fixture/ca.pem": (directory / "ca.pem", False),
            _CONTAINER_TMP: (runtime / "readiness-tmp", True),
        },
    }
    return mounts[service]


def _admitted_directory(workspace: VolatileWorkspace) -> Path:
    workspace.verify()
    directory = workspace.directory
    require(
        directory is not None
        and workspace.receipt.get("admitted") is True
        and directory.resolve() == directory
        and workspace.runtime_directory == directory / "runtime",
        "volatile-consumer-workspace",
    )
    if directory is None:
        raise AssertionError("admitted workspace has no directory")
    return directory


def prepare_runtime_directories(workspace: VolatileWorkspace) -> None:
    directory = _admitted_directory(workspace)
    layout = {
        **_RUNTIME_DIRECTORIES,
        # Seed the image's public bundled files before transferring this tree to UID 1000.
        "keycloak-quarkus": (os.getuid(), os.getgid(), 0o700),
    }
    paths = sorted(layout, key=lambda name: len(Path(name).parts))
    for name in paths:
        (directory / "runtime" / name).mkdir(mode=0o700)
    # Changing a parent to a consumer UID first would deny the producer access to its children.
    for name in reversed(paths):
        uid, gid, mode = layout[name]
        workspace.own(directory / "runtime" / name, uid=uid, gid=gid, mode=mode)


def apply_fixture_ownership(workspace: VolatileWorkspace) -> None:
    directory = _admitted_directory(workspace)
    evidence = directory / "evidence"
    require(evidence.is_dir() and not evidence.is_symlink(), "volatile-evidence-directory")
    identity = evidence.stat()
    require(
        identity.st_uid == os.getuid()
        and identity.st_gid == os.getgid()
        and stat.S_IMODE(identity.st_mode) == 0o700,
        "volatile-evidence-permissions",
    )
    ownership = {
        **dict.fromkeys((*_APP_SECRETS, "migration-dsn"), (10001, 10001, 0o400)),
        "realm.json": (1000, 0, 0o400),
        "tls-key.pem": (0, 0, 0o400),
        "browser.json": (0, 0, 0o400),
        # The official PostgreSQL entrypoint reads this again after gosu to postgres.
        "postgres-superuser-password": (999, 999, 0o400),
        "postgres-migration-password": (0, 0, 0o400),
        "postgres-runtime-password": (0, 0, 0o400),
        **dict.fromkeys(
            ("ca.pem", "tls.pem", "ca-bundle.pem", "backend.env"),
            (os.getuid(), os.getgid(), 0o444),
        ),
    }
    for name, (uid, gid, mode) in ownership.items():
        workspace.own(directory / name, uid=uid, gid=gid, mode=mode)
    # Evidence stays producer-owned: it reads browser checkpoints and writes root-readable ACKs.
