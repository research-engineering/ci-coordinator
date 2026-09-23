"""Own watch clients without inferring completion of daemon-internal effects."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import secrets
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Literal

from scripts.bounded_process import InteractiveResult, run_interactive
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    OperationBusy,
    OperationPaths,
    TerminationRequest,
    operation_paths,
)
from scripts.dev_environment.private_files import (
    PrivateFileError,
    PrivateLockBusy,
    _fsync_directory,
    atomic_write_private_text,
    bounded_private_lock,
    read_private_text,
    require_private_directory,
)

_SESSION_REASONS = frozenset(
    {
        "watch_active",
        "watch_abandoned",
        "provider_drain_unproven",
        "process_stop_unproven",
        "client_contract_unadmitted",
        "watch_client_failed",
        "watch_client_forced",
    }
)
WATCH_GRACEFUL_SECONDS = 2
WATCH_KILL_SECONDS = 1


class WatchClientContract(Enum):
    # Compose owns version/source admission for joined watchEvents/errgroup
    # completion and AdaptCmd's controlled-cancellation exit 130. This contract
    # does not cover daemon-internal transactions or normalize arbitrary 130s.
    COMPOSE_JOINED_WATCH = "compose.joined-watch.v1"


@dataclass(frozen=True, slots=True)
class CancelResult:
    state: Literal["quiescent", "blocked"]
    reason: str
    nonce: str | None = None
    recovery: str | None = field(init=False)

    def __post_init__(self) -> None:
        recovery = None
        if self.state == "blocked":
            if self.reason in {
                "watch_active",
                "control_busy",
                "operation_busy",
                "cancellation_timeout",
            }:
                recovery = "Retry bounded cancellation for the same nonce after the owner responds."
            elif self.reason == "stale_nonce":
                recovery = (
                    "The session changed; inspect the current session before requesting stop."
                )
            else:
                recovery = (
                    "Preserve this instance and its fence for owner-scoped inspection. "
                    "Continue in a separate Git worktree with a new isolated instance. "
                    "Deleting the fence or force-resetting is not admitted recovery."
                )
        object.__setattr__(self, "recovery", recovery)


@dataclass(frozen=True, slots=True)
class _SessionRecord:
    nonce: str
    phase: Literal["active", "blocked"]
    reason: str


@dataclass(frozen=True, slots=True)
class WatchInspection:
    phase: Literal["absent", "active", "blocked", "unavailable"]
    reason: str
    nonce: str | None = None


def inspect_watch(identity: InstanceIdentity) -> WatchInspection:
    """Read an admitted snapshot without creating state or claiming client death.

    No PID is read or signalled. An absent record is not mutation admission;
    callers must still use instance_operation_lock for every mutation.
    """
    paths = operation_paths(identity, create=False)
    try:
        for directory in (identity.state_home, paths.session.parent):
            if not directory.exists() and not directory.is_symlink():
                return WatchInspection("absent", "no_watch")
            require_private_directory(directory)
        record = _read_session(paths, identity)
        if record is None:
            return WatchInspection("absent", "no_watch")
        return WatchInspection(record.phase, record.reason, record.nonce)
    except (OSError, ValueError):
        return WatchInspection("unavailable", "session_unavailable")


class WatchSession:
    def __init__(
        self,
        identity: InstanceIdentity,
        paths: OperationPaths,
        descriptor: int,
        nonce: str,
    ) -> None:
        self._identity = identity
        self._paths = paths
        self._descriptor = descriptor
        self._nonce = nonce
        self._closed = False
        self._started = False
        self._process: InteractiveResult | None = None
        self._client_contract: WatchClientContract | None = None
        self._invocation_digest: str | None = None
        self._outcome = CancelResult("blocked", "watch_active", nonce)

    @property
    def nonce(self) -> str:
        return self._nonce

    @property
    def outcome(self) -> CancelResult:
        return self._outcome

    def stop_requested(self) -> bool:
        if self._closed:
            raise OperationBlocked
        if not self._paths.stop.exists() and not self._paths.stop.is_symlink():
            return False
        request = _read_bound_json(self._paths.stop, self._identity)
        if set(request) != {"version", "rootDigest", "nonce"}:
            raise PrivateFileError("watch stop request is invalid")
        return request["nonce"] == self.nonce

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        client_contract: WatchClientContract | None = None,
        timeout_seconds: float | None = None,
        graceful_seconds: float = WATCH_GRACEFUL_SECONDS,
        kill_seconds: float = WATCH_KILL_SECONDS,
        stdout_fd: int | None = None,
    ) -> InteractiveResult:
        if self._closed or self._started or not argv or cwd != self._identity.repo_root:
            raise OperationBlocked
        if (
            client_contract is not None
            and client_contract is not WatchClientContract.COMPOSE_JOINED_WATCH
        ):
            raise ValueError("watch client contract is not admitted")
        self._client_contract = client_contract
        self._invocation_digest = hashlib.sha256(
            json.dumps(
                {"argv": list(argv), "cwd": str(cwd), "env": dict(env)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self._started = True
        self._process = run_interactive(
            argv[0],
            argv[1:],
            cwd=cwd,
            env=env,
            inherited_fds=(self._descriptor,),
            stop_requested=self.stop_requested,
            timeout_seconds=timeout_seconds,
            graceful_seconds=graceful_seconds,
            kill_seconds=kill_seconds,
            stderr=subprocess.DEVNULL,
            stdout_fd=stdout_fd,
            on_interrupt=self._record_interrupted_process,
        )
        return self._process

    def _record_interrupted_process(self, result: InteractiveResult, error: BaseException) -> None:
        self._process = (
            result
            if isinstance(error, (KeyboardInterrupt, TerminationRequest))
            else replace(result, failure_kind="lifecycle")
        )

    def _completion_outcome(self) -> CancelResult:
        process = self._process
        if not self._started or (
            process is not None
            and process.started is False
            and process.returncode is None
            and process.process_group_quiescent is True
            and process.escalated is False
            and process.failure_kind in {"spawn", "cancelled"}
        ):
            return CancelResult("quiescent", "watch_not_started", self.nonce)
        reason = "watch_abandoned"
        if process is not None:
            if process.process_group_quiescent is not True or process.started is not True:
                reason = "process_stop_unproven"
            elif process.escalated is not False:
                reason = "watch_client_forced"
            elif not _admitted_client_exit(
                process.returncode, process.failure_kind, process.cancellation_signal_sent
            ):
                reason = "watch_client_failed"
            elif self._client_contract is not WatchClientContract.COMPOSE_JOINED_WATCH:
                reason = "client_contract_unadmitted"
            else:
                return CancelResult("quiescent", "watch_client_stopped", self.nonce)
        return CancelResult("blocked", reason, self.nonce)

    def _finish(self) -> None:
        self._closed = True
        current = _read_session(self._paths, self._identity)
        if current is None or current.nonce != self.nonce:
            self._outcome = CancelResult("blocked", "watch_abandoned", self.nonce)
            if current is None and self._started:
                _write_session(
                    self._paths,
                    self._identity,
                    _SessionRecord(self.nonce, "blocked", "watch_abandoned"),
                )
            raise OperationBlocked
        # Never reacquire control: a cancelling caller holds it while awaiting
        # this mutation descriptor. The durable completion precedes removal.
        outcome = self._completion_outcome()
        if outcome.state == "quiescent":
            try:
                atomic_write_private_text(
                    self._paths.completed,
                    _bound_json(
                        self._identity,
                        self.nonce,
                        reason=outcome.reason,
                        clientContract=(
                            None if self._client_contract is None else self._client_contract.value
                        ),
                        invocationDigest=self._invocation_digest,
                        process=None if self._process is None else asdict(self._process),
                    ),
                )
                self._paths.session.unlink()
                _fsync_directory(self._paths.session.parent)
            except (OSError, ValueError):
                self._outcome = CancelResult("blocked", "watch_abandoned", self.nonce)
                _write_session(
                    self._paths,
                    self._identity,
                    _SessionRecord(self.nonce, "blocked", "watch_abandoned"),
                )
                raise
            self._outcome = outcome
            return
        self._outcome = outcome
        _write_session(
            self._paths,
            self._identity,
            _SessionRecord(self.nonce, "blocked", self.outcome.reason),
        )


@contextmanager
def owned_watch_session(
    identity: InstanceIdentity,
    *,
    timeout_seconds: float = 0,
) -> Iterator[WatchSession]:
    """Publish after up; re-admit read-only, then mutate only through session.run.

    Preparation/up belongs to a preceding ordinary operation. The intervening
    gap requires fresh state/service admission and is not an atomic up+watch.
    """
    deadline = _deadline(timeout_seconds)
    paths = operation_paths(identity)
    with ExitStack() as mutation:
        try:
            with bounded_private_lock(paths.control, timeout_seconds=_remaining(deadline)):
                descriptor = mutation.enter_context(
                    bounded_private_lock(paths.mutation, timeout_seconds=_remaining(deadline))
                )
                if paths.session.exists() or paths.session.is_symlink():
                    raise OperationBlocked
                session = WatchSession(identity, paths, descriptor, secrets.token_hex(32))
                _write_session(
                    paths,
                    identity,
                    _SessionRecord(session.nonce, "active", "watch_active"),
                )
        except PrivateLockBusy as error:
            raise OperationBusy from error
        try:
            yield session
        finally:
            session._finish()


def cancel_watch(
    identity: InstanceIdentity,
    *,
    expected_nonce: str | None = None,
    timeout_seconds: float = 10,
) -> CancelResult:
    """Return with every caller-owned lock closed, before any environment lease."""
    deadline = _deadline(timeout_seconds)
    if expected_nonce is not None and not _valid_nonce(expected_nonce):
        raise ValueError("watch nonce is invalid")
    nonce: str | None = None
    try:
        paths = operation_paths(identity)
        with bounded_private_lock(paths.control, timeout_seconds=_remaining(deadline)):
            record = _read_session(paths, identity)
            nonce = None if record is None else record.nonce
            if expected_nonce is not None and record is not None and nonce != expected_nonce:
                return CancelResult("blocked", "stale_nonce", expected_nonce)
            try:
                with bounded_private_lock(paths.mutation):
                    return _inactive_result(paths, identity, record, expected_nonce)
            except PrivateLockBusy:
                if record is None:
                    return CancelResult("blocked", "operation_busy")
                if record.phase == "blocked":
                    return CancelResult("blocked", record.reason, nonce)
            atomic_write_private_text(paths.stop, _bound_json(identity, record.nonce))
            try:
                with bounded_private_lock(paths.mutation, timeout_seconds=_remaining(deadline)):
                    return _inactive_result(paths, identity, record, expected_nonce)
            except PrivateLockBusy:
                return CancelResult("blocked", "cancellation_timeout", nonce)
    except PrivateLockBusy:
        return CancelResult("blocked", "control_busy", nonce)
    except (OSError, ValueError):
        return CancelResult("blocked", "session_unavailable", nonce)


def _inactive_result(
    paths: OperationPaths,
    identity: InstanceIdentity,
    observed: _SessionRecord | None,
    expected_nonce: str | None = None,
) -> CancelResult:
    current = _read_session(paths, identity)
    if current is None:
        nonce = observed.nonce if observed is not None else expected_nonce
        if nonce is None:
            return CancelResult("quiescent", "no_watch")
        try:
            completed = _read_completion(paths, identity, nonce)
        except (OSError, ValueError):
            if observed is not None:
                _write_session(paths, identity, _SessionRecord(nonce, "blocked", "watch_abandoned"))
            raise
        if completed is not None:
            return completed
        if observed is None:
            return CancelResult("blocked", "stale_nonce", nonce)
    record = observed if current is None else current
    if record is None:
        raise AssertionError("a watch record was observed")
    if record.phase == "active" or current is None:
        record = _SessionRecord(record.nonce, "blocked", "watch_abandoned")
        _write_session(paths, identity, record)
    return CancelResult("blocked", record.reason, record.nonce)


def _read_completion(
    paths: OperationPaths, identity: InstanceIdentity, nonce: str
) -> CancelResult | None:
    if not paths.completed.exists() and not paths.completed.is_symlink():
        return None
    value = _read_bound_json(paths.completed, identity)
    if value["nonce"] != nonce:
        return None
    if set(value) != {
        "version",
        "rootDigest",
        "nonce",
        "reason",
        "clientContract",
        "invocationDigest",
        "process",
    }:
        raise PrivateFileError("watch completion is invalid")
    reason, process = value["reason"], value["process"]
    if reason == "watch_not_started" and process is None and value["invocationDigest"] is None:
        return CancelResult("quiescent", reason, nonce)
    if not isinstance(process, dict) or set(process) != {
        "returncode",
        "process_group_quiescent",
        "failure_kind",
        "escalated",
        "started",
        "cancellation_signal_sent",
    }:
        raise PrivateFileError("watch process completion is invalid")
    if not _valid_nonce(value["invocationDigest"]) or (
        process["process_group_quiescent"] is not True or process["escalated"] is not False
    ):
        raise PrivateFileError("watch client stop was not established")
    if (
        reason == "watch_not_started"
        and process["started"] is False
        and process["returncode"] is None
        and process["failure_kind"] in ("spawn", "cancelled")
    ) or (
        reason == "watch_client_stopped"
        and value["clientContract"] == WatchClientContract.COMPOSE_JOINED_WATCH.value
        and process["started"] is True
        and _admitted_client_exit(
            process["returncode"], process["failure_kind"], process["cancellation_signal_sent"]
        )
    ):
        return CancelResult("quiescent", str(reason), nonce)
    raise PrivateFileError("watch completion does not admit a clean client stop")


def _admitted_client_exit(
    returncode: object, failure_kind: object, cancellation_signal_sent: object
) -> bool:
    if type(returncode) is not int or type(cancellation_signal_sent) is not bool:
        return False
    return (returncode == 0 and failure_kind in (None, "cancelled")) or (
        returncode == 130 and failure_kind == "cancelled" and cancellation_signal_sent is True
    )


def _read_session(paths: OperationPaths, identity: InstanceIdentity) -> _SessionRecord | None:
    if not paths.session.exists() and not paths.session.is_symlink():
        return None
    value = _read_bound_json(paths.session, identity)
    nonce, phase, reason = value.get("nonce"), value.get("phase"), value.get("reason")
    if (
        set(value) != {"version", "rootDigest", "nonce", "phase", "reason"}
        or not isinstance(nonce, str)
        or not isinstance(phase, str)
        or not isinstance(reason, str)
        or phase not in {"active", "blocked"}
        or reason not in _SESSION_REASONS
        or (phase == "active") != (reason == "watch_active")
    ):
        raise PrivateFileError("watch session is invalid")
    return _SessionRecord(nonce, "active" if phase == "active" else "blocked", reason)


def _read_bound_json(path: Path, identity: InstanceIdentity) -> dict[str, object]:
    try:
        value: object = json.loads(read_private_text(path))
    except (ValueError, RecursionError) as error:
        raise PrivateFileError("watch state encoding is invalid") from error
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 1
        or value.get("rootDigest") != identity.root_digest
        or not _valid_nonce(value.get("nonce"))
    ):
        raise PrivateFileError("watch state identity is invalid")
    return value


def _bound_json(identity: InstanceIdentity, nonce: str, **fields: object) -> str:
    return (
        json.dumps(
            {"version": 1, "rootDigest": identity.root_digest, "nonce": nonce, **fields},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _write_session(
    paths: OperationPaths,
    identity: InstanceIdentity,
    record: _SessionRecord,
) -> None:
    atomic_write_private_text(
        paths.session,
        _bound_json(identity, record.nonce, phase=record.phase, reason=record.reason),
    )


def _valid_nonce(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _deadline(seconds: float) -> float:
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or not 0 <= seconds <= 120
    ):
        raise ValueError("watch admission budget must be between zero and 120 seconds")
    return time.monotonic() + seconds


def _remaining(deadline: float) -> float:
    return max(0, deadline - time.monotonic())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -S -m scripts.dev_environment.watch_session")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--state-home", type=Path)
    parser.add_argument("--nonce")
    parser.add_argument("--timeout-seconds", type=float, default=10)
    args = parser.parse_args(argv)
    try:
        identity = derive_instance_identity(args.repo_root, state_home=args.state_home)
        result = cancel_watch(
            identity,
            expected_nonce=args.nonce,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, ValueError):
        result = CancelResult("blocked", "session_unavailable")
    sys.stdout.write(json.dumps(asdict(result), sort_keys=True) + "\n")
    return 0 if result.state == "quiescent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
