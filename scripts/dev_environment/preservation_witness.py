"""Native data, credential and runtime preservation oracle for an owned CI stack."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Protocol

from scripts.bounded_process import spawn
from scripts.dev_environment.compose import ComposeError, ProviderInvocation, ServiceRuntimeIdentity
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.private_files import read_private_text, require_private_directory


class PreservedProject(Protocol):
    def watch_invocation(self) -> ProviderInvocation: ...

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity: ...


class StackPreservation:
    """Seed a private database sentinel before edits; reset owns its eventual removal."""

    def __init__(
        self, identity: InstanceIdentity, project: PreservedProject, *, all_services: bool
    ) -> None:
        self.identity = identity
        self.project = project
        self.invocation = project.watch_invocation()
        self.state_digest = self._state_digest()
        services = ("postgres", "backend", "frontend") if all_services else ("postgres",)
        self.runtime = {service: project.service_runtime_identity(service) for service in services}
        self.token = secrets.token_hex(16)
        self.table = f"feedback_preservation_{self.token}"
        self._query(
            f"CREATE TABLE {self.table}(value text); "  # noqa: S608 - identifier contains only generated hex
            f"INSERT INTO {self.table} VALUES ('{self.token}');"
        )

    def verify(self) -> None:
        if self._state_digest() != self.state_digest:
            raise ComposeError("feedback changed instance credentials or identity")
        for service, expected in self.runtime.items():
            if self.project.service_runtime_identity(service) != expected:
                raise ComposeError("feedback replaced an unrelated runtime")
        if self._query(f"SELECT value FROM {self.table};") != self.token:  # noqa: S608 - table contains only generated hex
            raise ComposeError("feedback did not preserve existing database data")

    def _state_digest(self) -> str:
        require_private_directory(self.identity.secrets_directory)
        paths = [
            self.identity.metadata_path,
            self.identity.environment_path,
            *sorted(self.identity.secrets_directory.iterdir()),
        ]
        if len(paths) > 64:
            raise ComposeError("feedback state inventory exceeds its admitted bound")
        fingerprints = {
            path.relative_to(self.identity.state_directory).as_posix(): hashlib.sha256(
                read_private_text(path).encode()
            ).hexdigest()
            for path in paths
        }
        return hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()

    def _query(self, sql: str) -> str:
        postgres = self.runtime["postgres"]
        if self.project.service_runtime_identity("postgres") != postgres:
            raise ComposeError("feedback database identity changed before observation")
        result = _query(self.invocation, postgres.container_id, sql)
        if self.project.service_runtime_identity("postgres") != postgres:
            raise ComposeError("feedback database identity changed during observation")
        return result


def _query(invocation: ProviderInvocation, container_id: str, sql: str) -> str:
    result = spawn(
        "docker",
        [
            "exec",
            "--user",
            "postgres",
            container_id,
            "psql",
            "-X",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            sql,
        ],
        cwd=invocation.cwd,
        env=invocation.environment,
        timeout_seconds=15,
        max_buffer=65_536,
    )
    if result.status != 0 or result.error is not None or result.failure_kind is not None:
        raise ComposeError("feedback database observation failed")
    return result.stdout.strip()
