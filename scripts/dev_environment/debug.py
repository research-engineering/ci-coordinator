"""Backend debug transitions under the caller's instance lock and watch exclusion."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from scripts.bounded_process import CommandFailureKind, spawn
from scripts.dev_environment.compose import (
    CommandResult,
    CommandRunner,
    ComposeError,
    LocalEndpoints,
    ProviderInvocation,
    ServiceRuntimeIdentity,
    ServiceStatus,
)
from scripts.dev_environment.debug_witness import (
    DEBUG_CONTAINER_PORT,
    DEBUG_OVERRIDE_PATH,
    backend_attach_configuration,
)
from scripts.dev_environment.lifecycle import InstanceMutationLease


class DebugError(ComposeError):
    """A debug transition failed without exposing provider output or environment."""


type DebugOperation = Literal["process", "config", "start", "port", "restore", "inspect"]


class DebugProviderFailure(DebugError):
    def __init__(
        self,
        operation: DebugOperation,
        status: int | None,
        failure_kind: CommandFailureKind | None = None,
        *,
        elapsed_ms: int = 0,
    ) -> None:
        self.status = status
        self.failure_kind = failure_kind
        admitted_status = (
            str(status) if type(status) is int and abs(status) <= 2_147_483_647 else "unknown"
        )
        super().__init__(
            f"bounded debug provider operation failed; operation={operation}; "
            f"status={admitted_status}; failure={failure_kind or 'exit'}; "
            f"elapsed_ms={min(3_600_000, max(0, elapsed_ms))}"
        )


class DebugProject(Protocol):
    @property
    def repo_root(self) -> Path: ...

    def validate(self) -> None: ...

    def watch_invocation(self) -> ProviderInvocation: ...

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]: ...

    def observation_budget(self, seconds: float = 30.0) -> AbstractContextManager[None]: ...

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity: ...

    def service_file(self, service: str, path: str) -> bytes | None: ...

    def assert_runtime_boundary(
        self, service: str, *, expected_uid: int, protected_path: str | None
    ) -> None: ...

    def endpoints(self) -> LocalEndpoints: ...


@dataclass(frozen=True, slots=True)
class DebugEndpoint:
    host: str
    port: int
    container_id: str
    state: Literal["awaiting_debugger", "ready"]
    attach_configuration: Mapping[str, object]


class BackendDebugger:
    def __init__(
        self,
        project: DebugProject,
        *,
        operation_lease: InstanceMutationLease,
        runner: CommandRunner | None = None,
    ) -> None:
        self._project = project
        self._lease = operation_lease
        self._runner = runner
        self._inherited_fds()
        self._override = project.repo_root / DEBUG_OVERRIDE_PATH
        self._override_digest = self._read_override_digest()
        self._binding = self._base_invocation()
        self._restore_required = False
        self._endpoint: DebugEndpoint | None = None
        self._runtime_identity: ServiceRuntimeIdentity | None = None
        self._adapter_endpoints_path = ""

    def start(self) -> DebugEndpoint:
        if self._restore_required:
            raise DebugError("debugger transition already started")
        self._adapter_endpoints_path = f"/tmp/ci-coordinator-debugpy-{uuid4().hex}.json"  # noqa: S108 -- unique file inside the fresh debug container
        self._project.validate()
        statuses = {item.service: item for item in self._project.diagnostic_status()}
        if any(
            service not in statuses
            or statuses[service].state != "running"
            or statuses[service].health != "healthy"
            for service in ("backend", "postgres", "frontend")
        ):
            summary = ",".join(
                f"{service}={statuses[service].state}/{statuses[service].health or 'none'}"
                if service in statuses
                else f"{service}=missing"
                for service in ("backend", "postgres", "frontend")
            )
            raise DebugError(f"debugging requires an ordinary healthy stack; services={summary}")
        before = self._project.service_runtime_identity("backend")
        self._checked(("config", "--quiet"), operation="config", debug=True)
        self._project.validate()
        invocation = self._invocation(debug=True)
        self._restore_required = True
        self._execute(
            invocation,
            ("up", "--detach", "--build", "--no-deps", "--force-recreate", "backend"),
            timeout_seconds=300,
            operation="start",
        )
        current = self._project.service_runtime_identity("backend")
        if current.container_id == before.container_id:
            raise DebugError("debug startup did not replace the ordinary backend")
        self._await_runtime_boundary(current)
        output = self._checked(
            ("port", "backend", str(DEBUG_CONTAINER_PORT)), operation="port", debug=True
        )
        match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", output.strip())
        if match is None or not 1 <= int(match.group(1)) <= 65_535:
            raise DebugError("debugger endpoint is not an admitted loopback binding")
        port = int(match.group(1))
        ports = self._ports(current.container_id)
        if ports.get(f"{DEBUG_CONTAINER_PORT}/tcp") != [
            {"HostIp": "127.0.0.1", "HostPort": str(port)}
        ]:
            raise DebugError("debugger has an unexpected published binding")
        if self._project.service_runtime_identity("backend") != current:
            raise DebugError("debugger runtime changed during endpoint observation")
        self._runtime_identity = current
        self._endpoint = DebugEndpoint(
            "127.0.0.1",
            port,
            current.container_id,
            "awaiting_debugger",
            backend_attach_configuration(self._project.repo_root, port),
        )
        return self._endpoint

    def wait_ready(self, *, timeout_seconds: float = 300) -> DebugEndpoint:
        if self._endpoint is None:
            raise DebugError("debugger session has not started")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 3_600:
            raise DebugError("debugger attach deadline is invalid")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._project.observation_budget(min(10.0, deadline - time.monotonic())):
                backend = next(
                    (
                        item
                        for item in self._project.diagnostic_status()
                        if item.service == "backend"
                    ),
                    None,
                )
            if (
                backend is None
                or backend.container_id != self._endpoint.container_id
                or backend.state != "running"
            ):
                raise DebugError(
                    "debugger backend changed or stopped while awaiting attach; "
                    + _backend_observation(backend, self._endpoint.container_id)
                )
            if backend.health == "healthy":
                if self._project.service_runtime_identity("backend") != self._runtime_identity:
                    raise DebugError("debugger runtime restarted while awaiting attach")
                self._endpoint = replace(self._endpoint, state="ready")
                return self._endpoint
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        raise DebugError("debugger attach/readiness deadline exceeded")

    def restore(self) -> LocalEndpoints | None:
        if not self._restore_required:
            return None
        self._project.validate()
        invocation = self._invocation(debug=False)
        self._execute(
            invocation,
            (
                "up",
                "--detach",
                "--build",
                "--no-deps",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "240",
                "backend",
            ),
            timeout_seconds=300,
            operation="restore",
        )
        current = self._project.service_runtime_identity("backend")
        if self._endpoint is not None and current.container_id == self._endpoint.container_id:
            raise DebugError("ordinary restore did not replace the debug backend")
        if f"{DEBUG_CONTAINER_PORT}/tcp" in self._ports(current.container_id):
            raise DebugError("ordinary backend still exposes the debugger")
        self._project.assert_runtime_boundary(
            "backend", expected_uid=65_532, protected_path="/run/ci-coordinator-secrets/runtime-dsn"
        )
        if self._project.service_runtime_identity("backend") != current:
            raise DebugError("ordinary backend changed during restore observation")
        endpoints = self._project.endpoints()
        self._restore_required = False
        self._endpoint = None
        self._runtime_identity = None
        return endpoints

    def _base_invocation(self) -> ProviderInvocation:
        # Compose owns the root, environment, project identity and resource admission.
        # Its public watch invocation supplies that binding, never a running watcher.
        invocation = self._project.watch_invocation()
        prefix = invocation.argv[:-2]
        if (
            invocation.argv[-2:] != ("watch", "--no-up")
            or invocation.cwd != self._project.repo_root
            or len(prefix) != 8
            or prefix[:3] != ("docker", "compose", "--project-name")
            or prefix[4] != "--env-file"
            or prefix[-2:] != ("--file", str(self._project.repo_root / "compose.yaml"))
        ):
            raise DebugError("Compose debug invocation binding is unavailable")
        return ProviderInvocation(prefix, invocation.cwd, dict(invocation.environment))

    def _await_runtime_boundary(self, expected: ServiceRuntimeIdentity) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with self._project.observation_budget(deadline - time.monotonic()):
                self._invocation(debug=False)
                if self._project.service_runtime_identity("backend") != expected:
                    raise DebugError("debugger changed during privilege transition")
                try:
                    self._project.assert_runtime_boundary(
                        "backend",
                        expected_uid=65_532,
                        protected_path="/run/ci-coordinator-secrets/runtime-dsn",
                    )
                except ComposeError:
                    pass
                else:
                    endpoints = self._project.service_file("backend", self._adapter_endpoints_path)
                    if self._project.service_runtime_identity("backend") != expected:
                        raise DebugError("debugger changed during listener observation")
                    if _adapter_listener_ready(endpoints) and time.monotonic() < deadline:
                        return
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        raise DebugError("debugger privilege/listener transition did not complete")

    def _invocation(self, *, debug: bool) -> ProviderInvocation:
        invocation = self._base_invocation()
        if invocation != self._binding:
            raise DebugError("Compose debug invocation binding changed")
        if not debug:
            return invocation
        if self._read_override_digest() != self._override_digest:
            raise DebugError("debug override changed during the session")
        return ProviderInvocation(
            (*invocation.argv, "--file", str(self._override)),
            invocation.cwd,
            {
                **invocation.environment,
                "CI_COORDINATOR_DEBUGPY_ENDPOINTS_FILE": self._adapter_endpoints_path,
            },
        )

    def _read_override_digest(self) -> str:
        if (
            self._override.is_symlink()
            or not self._override.is_file()
            or self._override.stat().st_size > 65_536
        ):
            raise DebugError("debug override is not an owned regular input")
        if self._override.resolve() != self._override:
            raise DebugError("debug override escaped its canonical source path")
        return hashlib.sha256(self._override.read_bytes()).hexdigest()

    def _checked(self, arguments: Sequence[str], *, operation: DebugOperation, debug: bool) -> str:
        return self._execute(
            self._invocation(debug=debug), arguments, timeout_seconds=30, operation=operation
        )

    def _execute(
        self,
        invocation: ProviderInvocation,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        operation: DebugOperation,
    ) -> str:
        started = time.monotonic_ns()
        try:
            result = self._run_provider(
                (*invocation.argv, *arguments),
                cwd=invocation.cwd,
                env=invocation.environment,
                timeout_seconds=timeout_seconds,
            )
        except DebugProviderFailure as error:
            raise DebugProviderFailure(
                operation,
                error.status,
                error.failure_kind,
                elapsed_ms=(time.monotonic_ns() - started) // 1_000_000,
            ) from error
        if result.status != 0:
            raise DebugProviderFailure(
                operation,
                result.status,
                elapsed_ms=(time.monotonic_ns() - started) // 1_000_000,
            )
        return result.stdout

    def _ports(self, container_id: str) -> dict[str, object]:
        invocation = self._invocation(debug=False)
        output = self._execute(
            ProviderInvocation(("docker",), invocation.cwd, invocation.environment),
            ("inspect", "--format", "{{json .NetworkSettings.Ports}}", container_id),
            timeout_seconds=30,
            operation="inspect",
        )
        try:
            value = json.loads(output)
        except ValueError as error:
            raise DebugError("debug port observation is invalid") from error
        if not isinstance(value, dict):
            raise DebugError("debug port observation is invalid")
        return dict(value)

    def _inherited_fds(self) -> tuple[int, ...]:
        root = self._project.repo_root
        if (
            root.resolve() != root
            or self._lease.root_digest != hashlib.sha256(str(root).encode("utf-8")).hexdigest()
        ):
            raise DebugError("debug operation lease does not match its canonical source")
        return self._lease.inherited_fds

    def _run_provider(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
    ) -> CommandResult:
        inherited_fds = self._inherited_fds()
        if self._runner is not None:
            return self._runner(argv, cwd=cwd, env=env, timeout_seconds=timeout_seconds)
        return _run(
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            inherited_fds=inherited_fds,
        )


def _adapter_listener_ready(content: bytes | None) -> bool:
    if content is None:
        return False
    if len(content) > 1024:
        raise DebugError("debugger endpoint observation exceeded its byte bound")
    # debugpy 1.8.22 prints one JSON line after both native listeners are bound.
    if not content.endswith(b"\n"):
        return False
    try:
        value = json.loads(content, object_pairs_hook=_unique_endpoint_object)
    except (UnicodeDecodeError, ValueError) as error:
        raise DebugError("debugger endpoint observation is invalid") from error
    if not isinstance(value, dict) or set(value) != {"client", "server"}:
        raise DebugError("debugger endpoint observation is invalid")
    # The container listener must use the CLI's exact bind; host publication stays loopback-only.
    for role, hosts in (("client", {"0.0.0.0"}), ("server", {"127.0.0.1", "::1"})):  # noqa: S104 -- comparison of observed data, no socket bind
        endpoint = value[role]
        if (
            not isinstance(endpoint, dict)
            or set(endpoint) != {"host", "port"}
            or not isinstance(endpoint["host"], str)
            or endpoint["host"] not in hosts
            or type(endpoint["port"]) is not int
            or not 1 <= endpoint["port"] <= 65_535
            or (role == "client" and endpoint["port"] != DEBUG_CONTAINER_PORT)
        ):
            raise DebugError("debugger endpoint observation is invalid")
    return True


def _unique_endpoint_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate endpoint key")
        value[key] = item
    return value


def _backend_observation(backend: ServiceStatus | None, expected_id: str) -> str:
    if backend is None:
        return "backend=missing"
    state = (
        backend.state
        if backend.state
        in ("created", "restarting", "running", "removing", "paused", "exited", "dead")
        else "unknown"
    )
    health = backend.health if backend.health in ("starting", "healthy", "unhealthy") else "unknown"
    if backend.health == "":
        health = "none"
    exit_code = (
        str(backend.exit_code)
        if type(backend.exit_code) is int and 0 <= backend.exit_code <= 2_147_483_647
        else "unknown"
    )
    id_width = (
        str(len(backend.container_id))
        if re.fullmatch(r"[0-9a-f]{12,64}", backend.container_id)
        else "unknown"
    )
    return (
        f"state={state}; health={health}; exit={exit_code}; "
        f"same_container={int(backend.container_id == expected_id)}; id_width={id_width}"
    )


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    capture: bool = True,
    env: Mapping[str, str],
    timeout_seconds: float | None,
    inherited_fds: Sequence[int] = (),
) -> CommandResult:
    if not capture or timeout_seconds is None:
        raise DebugError("debug provider calls require bounded capture")
    result = spawn(
        argv[0],
        argv[1:],
        cwd=cwd,
        env=env,
        max_buffer=65_536,
        timeout_seconds=timeout_seconds,
        inherited_fds=inherited_fds,
    )
    if result.status is None or result.failure_kind is not None:
        raise DebugProviderFailure("process", result.status, result.failure_kind)
    return CommandResult(result.status, result.stdout)
