from __future__ import annotations

import json
import os
import re
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
_COMMAND_TIMEOUT_SECONDS: Final = 600
_HEALTH_DEADLINE_MS: Final = 60_000
_BUILD_TIMING_REPORT_ID: Final = "ci-coordinator.container-build-timing"
_MAX_BUILD_TIMING_STEPS: Final = 64
_MAX_BUILD_STEP_NAME_CHARACTERS: Final = 160
# BuildKit plain progress: "#<vertex> <detail>"; vertex 0 is buildx's builder line.
_BUILD_PROGRESS_LINE: Final = re.compile(r"#(\d{1,9}) (.*)")
_BUILD_STEP_DONE: Final = re.compile(r"DONE (\d{1,9}(?:\.\d{1,9})?)s")
_BUILD_STEP_LOG: Final = re.compile(r"(\d{1,9}\.\d{1,9}) ")
_BUILD_STEP_RANK: Final = {"canceled": 0, "error": 0, "unfinished": 0, "done": 1, "cached": 2}


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


@dataclass(slots=True)
class _BuildStep:
    step_id: int
    name: str
    state: str = "unfinished"
    seconds: float | None = None
    last_log_seconds: float | None = None


class _DockerCommands:
    def __init__(self, docker: str) -> None:
        self._docker = docker

    def run(self, args: Sequence[str], label: str) -> CommandResult:
        return _require_success(
            _spawn(self._docker, args, max_buffer=_RUN_MAX_BUFFER),
            label,
        )

    def build(self, args: Sequence[str], label: str) -> None:
        started_at = _monotonic_ms()
        result = _spawn(self._docker, args, max_buffer=_RUN_MAX_BUFFER)
        _write_build_timing(
            label,
            _build_timing_record(label, result, _monotonic_ms() - started_at),
        )
        _require_success(result, label)

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
        commands.build(profile.build_args(image), f"{profile.label} image build")
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


def _build_timing_record(
    label: str,
    result: CommandResult,
    elapsed_ms: int,
) -> dict[str, JsonValue]:
    steps = _parse_build_progress(result.stderr)
    ranked = sorted(
        steps,
        key=lambda step: (
            _BUILD_STEP_RANK[step.state],
            -(step.seconds or step.last_log_seconds or 0.0),
            step.step_id,
        ),
    )
    retained = sorted(ranked[:_MAX_BUILD_TIMING_STEPS], key=lambda step: step.step_id)
    if result.error is None and result.status == 0:
        outcome = "passed"
    else:
        outcome = result.failure_kind or "failed"
    return {
        "elapsedSeconds": round(elapsed_ms / 1000, 3),
        "exitCode": result.status,
        "label": label,
        "omittedStepCount": len(steps) - len(retained),
        "outcome": outcome,
        "reportId": _BUILD_TIMING_REPORT_ID,
        "schemaVersion": 1,
        "stepCount": len(steps),
        "stepStates": {
            state: sum(1 for step in steps if step.state == state) for state in _BUILD_STEP_RANK
        },
        "steps": [
            {
                "id": step.step_id,
                "lastLogSeconds": step.last_log_seconds,
                "name": step.name,
                "seconds": step.seconds,
                "state": step.state,
            }
            for step in retained
        ],
        "timeoutSeconds": _COMMAND_TIMEOUT_SECONDS,
    }


def _parse_build_progress(progress: str) -> list[_BuildStep]:
    steps: dict[int, _BuildStep] = {}
    for line in progress.splitlines():
        match = _BUILD_PROGRESS_LINE.fullmatch(line)
        if match is None or int(match[1]) == 0:
            continue
        step_id = int(match[1])
        detail = match[2]
        step = steps.get(step_id)
        if step is None:
            steps[step_id] = _BuildStep(step_id, _bounded_step_name(detail))
            continue
        done = _BUILD_STEP_DONE.fullmatch(detail)
        log = _BUILD_STEP_LOG.match(detail)
        if done is not None:
            step.state = "done"
            step.seconds = float(done[1])
        elif detail == "CACHED":
            step.state = "cached"
        elif detail == "CANCELED":
            step.state = "canceled"
        elif detail.startswith("ERROR:"):
            step.state = "error"
        elif log is not None:
            step.last_log_seconds = max(step.last_log_seconds or 0.0, float(log[1]))
    return sorted(steps.values(), key=lambda step: step.step_id)


def _bounded_step_name(name: str) -> str:
    if len(name) <= _MAX_BUILD_STEP_NAME_CHARACTERS:
        return name
    return f"{name[: _MAX_BUILD_STEP_NAME_CHARACTERS - 3]}..."


def _write_build_timing(label: str, record: Mapping[str, JsonValue]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    document = json.dumps(record, indent=2, ensure_ascii=False)
    longest_backticks = max((len(run) for run in re.findall(r"`+", document)), default=0)
    fence = "`" * max(3, longest_backticks + 1)
    try:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(f"### {label} timing\n\n{fence}json\n{document}\n{fence}\n")
    except OSError as error:
        sys.stderr.write(f"{label} timing record failed: {error}\n")


def _require_success(result: CommandResult, label: str) -> CommandResult:
    if result.error is not None:
        raise RuntimeError(f"{label} could not start: {result.error}")
    if result.status != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{label} failed: {detail}")
    return result


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
    return spawn(
        command,
        args,
        cwd=_REPO_ROOT,
        max_buffer=max_buffer,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
    )


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _sleep_one_second() -> None:
    time.sleep(1)
