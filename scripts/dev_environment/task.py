from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TextIO, cast

from scripts.bounded_process import InteractiveResult, run_interactive, spawn
from scripts.dev_environment.diagnostics import (
    Diagnostic,
    OutputFormat,
    Reason,
    write_diagnostic,
    write_json,
)
from scripts.dev_environment.environment import (
    DependencyScope,
    EnvironmentError,
    admit_dependencies,
    dependency_lease,
    prepare_dependencies,
)
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    OperationBusy,
    TerminationRequest,
    sigterm_guard,
)
from scripts.dev_environment.watch_session import CancelResult, cancel_watch

_INSTALL_SCOPES: dict[str, tuple[DependencyScope, ...]] = {
    "install": ("backend", "frontend"),
    "install:backend": ("backend",),
    "install:frontend": ("frontend",),
}
_INSTANCE_OPERATIONS = frozenset(
    {"prepare", "up", "status", "smoke", "logs", "watch", "down", "reset", "open", "debug-backend"}
)
_BROWSER_SCRIPTS = frozenset(
    {"dev:demo", "browser:prepare", "browser:ui", "browser:debug", "browser:record"}
)
_QUALITY_TASKS = frozenset({"check", "check:portable"})
_API_TASKS = frozenset({"test:api", "test:api:deep"})
_KNOWN_TASKS = (
    frozenset(_INSTALL_SCOPES)
    | _BROWSER_SCRIPTS
    | _QUALITY_TASKS
    | _API_TASKS
    | {"browser:install", "dev:doctor", "toolchain:check"}
    | {f"dev:{operation}" for operation in _INSTANCE_OPERATIONS}
)


def run(
    argv: Sequence[str],
    *,
    repo_root: Path,
    stdout: TextIO,
    stderr: TextIO,
    state_home: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    output_format: OutputFormat = "json"
    task_name = "task"
    cancellation: CancelResult | None = None
    try:
        if not argv or argv[0] not in _KNOWN_TASKS:
            raise EnvironmentError(Reason.INVALID_ARGUMENT)
        task_name = argv[0]
        arguments = list(argv[1:])
        output_format, arguments = _extract_format(arguments)
        stop_watch = "--stop-watch" in arguments
        if stop_watch:
            if task_name not in {"dev:down", "dev:reset"} or arguments != ["--stop-watch"]:
                raise EnvironmentError(Reason.INVALID_ARGUMENT)
            arguments = []
        source = os.environ if environment is None else environment
        identity = derive_instance_identity(repo_root, state_home=state_home, environment=source)
        with sigterm_guard():
            if task_name in _INSTALL_SCOPES:
                if arguments:
                    raise EnvironmentError(Reason.INVALID_ARGUMENT)
                scopes = _INSTALL_SCOPES[task_name]
                prepare_dependencies(identity, scopes, environment=source)
                if output_format == "json":
                    write_json(stdout, {"schemaVersion": 1, "prepared": list(scopes)})
                else:
                    stdout.write("Prepared locked dependencies: " + ", ".join(scopes) + "\n")
                return 0
            if stop_watch:
                cancellation = cancel_watch(identity)
                if cancellation.state != "quiescent":
                    raise EnvironmentError(Reason.WATCH_BLOCKED)
            child_environment = _child_environment(identity, source)
            if task_name == "dev:doctor":
                return _doctor(identity, arguments, output_format, child_environment)
            with dependency_lease(identity) as descriptor:
                for scope in _scopes(task_name):
                    admit_dependencies(identity, scope)
                command, child_arguments = _command(identity, task_name, arguments, output_format)
                if cancellation is not None:
                    return _after_cancellation(
                        command,
                        child_arguments,
                        identity,
                        child_environment,
                        descriptor,
                        task_name,
                        cancellation,
                        output_format,
                        stdout,
                        stderr,
                    )
                result = run_interactive(
                    command,
                    child_arguments,
                    cwd=identity.repo_root,
                    env=child_environment,
                    inherited_fds=(descriptor,),
                    timeout_seconds=None,
                    graceful_seconds=420 if task_name == "dev:debug-backend" else 30,
                )
                return _completed_status(result)
    except (EnvironmentError, OperationBusy, OperationBlocked, OSError, ValueError) as error:
        reason = _reason(error)
        if cancellation is not None:
            _write_cancellation(stdout, cancellation, output_format, performed=False)
        write_diagnostic(stderr, Diagnostic(reason, task_name, "preparation"), output_format)
        return 2
    except KeyboardInterrupt:
        return 130
    except TerminationRequest as error:
        return 128 + error.signal_number


def _command(
    identity: InstanceIdentity,
    task_name: str,
    arguments: Sequence[str],
    output_format: OutputFormat,
) -> tuple[str, tuple[str, ...]]:
    python = str(identity.repo_root / "backend/.venv/bin/python")
    if task_name in _API_TASKS:
        return python, (
            "-m",
            "scripts.api_contract_campaign",
            "deep" if task_name.endswith(":deep") else "fast",
            *arguments,
        )
    if task_name == "toolchain:check":
        return python, (
            "-m",
            "scripts.dev_environment.toolchain",
            "--format",
            output_format,
            *arguments,
        )
    if task_name in _QUALITY_TASKS:
        return python, (
            "-m",
            "scripts.quality_plan",
            *(["portable"] if task_name == "check:portable" else []),
            *arguments,
        )
    if task_name == "browser:install":
        return "pnpm", (
            "--filter",
            "@ci-coordinator/operator-ui",
            "exec",
            "playwright",
            "install",
            "--with-deps",
            "chromium",
            *arguments,
        )
    if task_name in _BROWSER_SCRIPTS:
        return "pnpm", ("--dir", "frontend", "run", task_name, *arguments)
    return python, (
        "-m",
        "scripts.dev_environment",
        task_name.removeprefix("dev:"),
        "--format",
        output_format,
        *arguments,
    )


def _scopes(task_name: str) -> tuple[DependencyScope, ...]:
    if task_name in _QUALITY_TASKS or task_name == "browser:install":
        return "backend", "frontend"
    if task_name in _BROWSER_SCRIPTS:
        return ("frontend",)
    return ("backend",)


def _doctor(
    identity: InstanceIdentity,
    arguments: Sequence[str],
    output_format: OutputFormat,
    environment: Mapping[str, str],
) -> int:
    if not (identity.repo_root / "backend/.venv").exists():
        return _bare_doctor(identity, arguments, output_format, environment)
    try:
        with dependency_lease(identity) as descriptor:
            prefix: tuple[str, ...]
            try:
                admit_dependencies(identity, "backend")
            except EnvironmentError:
                python = sys.executable
                prefix = ("-S",)
            else:
                python = str(identity.repo_root / "backend/.venv/bin/python")
                prefix = ()
            result = run_interactive(
                python,
                (
                    *prefix,
                    "-m",
                    "scripts.dev_environment",
                    "doctor",
                    "--format",
                    output_format,
                    *arguments,
                ),
                cwd=identity.repo_root,
                env=environment,
                inherited_fds=(descriptor,),
                timeout_seconds=60,
            )
    except EnvironmentError as error:
        if error.reason != Reason.PREPARATION_IN_PROGRESS:
            raise
        return _bare_doctor(identity, arguments, output_format, environment)
    return _completed_status(result)


def _bare_doctor(
    identity: InstanceIdentity,
    arguments: Sequence[str],
    output_format: OutputFormat,
    environment: Mapping[str, str],
) -> int:
    return _completed_status(
        run_interactive(
            sys.executable,
            (
                "-S",
                "-m",
                "scripts.dev_environment",
                "doctor",
                "--format",
                output_format,
                *arguments,
            ),
            cwd=identity.repo_root,
            env=environment,
            timeout_seconds=60,
        )
    )


def _completed_status(result: InteractiveResult) -> int:
    if result.failure_kind == "timeout":
        raise EnvironmentError(Reason.PROVIDER_TIMEOUT)
    if (
        not result.process_group_quiescent
        or result.returncode is None
        or result.failure_kind not in {None, "signal"}
        or (result.failure_kind == "signal" and result.returncode >= 0)
        or result.escalated
        or not result.started
    ):
        raise EnvironmentError(Reason.PROVIDER_UNAVAILABLE)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def _after_cancellation(
    command: str,
    arguments: tuple[str, ...],
    identity: InstanceIdentity,
    environment: Mapping[str, str],
    descriptor: int,
    task_name: str,
    cancellation: CancelResult,
    output_format: OutputFormat,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    _, raw_arguments = _extract_format(arguments)
    result = spawn(
        command,
        (*raw_arguments, "--format", "json"),
        cwd=identity.repo_root,
        env=environment,
        inherited_fds=(descriptor,),
        max_buffer=1_048_576,
        timeout_seconds=180,
    )
    payload: dict[str, object] = {}
    if result.stdout:
        try:
            value = json.loads(result.stdout)
            if isinstance(value, dict):
                payload = value
        except ValueError:
            pass
    payload["watchCancellation"] = asdict(cancellation)
    if result.status == 0 and result.error is None:
        payload["operationPerformed"] = True
        if output_format == "json":
            write_json(stdout, payload)
        else:
            _write_cancellation(stdout, cancellation, output_format, performed=True)
        return 0
    if output_format == "json":
        write_json(stdout, payload)
    else:
        _write_cancellation(stdout, cancellation, output_format, performed=None)
    reason = Reason.PROVIDER_TIMEOUT if result.timed_out else Reason.PROVIDER_UNAVAILABLE
    if result.error is None:
        try:
            diagnostic = json.loads(result.stderr)
            reason = Reason(diagnostic["diagnostic"]["reason"])
        except (ValueError, KeyError, TypeError):
            pass
    write_diagnostic(stderr, Diagnostic(reason, task_name, "operation"), output_format)
    return 2


def _write_cancellation(
    stdout: TextIO,
    cancellation: CancelResult,
    output_format: OutputFormat,
    *,
    performed: bool | None,
) -> None:
    if output_format == "json":
        value: dict[str, object] = {"watchCancellation": asdict(cancellation)}
        if performed is not None:
            value["operationPerformed"] = performed
        write_json(stdout, value)
    else:
        stdout.write(f"Watch cancellation: {cancellation.state} ({cancellation.reason}).\n")
        if performed is not None:
            stdout.write(
                "Requested operation completed.\n"
                if performed
                else "Requested operation was not performed.\n"
            )


def _extract_format(arguments: Sequence[str]) -> tuple[OutputFormat, list[str]]:
    output_format: OutputFormat = "json"
    remaining: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--format":
            index += 1
            if index >= len(arguments) or arguments[index] not in {"json", "human"}:
                raise EnvironmentError(Reason.INVALID_ARGUMENT)
            output_format = cast(OutputFormat, arguments[index])
        elif argument.startswith("--format="):
            value = argument.removeprefix("--format=")
            if value not in {"json", "human"}:
                raise EnvironmentError(Reason.INVALID_ARGUMENT)
            output_format = cast(OutputFormat, value)
        else:
            remaining.append(argument)
        index += 1
    return output_format, remaining


def _child_environment(identity: InstanceIdentity, source: Mapping[str, str]) -> dict[str, str]:
    environment = dict(source)
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(key, None)
    environment["CI_COORDINATOR_DEV_STATE_HOME"] = str(identity.state_home)
    environment["MISE_AUTO_INSTALL"] = "false"
    return environment


def _reason(error: Exception) -> Reason:
    if isinstance(error, EnvironmentError):
        return error.reason
    if isinstance(error, OperationBusy):
        return Reason.OPERATION_BUSY
    if isinstance(error, OperationBlocked):
        return Reason.WATCH_BLOCKED
    return Reason.INVALID_STATE


def main(argv: Sequence[str] | None = None) -> int:
    return run(
        sys.argv[1:] if argv is None else argv,
        repo_root=Path(__file__).resolve().parents[2],
        stdout=sys.stdout,
        stderr=sys.stderr,
        environment=os.environ,
    )


if __name__ == "__main__":
    raise SystemExit(main())
