from __future__ import annotations

import json
import os
import secrets
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from scripts.bounded_process import CommandResult, spawn

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

_REPO_ROOT: Final = Path(__file__).resolve().parent.parent
_RUN_MAX_BUFFER: Final = 10_000_000
_LOG_MAX_BUFFER: Final = 1_000_000
_DEFAULT_MAX_BUFFER: Final = 1_048_576
_HEALTH_DEADLINE_MS: Final = 60_000


class RunCommand(Protocol):
    def __call__(self, args: Sequence[str], label: str, /) -> CommandResult: ...


class OutputCommand(Protocol):
    def __call__(self, args: Sequence[str], label: str, /) -> str: ...


@dataclass(frozen=True, slots=True)
class ContainerRunContext:
    container: str
    image: str


@dataclass(frozen=True, slots=True)
class InspectionContext:
    container: str
    output: OutputCommand
    run: RunCommand


class Inspector(Protocol):
    def __call__(self, context: InspectionContext, /) -> Mapping[str, JsonValue] | None: ...


@dataclass(frozen=True, slots=True)
class ContainerSmokeProfile:
    build_args: Callable[[str], Sequence[str]]
    container_prefix: str
    image_prefix: str
    inspect: Inspector
    label: str
    non_claims: tuple[str, ...]
    report_id: str
    run_args: Callable[[ContainerRunContext], Sequence[str]]


class _DockerCommands:
    def __init__(self, docker: str) -> None:
        self._docker = docker

    def run(self, args: Sequence[str], label: str) -> CommandResult:
        result = _spawn(self._docker, args, max_buffer=_RUN_MAX_BUFFER)
        if result.error is not None:
            raise RuntimeError(f"{label} could not start: {result.error}")
        if result.status != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"{label} failed: {detail}")
        return result

    def output(self, args: Sequence[str], label: str) -> str:
        return self.run(args, label).stdout.strip()

    def print_logs(self, container: str) -> None:
        result = _spawn(
            self._docker,
            ["logs", container],
            max_buffer=_LOG_MAX_BUFFER,
        )
        if result.stdout:
            sys.stderr.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)

    def cleanup(
        self,
        args: Sequence[str],
        label: str,
        failures: list[str],
    ) -> None:
        result = _spawn(self._docker, args, max_buffer=_DEFAULT_MAX_BUFFER)
        if result.error is not None:
            failures.append(f"{label} could not start: {result.error}")
        elif result.status != 0:
            detail = (result.stderr or result.stdout).strip()
            failures.append(f"{label} failed: {detail}")


def run_container_smoke(profile: ContainerSmokeProfile) -> bool:
    docker = os.environ.get("CI_COORDINATOR_DOCKER_BIN")
    if docker is None:
        docker = "docker"
    suffix = f"{os.getpid()}-{secrets.token_hex(8)}"
    image = f"{profile.image_prefix}-{suffix}"
    container = f"{profile.container_prefix}-{suffix}"
    commands = _DockerCommands(docker)
    image_created = False
    container_created = False
    failure: str | None = None
    success: Mapping[str, JsonValue] | None = None

    try:
        commands.run(profile.build_args(image), f"{profile.label} image build")
        image_created = True
        commands.run(
            profile.run_args(
                ContainerRunContext(
                    container=container,
                    image=image,
                )
            ),
            f"{profile.label} startup",
        )
        container_created = True
        _await_healthy(commands.output, container)
        success = profile.inspect(
            InspectionContext(
                container=container,
                output=commands.output,
                run=commands.run,
            )
        )
    except Exception as error:
        failure = str(error)
        if container_created:
            commands.print_logs(container)
    finally:
        cleanup_failures: list[str] = []
        if container_created:
            commands.cleanup(
                ["rm", "--force", container],
                "container cleanup",
                cleanup_failures,
            )
        if image_created:
            commands.cleanup(
                ["image", "rm", "--force", image],
                "image cleanup",
                cleanup_failures,
            )
        if cleanup_failures:
            failure_parts = [part for part in [failure, *cleanup_failures] if part]
            failure = "; ".join(failure_parts)

    if failure is not None:
        sys.stderr.write(f"{failure}\n")
        return False
    if success is None:
        sys.stderr.write(f"{profile.label} completed without a result\n")
        return False

    report: dict[str, JsonValue] = dict(success)
    report.update(
        {
            "nonClaims": list(profile.non_claims),
            "reportId": profile.report_id,
            "reportKind": profile.report_id,
            "schemaVersion": 1,
            "state": "passed",
        }
    )
    sys.stdout.write(f"{json.dumps(report, indent=2, ensure_ascii=False)}\n")
    return True


def _await_healthy(output: OutputCommand, container: str) -> None:
    started_at = _monotonic_ms()
    previous = started_at
    deadline = started_at + _HEALTH_DEADLINE_MS
    while True:
        current = _monotonic_ms()
        if current < previous:
            raise RuntimeError("monotonic clock regressed")
        if current >= deadline:
            break
        previous = current
        status = output(
            ["inspect", "--format", "{{.State.Health.Status}}", container],
            "container health state",
        )
        if status == "healthy":
            return
        if status == "unhealthy":
            raise RuntimeError("container became unhealthy")
        _sleep_one_second()
    raise RuntimeError("container health deadline exceeded")


def _spawn(
    command: str,
    args: Sequence[str],
    *,
    max_buffer: int,
) -> CommandResult:
    return spawn(command, args, cwd=_REPO_ROOT, max_buffer=max_buffer)


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _sleep_one_second() -> None:
    time.sleep(1)
