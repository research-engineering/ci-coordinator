"""Witness a native Compose client cancellation; no daemon drain inference.

Run on an already running owned project, outside any instance mutation lock.
The caller owns preparation and cleanup. In particular, a blocked outcome must
not trigger an unconditional reset. The in-flight adapter owns a separate source
barrier; the idle client witness does not modify watched inputs.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from scripts.bounded_process import InteractiveResult, run_interactive
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    OperationBusy,
    instance_operation_lock,
)
from scripts.dev_environment.watch_session import (
    WATCH_GRACEFUL_SECONDS,
    WATCH_KILL_SECONDS,
    CancelResult,
    WatchClientContract,
    cancel_watch,
    owned_watch_session,
)

if TYPE_CHECKING:
    from scripts.dev_environment.compose import (
        LocalEndpoints,
        ProviderInvocation,
        ServiceRuntimeIdentity,
        ServiceStatus,
    )

_SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]
_READY_SECONDS: Final = 20.0
_CANCEL_SECONDS: Final = 12.0
_WATCH_SECONDS: Final = 40.0
_REQUESTER_SECONDS: Final = 50.0
_GRACE_SECONDS: Final = 5.0
_KILL_SECONDS: Final = 1.0
_MAX_READY_BYTES: Final = 262_144
_MAX_LINE_BYTES: Final = 4_096
_MAX_RECEIPT_BYTES: Final = 4_096
_SERVICES: Final = frozenset({"backend", "frontend", "postgres"})
_ONESHOTS: Final = frozenset({"database-provision", "migrate", "database-access"})
# v2.39.4 cmd/compose/watch.go uses NewLogConsumer(..., false, false, false).
# pkg/compose/watch.go emits this exact line after watcher.Start and eg.Go.
# It witnesses readiness only, not batch effects or daemon transaction drain.
_READY_LINE: Final = b"Watch enabled"


class WatchClientProject(Protocol):
    @property
    def repo_root(self) -> Path: ...

    def validate(self) -> None: ...

    def observation_budget(self, seconds: float = 30.0) -> AbstractContextManager[None]: ...

    def diagnostic_status(self) -> Sequence[ServiceStatus]: ...

    def watch_invocation(self) -> ProviderInvocation: ...


class IsolatedWatchClientProject(WatchClientProject, Protocol):
    def assert_unallocated(self) -> None: ...

    def up(self) -> LocalEndpoints: ...

    def reset(self) -> None: ...

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity: ...


class WatchClientWitnessError(RuntimeError):
    """A stable diagnostic with physical process facts and a bounded recovery path."""

    def __init__(
        self,
        reason: str,
        *,
        process: InteractiveResult | None = None,
        cancellation: CancelResult | None = None,
        retained_fixture: dict[str, str] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.process = process
        self.cancellation = cancellation
        self.retained_fixture = retained_fixture


def verify_isolated_watch_client(
    identity: InstanceIdentity, project: IsolatedWatchClientProject
) -> dict[str, object]:
    """Own a separate up/watch/reset phase after the existing CI fixture reset.

    Call outside any mutation lock. Admission and cleanup use the normal owner;
    a watch fence or competing operation prevents cleanup from crossing it.
    """
    allocated = False
    try:
        with instance_operation_lock(identity):
            project.assert_unallocated()
            allocated = True
            project.up()
        verify_watch_client(identity, project)
        receipt = verify_watch_inflight(identity, project)
    except BaseException:
        if allocated:
            try:
                with instance_operation_lock(identity):
                    project.reset()
            except (OperationBlocked, OperationBusy):
                # Preserve the failing witness and its exact nonce. Neither a
                # reset nor an unlink is authorized across another live owner.
                pass
        raise
    else:
        with instance_operation_lock(identity):
            project.reset()
    return receipt


def verify_watch_inflight(
    identity: InstanceIdentity, project: IsolatedWatchClientProject
) -> dict[str, object]:
    """Qualify actual in-flight cancellation using the production stop budgets."""
    from scripts.dev_environment.watch_batch_witness import verify_watch_batch

    return verify_watch_batch(identity, project)


def verify_watch_abandonment(
    identity: InstanceIdentity, project: IsolatedWatchClientProject
) -> dict[str, object]:
    """Qualify controller death in an independently retained sibling fixture.

    The receipt names a separate checkout/state, outside the caller's temporary
    root. Its expected fence is preserved even when assertions pass; cleanup of
    the caller's healthy instance cannot remove this retained fixture.
    """
    from scripts.dev_environment.watch_batch_witness import verify_isolated_abandonment

    return verify_isolated_abandonment(identity, project.watch_invocation())


def verify_debug_provider_abandonment(
    identity: InstanceIdentity, project: IsolatedWatchClientProject
) -> dict[str, object]:
    """Qualify the actual debug provider's inherited lease after controller death.

    Call outside mutation admission. The independent sibling fixture is retained
    on expected-fault success and on an unproved provider lifetime.
    """
    from scripts.dev_environment.watch_batch_witness import verify_isolated_abandonment

    return verify_isolated_abandonment(identity, project.watch_invocation(), debug_provider=True)


def verify_watch_client(identity: InstanceIdentity, project: WatchClientProject) -> None:
    """Require a controlled clean client stop, then fresh mutation admission.

    Both provider admissions are read-only and finite. Session.run stays on the
    caller's main thread; only the finite stdlib cancellation requester runs in
    a worker. The completion acknowledgment is read after releasing the session
    and before acquiring the next mutation lock. Preparation/up and reset are
    separate caller-owned operations, with no atomic up+watch claim.

    Compose 2.39.4 AdaptCmd maps cancellation to 130 after the watch function
    returns. This witness requires our actual graceful signal, the unchanged
    physical return code (0 or 130), a quiescent group and no escalation.
    """
    if threading.current_thread() is not threading.main_thread():
        raise WatchClientWitnessError("watch_client_requires_main_thread")
    _admit_running_project(identity, project)
    with ExitStack() as resources:
        with owned_watch_session(identity) as session:
            invocation = _admit_running_project(identity, project)
            output_fd, requester = resources.enter_context(_requester(identity, session.nonce))
            process = session.run(
                invocation.argv,
                cwd=invocation.cwd,
                env=invocation.environment,
                client_contract=WatchClientContract.COMPOSE_JOINED_WATCH,
                timeout_seconds=_WATCH_SECONDS,
                graceful_seconds=WATCH_GRACEFUL_SECONDS,
                kill_seconds=WATCH_KILL_SECONDS,
                stdout_fd=output_fd,
            )
        # The requester held control while awaiting mutation. Joining it inside
        # the session would invert that order and manufacture a timeout.
        try:
            receipt = requester.result(timeout=_REQUESTER_SECONDS + _GRACE_SECONDS + _KILL_SECONDS)
        except (WatchClientWitnessError, TimeoutError) as error:
            raise WatchClientWitnessError(
                "watch_cancel_requester_failed", process=process, cancellation=session.outcome
            ) from error
        if not _clean_process(process) or session.outcome.reason != "watch_client_stopped":
            raise WatchClientWitnessError(
                "watch_client_clean_stop_unproved", process=process, cancellation=session.outcome
            )
        _require_acknowledgment(receipt, identity, session.nonce)
    with instance_operation_lock(identity):
        _admit_running_project(identity, project)


def _admit_running_project(
    identity: InstanceIdentity, project: WatchClientProject
) -> ProviderInvocation:
    if project.repo_root != identity.repo_root:
        raise WatchClientWitnessError("watch_client_root_mismatch")
    with project.observation_budget(20):
        project.validate()
        statuses = project.diagnostic_status()
        names = {item.service for item in statuses}
        if (
            len(names) != len(statuses)
            or not names >= _SERVICES
            or not names <= _SERVICES | _ONESHOTS
            or any(
                item.state != "running" or item.health != "healthy"
                for item in statuses
                if item.service in _SERVICES
            )
            or any(
                item.state != "exited" or item.exit_code != 0
                for item in statuses
                if item.service in _ONESHOTS
            )
        ):
            raise WatchClientWitnessError("watch_client_requires_running_services")
        invocation = project.watch_invocation()
        if invocation.cwd != identity.repo_root:
            raise WatchClientWitnessError("watch_client_root_mismatch")
        return invocation


def _clean_process(process: InteractiveResult) -> bool:
    return (
        process.started is True
        and type(process.returncode) is int
        and process.returncode in {0, 130}
        and process.process_group_quiescent is True
        and process.escalated is False
        and process.failure_kind == "cancelled"
        and process.cancellation_signal_sent is True
    )


@contextmanager
def _requester(
    identity: InstanceIdentity, nonce: str
) -> Iterator[tuple[int, Future[dict[str, object]]]]:
    read_fd, write_fd = os.pipe()
    stop = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="watch-client-witness")
    try:
        future = executor.submit(_request_cancel, identity, nonce, read_fd, stop)
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    try:
        yield write_fd, future
    finally:
        os.close(write_fd)
        stop.set()
        try:
            # run_interactive has finite cleanup and does not call wait().
            future.exception(timeout=_GRACE_SECONDS + _KILL_SECONDS + 2)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def _request_cancel(
    identity: InstanceIdentity, nonce: str, read_fd: int, stop: threading.Event
) -> dict[str, object]:
    try:
        # Only our fixed stdlib requester writes this bounded receipt; raw
        # provider output is consumed from the pipe and is never persisted.
        with tempfile.TemporaryFile() as receipt:
            process = run_interactive(
                str(getattr(sys, "_base_executable", sys.executable)),
                (
                    "-S",
                    "-m",
                    "scripts.dev_environment.watch_client_witness",
                    "--repo-root",
                    str(identity.repo_root),
                    "--state-home",
                    str(identity.state_home),
                    "--nonce",
                    nonce,
                    "--ready-fd",
                    str(read_fd),
                ),
                cwd=_SOURCE_ROOT,
                env={
                    key: value for key, value in os.environ.items() if not key.startswith("PYTHON")
                },
                inherited_fds=(read_fd,),
                stop_requested=stop.is_set,
                timeout_seconds=_REQUESTER_SECONDS,
                graceful_seconds=_GRACE_SECONDS,
                kill_seconds=_KILL_SECONDS,
                stderr=subprocess.DEVNULL,
                stdout_fd=receipt.fileno(),
            )
            if (
                process.returncode != 0
                or not process.process_group_quiescent
                or process.failure_kind is not None
                or process.escalated
            ):
                raise WatchClientWitnessError("watch_cancel_requester_failed", process=process)
            receipt.seek(0)
            encoded = receipt.read(_MAX_RECEIPT_BYTES + 1)
            if len(encoded) > _MAX_RECEIPT_BYTES:
                raise WatchClientWitnessError("watch_cancel_receipt_invalid")
            try:
                value: object = json.loads(encoded)
            except (ValueError, RecursionError) as error:
                raise WatchClientWitnessError("watch_cancel_receipt_invalid") from error
            if not isinstance(value, dict):
                raise WatchClientWitnessError("watch_cancel_receipt_invalid")
            return value
    finally:
        os.close(read_fd)


def _require_acknowledgment(
    receipt: dict[str, object], identity: InstanceIdentity, nonce: str
) -> None:
    expected = {
        "version": 1,
        "rootDigest": identity.root_digest,
        "nonce": nonce,
        "readiness": "compose-watch-enabled",
        "cancellation": asdict(CancelResult("quiescent", "watch_client_stopped", nonce)),
    }
    if type(receipt.get("version")) is not int or receipt != expected:
        raise WatchClientWitnessError("watch_cancel_acknowledgment_unproved")


def _wait_for_readiness(read_fd: int) -> None:
    deadline = time.monotonic() + _READY_SECONDS
    retained = bytearray()
    total = 0
    with selectors.DefaultSelector() as selector:
        selector.register(read_fd, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(read_fd, _MAX_LINE_BYTES)
            if not chunk:
                raise WatchClientWitnessError("watch_readiness_stream_closed")
            total += len(chunk)
            if total > _MAX_READY_BYTES:
                raise WatchClientWitnessError("watch_readiness_output_limit")
            retained.extend(chunk)
            while b"\n" in retained:
                line, _, remainder = retained.partition(b"\n")
                if len(line) > _MAX_LINE_BYTES:
                    raise WatchClientWitnessError("watch_readiness_output_limit")
                if line == _READY_LINE:
                    return
                retained = bytearray(remainder)
            if len(retained) > _MAX_LINE_BYTES:
                raise WatchClientWitnessError("watch_readiness_output_limit")
    raise WatchClientWitnessError("watch_readiness_timeout")


def _cancel_while_draining(identity: InstanceIdentity, nonce: str, read_fd: int) -> CancelResult:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="watch-nonce-cancel")
    future = executor.submit(
        cancel_watch, identity, expected_nonce=nonce, timeout_seconds=_CANCEL_SECONDS
    )
    deadline = time.monotonic() + _CANCEL_SECONDS + 1
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            while not future.done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WatchClientWitnessError("watch_cancel_acknowledgment_timeout")
                if not selector.get_map():
                    return future.result(timeout=remaining)
                for _key, _mask in selector.select(min(remaining, 0.05)):
                    if not os.read(read_fd, _MAX_LINE_BYTES):
                        selector.unregister(read_fd)
            return future.result(timeout=0)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watch-client-cancel-witness")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--state-home", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--ready-fd", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        identity = derive_instance_identity(args.repo_root, state_home=args.state_home)
        _wait_for_readiness(args.ready_fd)
        cancellation = _cancel_while_draining(identity, args.nonce, args.ready_fd)
        print(
            json.dumps(
                {
                    "version": 1,
                    "rootDigest": identity.root_digest,
                    "nonce": args.nonce,
                    "readiness": "compose-watch-enabled",
                    "cancellation": asdict(cancellation),
                }
            ),
            flush=True,
        )
        return 0
    except (OSError, ValueError, WatchClientWitnessError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
