"""Ownership-safe Docker Compose orchestration for one worktree."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from scripts.bounded_process import spawn
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.identity import InstanceIdentity

_ROOT_DIGEST_LABEL: Final = "io.ci-coordinator.root-digest"
_MAX_CAPTURE_BYTES: Final = 1_048_576
_MAX_STATUS_SERVICES: Final = 16
MINIMUM_COMPOSE_VERSION: Final = (2, 39, 4)
ALLOWED_SERVICES: Final = frozenset(
    {
        "postgres",
        "database-provision",
        "migrate",
        "database-access",
        "backend",
        "frontend",
    }
)
_COMPOSE_VERSION: Final = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?")
_PORT_OUTPUT: Final = re.compile(r"(?P<host>\[[^]]+]|[^:]+):(?P<port>\d{1,5})")
_SERVICE_NAME: Final = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?")
_CONTAINER_ID: Final = re.compile(r"[0-9a-f]{12,64}")
_PROVIDER_TIMESTAMP: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z"
)
_PROVIDER_ENVIRONMENT_KEYS: Final = frozenset(
    {
        "DOCKER_CERT_PATH",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "HOME",
        "PATH",
        "TMPDIR",
        "XDG_RUNTIME_DIR",
    }
)
_OBSERVATION_TIMEOUT_SECONDS: Final = 30.0
_START_TIMEOUT_SECONDS: Final = 300.0
_STOP_TIMEOUT_SECONDS: Final = 120.0
_COMPOSE_OPERATIONS: Final = frozenset(
    {"config", "down", "logs", "port", "ps", "stop", "up", "version", "watch"}
)
_DOCKER_OPERATIONS: Final = frozenset({"exec", "inspect", "network", "ps", "volume"})
_FILE_PRESENCE_COMMAND: Final = (
    'if [ -f "$1" ]; then printf "present\\n"; '
    'elif [ -e "$1" ] || [ -L "$1" ]; then exit 2; '
    'elif [ -d "$2" ] && [ -x "$2" ]; then printf "absent\\n"; else exit 2; fi'
)


class ComposeError(RuntimeError):
    """A stable failure that never embeds provider output or secrets."""

    def __init__(self, message: str, *, reason: Reason = Reason.PROVIDER_UNAVAILABLE) -> None:
        super().__init__(message)
        self.reason = reason


class ServiceAbsent(ComposeError):
    """A successful identity query found no running container, not a missing file."""


class ProviderCommandFailed(ComposeError):
    """A failed command has no admitted observation; bounded waits may retry it."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    status: int
    stdout: str = ""


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class LocalEndpoints:
    api: str
    ui: str
    postgres: str


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    service: str
    state: str
    health: str
    exit_code: int
    container_id: str = ""


@dataclass(frozen=True, slots=True)
class ProviderInvocation:
    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ServiceRuntimeIdentity:
    container_id: str
    started_at: str


class ComposeProject:
    def __init__(
        self,
        identity: InstanceIdentity,
        environment: Mapping[str, str],
        *,
        runner: CommandRunner | None = None,
        provider_environment: Mapping[str, str] | None = None,
    ) -> None:
        self._identity = identity
        self._environment = environment
        self._runner = _run if runner is None else runner
        self._deadline: float | None = None
        source_environment = os.environ if provider_environment is None else provider_environment
        self._provider_environment = _project_provider_environment(source_environment)
        if environment.get("CI_COORDINATOR_DEV_ROOT_DIGEST") != identity.root_digest:
            raise ComposeError("development environment identity mismatch")

    def validate(self) -> None:
        self._assert_supported_compose()
        self._assert_owned_resources()
        self._checked((*self._compose_prefix(), "config", "--quiet"))

    def provider_preflight(self) -> None:
        with self.observation_budget(10):
            self._assert_supported_compose()
            self._checked(("docker", "version", "--format", "{{.Server.Version}}"))

    def assert_unallocated(self) -> None:
        if self._owned_resource_labels():
            raise ComposeError("development project identity is already allocated")

    def rendered_config(self) -> str:
        self._assert_owned_resources()
        return self._checked((*self._compose_prefix(), "config")).stdout

    def up(self) -> LocalEndpoints:
        self.validate()
        # Reapplying the database ACL requires the runtime principal to be
        # drained. Stop its client without replacing PostgreSQL or its volume.
        self._checked(
            (*self._compose_prefix(), "stop", "backend"),
            timeout_seconds=_STOP_TIMEOUT_SECONDS,
        )
        self._checked(
            (
                *self._compose_prefix(),
                "up",
                "--detach",
                "--build",
                "--wait",
                "--wait-timeout",
                "240",
            ),
            timeout_seconds=_START_TIMEOUT_SECONDS,
        )
        return self.endpoints()

    def down(self) -> None:
        self._assert_owned_resources()
        self._checked(
            (*self._compose_prefix(), "down", "--remove-orphans"),
            timeout_seconds=_STOP_TIMEOUT_SECONDS,
        )

    def reset(self) -> None:
        self._assert_owned_resources()
        self._checked(
            (*self._compose_prefix(), "down", "--volumes", "--remove-orphans"),
            timeout_seconds=_STOP_TIMEOUT_SECONDS,
        )

    def status(self) -> tuple[ServiceStatus, ...]:
        self._assert_owned_resources()
        result = self._checked((*self._compose_prefix(), "ps", "--format", "json"))
        return _parse_service_status(result.stdout)

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]:
        """Return bounded status for running and stopped project services."""
        self._assert_owned_resources()
        result = self._checked(
            (*self._compose_prefix(), "ps", "--all", "--no-trunc", "--format", "json")
        )
        return _parse_service_status(result.stdout)

    @contextmanager
    def observation_budget(self, seconds: float = 30.0) -> Iterator[None]:
        previous = self._deadline
        self._deadline = time.monotonic() + seconds
        try:
            yield
        finally:
            self._deadline = previous

    def endpoint(self, service: str) -> str:
        endpoints = {
            "backend": (3000, "http"),
            "frontend": (5173, "http"),
            "postgres": (5432, "postgresql"),
        }
        if service not in endpoints:
            raise ComposeError("unsupported endpoint service", reason=Reason.INVALID_ARGUMENT)
        port, scheme = endpoints[service]
        self._assert_owned_resources()
        return f"{scheme}://127.0.0.1:{self._published_port(service, port)}"

    def admitted_ui_endpoint(self) -> str:
        with self.observation_budget():
            before = self._service_container_id("frontend")
            started = self._owned_container_started_at(before)
            endpoint = self.endpoint("frontend")
            after = self._service_container_id("frontend")
            if before != after or started != self._owned_container_started_at(after):
                raise ComposeError("UI identity changed", reason=Reason.STALE_OBSERVATION)
            return endpoint

    def observation_signature(
        self, statuses: Sequence[ServiceStatus]
    ) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                item.service,
                item.state,
                item.health,
                item.exit_code,
                item.container_id,
                self._owned_container_started_at(item.container_id),
            )
            for item in statuses
        )

    def log_invocations(
        self,
        *,
        services: Sequence[str] = (),
        tail: int = 200,
        follow: bool = True,
    ) -> tuple[ProviderInvocation, ...]:
        if type(tail) is not int or not 0 <= tail <= 10000 or not set(services) <= ALLOWED_SERVICES:
            raise ComposeError("invalid log selection", reason=Reason.INVALID_ARGUMENT)
        with self.observation_budget():
            statuses = self.diagnostic_status()
            selected = tuple(
                item
                for item in statuses
                if item.service in ALLOWED_SERVICES and (not services or item.service in services)
            )
            if not selected or (services and set(services) != {item.service for item in selected}):
                raise ComposeError(
                    "selected service is unavailable", reason=Reason.SERVICE_UNAVAILABLE
                )
            for item in selected:
                self._owned_container_started_at(item.container_id)
            return tuple(
                ProviderInvocation(
                    argv=(
                        "docker",
                        "logs",
                        "--tail",
                        str(tail),
                        *(("--follow",) if follow and item.state == "running" else ()),
                        item.container_id,
                    ),
                    cwd=self.repo_root,
                    environment=dict(self._provider_environment),
                )
                for item in selected
            )

    def _owned_container_started_at(self, container_id: str) -> str:
        if _CONTAINER_ID.fullmatch(container_id) is None:
            raise ComposeError(
                "container identity is unavailable",
                reason=Reason.INVALID_PROVIDER_RESPONSE,
            )
        template = (
            '{{index .Config.Labels "com.docker.compose.project"}}|'
            f'{{{{index .Config.Labels "{_ROOT_DIGEST_LABEL}"}}}}|'
            "{{.State.StartedAt}}"
        )
        result = self._checked(("docker", "inspect", "--format", template, container_id))
        fields = result.stdout.strip().split("|")
        if len(fields) != 3 or fields[:2] != [
            self._identity.project_name,
            self._identity.root_digest,
        ]:
            raise ComposeError("container ownership changed", reason=Reason.FOREIGN_STATE)
        if _PROVIDER_TIMESTAMP.fullmatch(fields[2]) is None:
            raise ComposeError(
                "container start identity is invalid",
                reason=Reason.INVALID_PROVIDER_RESPONSE,
            )
        return fields[2]

    def endpoints(self) -> LocalEndpoints:
        self._assert_owned_resources()
        return LocalEndpoints(
            api=f"http://127.0.0.1:{self._published_port('backend', 3000)}",
            ui=f"http://127.0.0.1:{self._published_port('frontend', 5173)}",
            postgres=f"postgresql://127.0.0.1:{self._published_port('postgres', 5432)}",
        )

    def logs(self) -> None:
        self._assert_owned_resources()
        self._stream(
            (*self._compose_prefix(), "logs", "--follow", "--tail", "200"),
            failure="Compose log streaming failed",
        )

    def watch(self) -> None:
        self._assert_owned_resources()
        self._stream(
            (*self._compose_prefix(), "watch", "--no-up"),
            failure="Compose source watch failed",
        )

    @property
    def repo_root(self) -> Path:
        return self._identity.repo_root

    def watch_invocation(self) -> ProviderInvocation:
        self._assert_owned_resources()
        return ProviderInvocation(
            argv=(*self._compose_prefix(), "watch", "--no-up"),
            cwd=self._identity.repo_root,
            environment=dict(self._provider_environment),
        )

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
        container_id = self._service_container_id(service)
        result = self._checked(
            ("docker", "inspect", "--format", "{{.State.StartedAt}}", container_id)
        )
        started_at = result.stdout.strip()
        if _PROVIDER_TIMESTAMP.fullmatch(started_at) is None:
            raise ComposeError("Docker returned an invalid service start timestamp")
        return ServiceRuntimeIdentity(container_id=container_id, started_at=started_at)

    def service_file(self, service: str, path: str) -> bytes | None:
        if not path.startswith("/") or ".." in Path(path).parts:
            raise ComposeError("container witness path is invalid")
        container_id = self._service_container_id(service)
        present = self._checked(
            (
                "docker",
                "exec",
                container_id,
                "sh",
                "-c",
                _FILE_PRESENCE_COMMAND,
                "file-presence",
                path,
                str(Path(path).parent),
            )
        ).stdout
        if present == "absent\n":
            return None
        if present != "present\n":
            raise ComposeError("container file admission failed")
        content = self._checked(("docker", "exec", container_id, "cat", "--", path)).stdout
        return content.encode("utf-8")

    def assert_runtime_boundary(
        self,
        service: str,
        *,
        expected_uid: int,
        protected_path: str | None,
    ) -> None:
        container_id = self._service_container_id(service)
        status = self._checked(("docker", "exec", container_id, "cat", "/proc/1/status")).stdout
        fields = {
            key: value.strip()
            for line in status.splitlines()
            for key, separator, value in (line.partition(":"),)
            if separator
        }
        expected_identity = "\t".join([str(expected_uid)] * 4)
        if (
            fields.get("Uid") != expected_identity
            or fields.get("Gid") != expected_identity
            or fields.get("CapEff") != "0000000000000000"
            or fields.get("NoNewPrivs") != "1"
        ):
            raise ComposeError(f"{service} runtime privilege boundary is invalid")
        if protected_path is None:
            return
        metadata = self._checked(
            (
                "docker",
                "exec",
                container_id,
                "stat",
                "--format=%u:%g:%a:%F",
                protected_path,
            )
        ).stdout.strip()
        if metadata != f"{expected_uid}:{expected_uid}:400:regular file":
            raise ComposeError(f"{service} runtime secret boundary is invalid")

    def _stream(self, argv: Sequence[str], *, failure: str) -> None:
        try:
            result = self._runner(
                argv,
                cwd=self._identity.repo_root,
                capture=False,
                env=self._provider_environment,
                timeout_seconds=None,
            )
        except KeyboardInterrupt:
            return
        if result.status != 0:
            raise ComposeError(failure)

    def _published_port(self, service: str, container_port: int) -> int:
        result = self._checked((*self._compose_prefix(), "port", service, str(container_port)))
        match = _PORT_OUTPUT.fullmatch(result.stdout.strip())
        if match is None:
            raise ComposeError(f"Compose did not report the {service} endpoint")
        if match.group("host") != "127.0.0.1":
            raise ComposeError(f"Compose exposed the {service} endpoint beyond loopback")
        port = int(match.group("port"))
        if not 1 <= port <= 65_535:
            raise ComposeError(f"Compose reported an invalid {service} port")
        return port

    def _service_container_id(self, service: str) -> str:
        if _SERVICE_NAME.fullmatch(service) is None:
            raise ComposeError("Compose service identity is invalid")
        result = self._checked((*self._compose_prefix(), "ps", "--quiet", service))
        container_id = result.stdout.strip()
        if _CONTAINER_ID.fullmatch(container_id) is None:
            shape = (
                "empty"
                if not container_id
                else "multiple"
                if len(container_id.split()) > 1
                else "noncanonical"
            )
            observed_service = service if service in ALLOWED_SERVICES else "other"
            error_type = ServiceAbsent if not container_id else ComposeError
            raise error_type(
                "Compose returned an invalid container identity "
                f"(service={observed_service}, shape={shape})"
            )
        return container_id

    def _assert_supported_compose(self) -> None:
        result = self._checked(("docker", "compose", "version", "--short"))
        match = _COMPOSE_VERSION.fullmatch(result.stdout.strip())
        if match is None:
            raise ComposeError("Docker Compose returned an invalid version")
        version = tuple(int(component) for component in match.groups())
        if version < MINIMUM_COMPOSE_VERSION:
            required = ".".join(str(component) for component in MINIMUM_COMPOSE_VERSION)
            raise ComposeError(
                f"Docker Compose {required} or newer is required",
                reason=Reason.UNSUPPORTED_TOOL,
            )

    def _compose_prefix(self) -> tuple[str, ...]:
        return (
            "docker",
            "compose",
            "--project-name",
            self._identity.project_name,
            "--env-file",
            str(self._identity.environment_path),
            "--file",
            str(self._identity.repo_root / "compose.yaml"),
        )

    def _assert_owned_resources(self) -> None:
        labels = self._owned_resource_labels()
        if any(label != self._identity.root_digest for _, label in labels):
            foreign_kind = next(
                kind for kind, label in labels if label != self._identity.root_digest
            )
            raise ComposeError(
                f"foreign {foreign_kind} uses the derived project identity",
                reason=Reason.FOREIGN_STATE,
            )

    def _owned_resource_labels(self) -> tuple[tuple[str, str], ...]:
        project_filter = f"label=com.docker.compose.project={self._identity.project_name}"
        labels: list[tuple[str, str]] = []
        for kind, args in (
            ("container", ("ps", "--all")),
            ("network", ("network", "ls")),
            ("volume", ("volume", "ls")),
        ):
            result = self._checked(
                (
                    "docker",
                    *args,
                    "--filter",
                    project_filter,
                    "--format",
                    f'label={{{{.Label "{_ROOT_DIGEST_LABEL}"}}}}',
                )
            )
            for line in result.stdout.splitlines():
                if not line.startswith("label="):
                    raise ComposeError("Docker returned invalid resource ownership output")
                labels.append((kind, line.removeprefix("label=").strip()))
        return tuple(labels)

    def _checked(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float = _OBSERVATION_TIMEOUT_SECONDS,
    ) -> CommandResult:
        if self._deadline is not None:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise ComposeError("observation deadline exceeded", reason=Reason.PROVIDER_TIMEOUT)
            timeout_seconds = min(timeout_seconds, remaining)
        result = self._runner(
            argv,
            cwd=self._identity.repo_root,
            env=self._provider_environment,
            timeout_seconds=timeout_seconds,
        )
        if result.status != 0:
            operation = _provider_operation(argv)
            raise ProviderCommandFailed(f"{operation} failed with status {result.status}")
        return result


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    capture: bool = True,
    env: Mapping[str, str],
    timeout_seconds: float | None,
) -> CommandResult:
    if capture:
        if timeout_seconds is None:
            raise ComposeError("captured development command requires a timeout")
        result = spawn(
            argv[0],
            argv[1:],
            cwd=cwd,
            env=env,
            max_buffer=_MAX_CAPTURE_BYTES,
            timeout_seconds=timeout_seconds,
        )
        if result.error is not None:
            reason = Reason.PROVIDER_TIMEOUT if result.timed_out else Reason.PROVIDER_UNAVAILABLE
            raise ComposeError(
                "Docker Compose is unavailable, unbounded, or timed out", reason=reason
            )
        return CommandResult(-1 if result.status is None else result.status, result.stdout)
    try:
        completed = subprocess.run(  # noqa: S603 - admitted Compose provider argv
            list(argv),
            cwd=cwd,
            check=False,
            env=dict(env),
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ComposeError("Docker Compose is unavailable or timed out") from error
    return CommandResult(completed.returncode)


def _project_provider_environment(source: Mapping[str, str]) -> dict[str, str]:
    return {key: source[key] for key in _PROVIDER_ENVIRONMENT_KEYS if key in source}


def _provider_operation(argv: Sequence[str]) -> str:
    if tuple(argv[:2]) == ("docker", "compose"):
        operation = next((value for value in argv[2:] if value in _COMPOSE_OPERATIONS), None)
        return "Docker Compose" if operation is None else f"Docker Compose {operation}"
    if argv and argv[0] == "docker" and len(argv) > 1 and argv[1] in _DOCKER_OPERATIONS:
        return f"Docker {argv[1]}"
    return "Development provider command"


def _parse_service_status(raw: str) -> tuple[ServiceStatus, ...]:
    stripped = raw.strip()
    if not stripped:
        return ()
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        try:
            records: object = [json.loads(line) for line in stripped.splitlines()]
        except json.JSONDecodeError as error:
            raise ComposeError("Compose returned invalid status JSON") from error
    else:
        records = decoded if isinstance(decoded, list) else [decoded]
    if not isinstance(records, list) or len(records) > _MAX_STATUS_SERVICES:
        raise ComposeError("Compose returned an invalid service count")

    statuses: list[ServiceStatus] = []
    observed_services: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ComposeError("Compose returned an invalid service status")
        service = _status_text(record.get("Service"), "service")
        state = _status_text(record.get("State"), "state")
        health = _status_text(record.get("Health", ""), "health", allow_empty=True)
        exit_code = _status_exit_code(record.get("ExitCode"))
        container_id = record.get("ID", "")
        if not isinstance(container_id, str) or (
            container_id and _CONTAINER_ID.fullmatch(container_id) is None
        ):
            raise ComposeError(
                "Compose returned an invalid container identity",
                reason=Reason.INVALID_PROVIDER_RESPONSE,
            )
        if (
            _SERVICE_NAME.fullmatch(service) is None
            or service not in ALLOWED_SERVICES
            or service in observed_services
        ):
            raise ComposeError("Compose returned an invalid service identity")
        if state not in {
            "created",
            "restarting",
            "running",
            "removing",
            "paused",
            "exited",
            "dead",
        } or health not in {"", "starting", "healthy", "unhealthy"}:
            raise ComposeError(
                "Compose returned an invalid service state",
                reason=Reason.INVALID_PROVIDER_RESPONSE,
            )
        observed_services.add(service)
        statuses.append(
            ServiceStatus(
                service=service,
                state=state,
                health=health,
                exit_code=exit_code,
                container_id=container_id,
            )
        )
    return tuple(sorted(statuses, key=lambda status: status.service))


def _status_text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 128 or (not value and not allow_empty):
        raise ComposeError(f"Compose returned an invalid service {field}")
    return value


def _status_exit_code(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise ComposeError("Compose returned an invalid service exit code")
    return value
