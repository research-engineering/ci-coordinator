"""Self-contained connected development stack witness for isolated CI runners."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from scripts.dev_environment.compose import (
    ComposeError,
    ComposeProject,
    LocalEndpoints,
    ServiceStatus,
)
from scripts.dev_environment.debug import BackendDebugger, DebugError, DebugProviderFailure
from scripts.dev_environment.debug_witness import DebugWitnessError, verify_backend_debugger
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    InstanceMutationLease,
    OperationBlocked,
    OperationBusy,
    TerminationRequest,
    instance_operation_lock,
    sigterm_guard,
)
from scripts.dev_environment.log_witness import verify_application_logs, verify_localhost_ssh_logs
from scripts.dev_environment.preservation_witness import StackPreservation
from scripts.dev_environment.secrets import InstanceStateError, ensure_instance_state
from scripts.dev_environment.smoke import SmokeError, verify_local_stack
from scripts.dev_environment.watch_client_witness import (
    WatchClientWitnessError,
    verify_debug_provider_abandonment,
    verify_isolated_watch_client,
    verify_watch_abandonment,
)
from scripts.dev_environment.watch_session import inspect_watch
from scripts.dev_environment.watch_witness import (
    WatchError,
    WatchProject,
    verify_material_feedback,
    verify_source_watch,
)
from scripts.mutation.detached_worktree_lifecycle import DetachedWorktreeLifecycle


class StackProject(WatchProject, Protocol):
    def assert_unallocated(self) -> None: ...

    def up(self) -> LocalEndpoints: ...

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]: ...

    def rendered_config(self) -> str: ...

    def assert_runtime_boundary(
        self,
        service: str,
        *,
        expected_uid: int,
        protected_path: str | None,
    ) -> None: ...

    def reset(self) -> None: ...


type StackVerifier = Callable[[InstanceIdentity, LocalEndpoints, str], None]
type WatchVerifier = Callable[[WatchProject], object]


class TextOutput(Protocol):
    def write(self, text: str, /) -> object: ...


class RetainedInstanceError(ComposeError):
    """An unproved cleanup must preserve the complete disposable instance."""

    def __init__(self, identity: InstanceIdentity, cause: BaseException) -> None:
        super().__init__("instance retained because cleanup admission or completion is unproved")
        self.identity = identity
        self.cause = cause


class DebugSessionFailure(DebugError):
    def __init__(self, primary: Exception, restoration: BaseException) -> None:
        self.primary = primary
        self.restoration = restoration
        super().__init__(
            f"debug session failed: {_debug_error(primary)}; "
            f"restoration failed: {_debug_error(restoration)}"
        )


def _debug_error(error: BaseException) -> str:
    return str(error) if isinstance(error, DebugProviderFailure) else "unclassified"


@contextmanager
def isolated_source_checkout(repo_root: Path) -> Iterator[tuple[Path, Path]]:
    identity: InstanceIdentity | None = None
    primary_error: BaseException | None = None
    try:
        lifecycle = DetachedWorktreeLifecycle(
            repo_root=repo_root,
            temp_prefix="ci-coordinator-stack-source-",
        )
    except (OSError, RuntimeError) as error:
        raise ComposeError("connected stack witness source checkout failed") from error
    try:
        try:
            lifecycle.add_detached_worktree("HEAD")
        except (OSError, RuntimeError) as error:
            raise ComposeError("connected stack witness source checkout failed") from error
        state_home = lifecycle.temp_root / "state"
        identity = derive_instance_identity(lifecycle.worktree, state_home=state_home)
        yield lifecycle.worktree, state_home
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if not isinstance(primary_error, RetainedInstanceError):
            try:
                if identity is None:
                    cleanup = lifecycle.cleanup()
                else:
                    # Retain the source and credentials as well as the durable
                    # session whenever the existing lifecycle owner rejects cleanup.
                    with instance_operation_lock(identity):
                        cleanup = lifecycle.cleanup()
                if cleanup.state != "passed":
                    raise ComposeError("connected stack witness source cleanup failed")
            except (OperationBlocked, OperationBusy) as error:
                if identity is None:
                    raise
                raise RetainedInstanceError(identity, primary_error or error) from error
            except (OSError, RuntimeError) as error:
                raise ComposeError("connected stack witness source cleanup failed") from error


def execute_ci_witness(
    identity: InstanceIdentity,
    project: StackProject,
    *,
    verifier: StackVerifier = verify_local_stack,
    watch_verifier: WatchVerifier = verify_source_watch,
    debug_verifier: Callable[[InstanceMutationLease], None] | None = None,
    log_verifier: Callable[[], object] | None = None,
) -> None:
    project.assert_unallocated()
    primary_error: BaseException | None = None
    try:
        with instance_operation_lock(identity):
            try:
                endpoints = project.up()
            except ComposeError as error:
                raise _startup_error(project, error) from error
            project.assert_runtime_boundary(
                "backend",
                expected_uid=65_532,
                protected_path="/run/ci-coordinator-secrets/runtime-dsn",
            )
            project.assert_runtime_boundary(
                "frontend",
                expected_uid=1_000,
                protected_path=None,
            )
            verifier(identity, endpoints, project.rendered_config())
            if log_verifier is not None:
                log_verifier()
        watch_verifier(project)
        if debug_verifier is not None:
            with instance_operation_lock(identity) as operation_lease:
                debug_verifier(operation_lease)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            with instance_operation_lock(identity):
                project.reset()
        except BaseException as error:
            raise RetainedInstanceError(identity, primary_error or error) from error


def _startup_error(project: StackProject, error: ComposeError) -> ComposeError:
    try:
        statuses = project.diagnostic_status()
    except ComposeError:
        return ComposeError(str(error))
    if not statuses:
        return ComposeError(str(error))
    summary = ",".join(
        (f"{status.service}={status.state}/{status.health or 'none'}/exit={status.exit_code}")
        for status in statuses
    )
    return ComposeError(f"{error}; services={summary}")


def run(
    *,
    repo_root: Path,
    environment: Mapping[str, str],
    stdout: TextOutput = sys.stdout,
    stderr: TextOutput = sys.stderr,
) -> int:
    if environment.get("CI") != "true":
        _write({"code": "ci_environment_required"}, stream=stderr)
        return 2
    phase = "source-checkout"
    qualification: dict[str, object] = {}
    try:
        with isolated_source_checkout(repo_root) as (source_root, state_home):
            identity = derive_instance_identity(source_root, state_home=state_home)
            phase = "instance-state"
            with instance_operation_lock(identity):
                instance_environment = ensure_instance_state(identity)
            project = ComposeProject(identity, instance_environment)

            def feedback(_project: WatchProject) -> None:
                nonlocal phase
                phase = "material-feedback"
                qualification["materialInputs"] = _verify_material_feedback(
                    identity, project, browser_runtime_root=repo_root
                )

            def logs() -> None:
                nonlocal phase
                phase = "application-logs"
                qualification["applicationLogs"] = verify_application_logs(identity, project)
                phase = "ssh-application-logs"
                qualification["sshApplicationLogs"] = verify_localhost_ssh_logs(identity, project)

            def debug(lease: InstanceMutationLease) -> None:
                nonlocal phase
                phase = "debug-session"
                _verify_debug_session(identity, project, lease)
                qualification["debugSession"] = "passed"

            phase = "stack-startup"
            execute_ci_witness(
                identity,
                project,
                watch_verifier=feedback,
                debug_verifier=debug,
                log_verifier=logs,
            )
            phase = "watch-cancellation"
            qualification["watchCancellation"] = verify_isolated_watch_client(identity, project)
            phase = "watch-controller-death"
            qualification["watchControllerDeath"] = verify_watch_abandonment(identity, project)
            phase = "debug-controller-death"
            qualification["debugControllerDeath"] = verify_debug_provider_abandonment(
                identity, project
            )
            phase = "source-cleanup"
        _write(
            {
                "projectName": identity.project_name,
                "state": "passed",
                "qualification": qualification,
            },
            stream=stdout,
        )
        return 0
    except (
        ComposeError,
        InstanceStateError,
        OSError,
        SmokeError,
        ValueError,
        WatchError,
        DebugWitnessError,
        WatchClientWitnessError,
    ) as error:
        failure: dict[str, object] = {
            "code": "connected_stack_witness_failed",
            "kind": type(error).__name__,
            "reason": str(error),
            "lastStartedPhase": phase,
            "completedPhases": sorted(qualification),
        }
        cause: BaseException = error
        retained: list[dict[str, object]] = []
        while isinstance(cause, RetainedInstanceError):
            retained.append(
                {
                    "sourceRoot": str(cause.identity.repo_root),
                    "stateHome": str(cause.identity.state_home),
                    "watch": asdict(inspect_watch(cause.identity)),
                }
            )
            cause = cause.cause
        if retained:
            failure["causeKind"] = type(cause).__name__
            if isinstance(cause, (ComposeError, WatchError, WatchClientWitnessError)):
                failure["causeReason"] = str(cause)
            failure["retainedInstance"] = retained[0]
            if len(retained) > 1:
                failure["retainedInstances"] = retained
        if isinstance(cause, (WatchClientWitnessError, WatchError)):
            if cause.process is not None:
                failure["process"] = asdict(cause.process)
            if cause.cancellation is not None:
                failure["cancellation"] = asdict(cause.cancellation)
        if isinstance(cause, WatchClientWitnessError) and cause.retained_fixture is not None:
            failure["retainedFixture"] = cause.retained_fixture
        _write(failure, stream=stderr)
        return 2


def _verify_material_feedback(
    identity: InstanceIdentity, project: ComposeProject, *, browser_runtime_root: Path
) -> tuple[str, ...]:
    with isolated_source_checkout(browser_runtime_root) as (peer_root, peer_state):
        peer_identity = derive_instance_identity(peer_root, state_home=peer_state)
        if peer_identity.root_digest == identity.root_digest:
            raise ComposeError("feedback requires a distinct running instance")
        peer: ComposeProject | None = None
        primary_error: BaseException | None = None
        try:
            with instance_operation_lock(peer_identity):
                peer = ComposeProject(peer_identity, ensure_instance_state(peer_identity))
                peer.assert_unallocated()
                try:
                    peer.up()
                except ComposeError as error:
                    raise ComposeError(
                        f"material peer startup: {_startup_error(peer, error)}",
                        reason=error.reason,
                    ) from error
                peer_preservation = StackPreservation(peer_identity, peer, all_services=True)
            with instance_operation_lock(identity):
                preservation = StackPreservation(identity, project, all_services=False)

            def preserved() -> None:
                preservation.verify()
                peer_preservation.verify()
                # Independence includes the ability to admit a B operation while
                # A is actively watching; its runtime must remain unchanged.
                with instance_operation_lock(peer_identity):
                    peer_preservation.verify()

            def reconcile() -> LocalEndpoints:
                with instance_operation_lock(identity):
                    try:
                        return project.up()
                    except ComposeError as error:
                        raise ComposeError(
                            f"material reconciliation: {_startup_error(project, error)}",
                            reason=error.reason,
                        ) from error

            return verify_material_feedback(
                project,
                identity=identity,
                browser_runtime_root=browser_runtime_root,
                preservation_check=preserved,
                reconcile=reconcile,
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if peer is not None:
                try:
                    with instance_operation_lock(peer_identity):
                        peer.reset()
                except BaseException as error:
                    raise RetainedInstanceError(peer_identity, primary_error or error) from error


def _verify_debug_session(
    identity: InstanceIdentity, project: ComposeProject, operation_lease: InstanceMutationLease
) -> None:
    debugger = BackendDebugger(project, operation_lease=operation_lease)
    primary: BaseException | None = None
    try:
        endpoint = debugger.start()
        verify_backend_debugger(project.repo_root, port=endpoint.port)
        debugger.wait_ready(timeout_seconds=60)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            restored = debugger.restore()
        except BaseException as restoration:
            if primary is not None and not isinstance(primary, Exception):
                primary.add_note(f"debug restoration failed: {_debug_error(restoration)}")
                raise primary from restoration
            if primary is not None:
                if not isinstance(restoration, Exception):
                    restoration.add_note(f"debug session failed: {_debug_error(primary)}")
                    raise
                raise DebugSessionFailure(primary, restoration) from primary
            raise
    if restored is None:
        raise ComposeError("debugger ordinary-mode restoration was not observed")
    verify_local_stack(identity, restored, project.rendered_config())


def main() -> int:
    try:
        with sigterm_guard():
            return run(
                repo_root=Path(__file__).resolve().parents[2],
                environment=os.environ,
            )
    except TerminationRequest as request:
        return 128 + request.signal_number


def _write(value: object, *, stream: TextOutput) -> None:
    stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
