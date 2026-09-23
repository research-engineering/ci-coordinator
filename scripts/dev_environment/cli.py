from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Never, cast

from scripts.dev_environment.compose import (
    ALLOWED_SERVICES,
    ComposeError,
    ComposeProject,
    LocalEndpoints,
)
from scripts.dev_environment.debug import BackendDebugger
from scripts.dev_environment.diagnostics import (
    Diagnostic,
    OutputFormat,
    Reason,
    write_diagnostic,
    write_json,
)
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    InstanceMutationLease,
    OperationBlocked,
    OperationBusy,
    TerminationRequest,
    instance_operation_lock,
    sigterm_guard,
)
from scripts.dev_environment.logs import stream_logs
from scripts.dev_environment.observation import (
    observe,
    render_human,
    unavailable_observation,
)
from scripts.dev_environment.opening import open_loopback_ui
from scripts.dev_environment.secrets import (
    ForeignInstanceStateError,
    InstanceStateError,
    admit_instance_state,
    ensure_instance_state,
)
from scripts.dev_environment.smoke import SmokeError, verify_local_stack
from scripts.dev_environment.watch_session import (
    WatchClientContract,
    cancel_watch,
    owned_watch_session,
)

_OPERATIONS = (
    "prepare",
    "up",
    "status",
    "smoke",
    "logs",
    "open",
    "watch",
    "down",
    "reset",
    "debug-backend",
)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("invalid development command arguments")


def run(
    argv: Sequence[str],
    *,
    repo_root: Path,
    stdout: object,
    stderr: object,
    state_home: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    operation = "command"
    output_format: OutputFormat = "human" if "human" in argv else "json"
    try:
        options = _parser().parse_args(argv)
        operation = options.operation
        output_format = cast(OutputFormat, options.format)
        _admit_options(options)
        identity = derive_instance_identity(
            repo_root, state_home=state_home, environment=environment
        )
        proxy = (
            None if environment is None else environment.get("CI_COORDINATOR_OUTBOUND_PROXY_URL")
        )
        with sigterm_guard():
            if options.stop_watch:
                cancellation = cancel_watch(identity)
                if cancellation.state != "quiescent":
                    _emit(
                        stdout,
                        {
                            "projectName": identity.project_name,
                            "watchCancellation": asdict(cancellation),
                            "operationPerformed": False,
                        },
                        output_format,
                    )
                    raise OperationBlocked
            if operation in {"status", "logs", "open"}:
                return _observe_command(options, identity, proxy, stdout, stderr, output_format)
            if operation == "watch":
                return _watch(identity, proxy, stdout, output_format)
            with instance_operation_lock(identity) as operation_lease:
                if operation == "debug-backend":
                    _debug(
                        options,
                        identity,
                        proxy,
                        stdout,
                        output_format,
                        operation_lease=operation_lease,
                    )
                else:
                    _mutate(operation, identity, proxy, stdout, output_format)
        return 0
    except (KeyboardInterrupt, TerminationRequest):
        return 130
    except (ComposeError, InstanceStateError, OSError, SmokeError, ValueError) as error:
        write_diagnostic(stderr, Diagnostic(_reason(error), operation), output_format)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    return run(
        sys.argv[1:] if argv is None else argv,
        repo_root=Path(__file__).resolve().parents[2],
        stdout=sys.stdout,
        stderr=sys.stderr,
        environment=os.environ,
    )


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(prog="python -m scripts.dev_environment")
    parser.add_argument("operation", choices=_OPERATIONS)
    parser.add_argument("--format", choices=("human", "json"), default="json")
    parser.add_argument("--tail", type=int)
    parser.add_argument("--service", choices=sorted(ALLOWED_SERVICES), action="append")
    parser.add_argument("--follow", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--stop-watch", action="store_true")
    parser.add_argument("--attach-timeout", type=int)
    parser.add_argument("--session-timeout", type=int)
    return parser


def _admit_options(options: argparse.Namespace) -> None:
    if options.operation != "debug-backend" and any(
        value is not None for value in (options.attach_timeout, options.session_timeout)
    ):
        raise ValueError("debug options require debug-backend")
    if any(
        value is not None and not 1 <= value <= 3600
        for value in (options.attach_timeout, options.session_timeout)
    ):
        raise ValueError("debug deadline is outside the supported bound")
    if options.operation != "logs" and any(
        value is not None for value in (options.tail, options.service, options.follow)
    ):
        raise ValueError("log options require logs")
    if options.tail is not None and not 0 <= options.tail <= 10000:
        raise ValueError("tail is outside the supported bound")
    if options.stop_watch and options.operation not in {"down", "reset"}:
        raise ValueError("watch cancellation requires down or reset")


def _project(
    identity: InstanceIdentity, proxy: str | None, *, create: bool = False
) -> ComposeProject:
    if (
        not create
        and not identity.state_directory.exists()
        and not identity.state_directory.is_symlink()
    ):
        raise ComposeError("instance is missing", reason=Reason.MISSING_STATE)
    loader = ensure_instance_state if create else admit_instance_state
    return ComposeProject(identity, loader(identity, outbound_proxy_url=proxy))


def _observe_command(
    options: argparse.Namespace,
    identity: InstanceIdentity,
    proxy: str | None,
    stdout: object,
    stderr: object,
    output_format: OutputFormat,
) -> int:
    try:
        project = _project(identity, proxy)
    except ComposeError as error:
        if options.operation != "status" or error.reason != Reason.MISSING_STATE:
            raise
        observation = unavailable_observation(identity, error.reason)
    else:
        if options.operation == "logs":
            stream_logs(
                project.log_invocations(
                    services=options.service or (),
                    tail=200 if options.tail is None else options.tail,
                    follow=True if options.follow is None else options.follow,
                ),
                identity=identity,
            )
            return 0
        if options.operation == "open":
            url = project.admitted_ui_endpoint()
            _emit(
                stdout,
                {
                    "projectName": identity.project_name,
                    "state": "opened" if open_loopback_ui(url) else "manual",
                    "endpoints": {"ui": url},
                },
                output_format,
            )
            return 0
        observation = observe(identity, project)
    _emit(stdout, observation.payload, output_format)
    if observation.reason is not None:
        write_diagnostic(
            stderr,
            Diagnostic(observation.reason, "status", "observation"),
            output_format,
        )
        return 2
    return 0


def _mutate(
    operation: str,
    identity: InstanceIdentity,
    proxy: str | None,
    stdout: object,
    output_format: OutputFormat,
) -> None:
    project = _project(identity, proxy, create=operation in {"prepare", "up"})
    if operation == "prepare":
        project.validate()
        payload: dict[str, object] = {
            "projectName": identity.project_name,
            "rootDigest": identity.root_digest,
            "state": "prepared",
        }
    elif operation == "up":
        payload = _endpoint_projection(identity.project_name, project.up())
    elif operation == "smoke":
        endpoints = project.endpoints()
        verify_local_stack(identity, endpoints, project.rendered_config())
        payload = {
            "endpoints": _endpoint_values(endpoints),
            "projectName": identity.project_name,
            "state": "verified",
        }
    elif operation == "down":
        project.down()
        payload = {"projectName": identity.project_name, "state": "stopped"}
    elif operation == "reset":
        project.reset()
        shutil.rmtree(identity.state_directory)
        payload = {"projectName": identity.project_name, "state": "reset"}
    else:
        raise AssertionError("unsupported lifecycle operation")
    _emit(stdout, payload, output_format)


def _watch(
    identity: InstanceIdentity,
    proxy: str | None,
    stdout: object,
    output_format: OutputFormat,
) -> int:
    with instance_operation_lock(identity):
        _project(identity, proxy, create=True).up()
    with owned_watch_session(identity) as session:
        project = _project(identity, proxy)
        project.validate()
        invocation = project.watch_invocation()
        _emit(
            stdout,
            _endpoint_projection(identity.project_name, project.endpoints()),
            output_format,
        )
        flush = getattr(stdout, "flush", None)
        if callable(flush):
            flush()
        session.run(
            invocation.argv,
            cwd=invocation.cwd,
            env=invocation.environment,
            client_contract=WatchClientContract.COMPOSE_JOINED_WATCH,
        )
    if session.outcome.state != "quiescent":
        raise OperationBlocked
    return 0


def _debug(
    options: argparse.Namespace,
    identity: InstanceIdentity,
    proxy: str | None,
    stdout: object,
    output_format: OutputFormat,
    *,
    operation_lease: InstanceMutationLease,
) -> None:
    debugger = BackendDebugger(_project(identity, proxy), operation_lease=operation_lease)
    try:
        endpoint = debugger.start()
        _emit(
            stdout,
            {
                "projectName": identity.project_name,
                "state": endpoint.state,
                "debugger": dict(endpoint.attach_configuration),
            },
            output_format,
        )
        flush = getattr(stdout, "flush", None)
        if callable(flush):
            flush()
        ready = debugger.wait_ready(timeout_seconds=options.attach_timeout or 300)
        _emit(stdout, {"projectName": identity.project_name, "state": ready.state}, output_format)
        threading.Event().wait(options.session_timeout or 3600)
    finally:
        endpoints = debugger.restore()
        if endpoints is not None:
            _emit(
                stdout,
                {
                    "projectName": identity.project_name,
                    "state": "restored",
                    "endpoints": _endpoint_values(endpoints),
                },
                output_format,
            )


def _reason(error: Exception) -> Reason:
    if isinstance(error, ComposeError):
        return error.reason
    if isinstance(error, OperationBusy):
        return Reason.OPERATION_BUSY
    if isinstance(error, OperationBlocked):
        return Reason.WATCH_BLOCKED
    if isinstance(error, ForeignInstanceStateError):
        return Reason.FOREIGN_STATE
    if isinstance(error, InstanceStateError):
        return Reason.INVALID_STATE
    if isinstance(error, SmokeError):
        return Reason.SERVICE_UNAVAILABLE
    if isinstance(error, OSError):
        return Reason.INVALID_STATE
    return Reason.INVALID_ARGUMENT


def _emit(output: object, payload: dict[str, object], output_format: OutputFormat) -> None:
    if output_format == "json":
        write_json(output, payload)
    elif hasattr(output, "write"):
        output.write(render_human(payload))
    else:
        raise TypeError("output must provide write")


def _endpoint_projection(project_name: str, endpoints: LocalEndpoints) -> dict[str, object]:
    return {
        "endpoints": _endpoint_values(endpoints),
        "projectName": project_name,
        "state": "running",
    }


def _endpoint_values(endpoints: LocalEndpoints) -> dict[str, str]:
    return {"api": endpoints.api, "postgres": endpoints.postgres, "ui": endpoints.ui}
