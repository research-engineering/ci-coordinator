"""Bounded process-group execution for local consumer controls."""

from __future__ import annotations

import errno
import json
import math
import os
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final, cast

from ci_coordinator.consumer_contract_lab.bootstrap import (
    LifetimeCancelled as LifetimeCancelled,
)
from ci_coordinator.consumer_contract_lab.bootstrap import (
    LifetimeInvocation as LifetimeInvocation,
)
from ci_coordinator.consumer_contract_lab.bootstrap import (
    LifetimeScope as LifetimeScope,
)
from ci_coordinator.consumer_contract_lab.bootstrap import (
    _close_capture,
    _defer_cancellation,
    _group_alive,
    _interruptible_cancellation,
    _ReceivedLifetime,
    _StopSchedule,
    _unique_object,
)
from ci_coordinator.consumer_contract_lab.bootstrap import (
    assert_lifetime_running as assert_lifetime_running,
)
from ci_coordinator.consumer_contract_lab.bootstrap import (
    borrowed_lifetime as borrowed_lifetime,
)
from ci_coordinator.consumer_contract_lab.bootstrap import (
    current_lifetime as current_lifetime,
)
from ci_coordinator.consumer_contract_lab.bootstrap import (
    lifetime_invocation as lifetime_invocation,
)

_FORCE_KILL_DELAY_SECONDS: Final = 1.0
_POLL_INTERVAL_SECONDS: Final = 0.02
_READ_CHUNK_BYTES: Final = 65_536
_SUPPORTED_PLATFORMS: Final = frozenset({"darwin", "linux"})
LAB_LIFETIME_PROTOCOL: Final = 1
_LIFETIME_ARGUMENT: Final = "--managed-lab-lifetime"
_DELETE_TREE: Final = "import shutil,sys;shutil.rmtree(sys.argv[1])"


class ConsumerLabCleanupError(RuntimeError):
    pass


def _decode_lifetime(payload: str) -> _ReceivedLifetime:
    if sys.platform not in _SUPPORTED_PLATFORMS or len(payload.encode()) > os.sysconf("SC_ARG_MAX"):
        raise ValueError("unsupported lifetime transport")
    import fcntl

    value = json.loads(payload, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {
        "version",
        "leases",
        "stopFd",
        "stopGrace",
        "deadline",
    }:
        raise ValueError("invalid lifetime fields")
    deadline, grace, reader, rows = (
        value[key] for key in ("deadline", "stopGrace", "stopFd", "leases")
    )
    if type(value["version"]) is not int or value["version"] != LAB_LIFETIME_PROTOCOL:
        raise ValueError("unsupported lifetime version")
    if deadline is not None and (
        type(deadline) not in {int, float}
        or not math.isfinite(deadline)
        or deadline <= time.monotonic()
    ):
        raise ValueError("invalid lifetime deadline")
    if type(grace) not in {int, float} or not math.isfinite(grace) or not 0 < grace <= 1:
        raise ValueError("invalid lifetime stop allowance")
    if type(reader) is not int or reader < 3 or not stat.S_ISFIFO(os.fstat(reader).st_mode):
        raise ValueError("invalid lifetime stop reader")
    flags = fcntl.fcntl(reader, fcntl.F_GETFL)
    if flags & os.O_ACCMODE != os.O_RDONLY or not flags & os.O_NONBLOCK:
        raise ValueError("invalid lifetime stop direction")
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing lifetime leases")
    descriptors = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"fd", "device", "inode"}
            or any(type(item) is not int for item in row.values())
        ):
            raise ValueError("invalid lifetime lease")
        descriptor = row["fd"]
        if descriptor < 3:
            raise ValueError("stdio is not a lifetime lease")
        identity = os.fstat(descriptor)
        if fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR:
            raise ValueError("lifetime lease access changed")
        if not stat.S_ISREG(identity.st_mode) or (identity.st_dev, identity.st_ino) != (
            row["device"],
            row["inode"],
        ):
            raise ValueError("lifetime lease identity changed")
        descriptors.append(descriptor)
    if len(set((reader, *descriptors))) != len(descriptors) + 1:
        raise ValueError("duplicate lifetime descriptor")
    return _ReceivedLifetime(tuple(descriptors), deadline, reader, grace)


@contextmanager
def received_lifetime(payload: str) -> Iterator[_ReceivedLifetime]:
    scope = _decode_lifetime(payload)
    parent = current_lifetime()
    if parent is not None:
        raise ValueError("exec receiver cannot replace an active borrowed scope")
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    primary: BaseException | None = None
    try:
        for number in previous:
            signal.signal(number, scope.signal)
        with borrowed_lifetime(scope):
            assert_lifetime_running()
            yield scope
    except BaseException as error:
        primary = error
        raise
    finally:
        close_error: BaseException | None = None
        for number, handler in previous.items():
            try:
                signal.signal(number, handler)
            except BaseException as error:
                if close_error is None:
                    close_error = error
        for descriptor in (scope.reader, *scope.inherited_fds):
            try:
                os.close(descriptor)
            except BaseException as error:
                if close_error is None:
                    close_error = error
        if close_error is not None:
            if primary is None:
                raise close_error
            primary.add_note("consumer lab lifetime cleanup raised a secondary exception")


def managed_lifetime_entrypoint(main: Callable[[], int]) -> int:
    original = sys.argv
    present = [
        arg
        for arg in original[1:]
        if arg == _LIFETIME_ARGUMENT or arg.startswith(_LIFETIME_ARGUMENT + "=")
    ]
    if not present:
        return main()
    if len(original) < 3 or original[-2] != _LIFETIME_ARGUMENT or present != [_LIFETIME_ARGUMENT]:
        return 2
    try:
        with ExitStack() as lifetime:
            try:
                lifetime.enter_context(received_lifetime(original[-1]))
            except (OSError, ValueError, TypeError):
                return 2
            try:
                sys.argv = original[:-2]
                try:
                    status = main()
                except SystemExit as error:
                    if error.code is None or error.code == 0:
                        assert_lifetime_running()
                    raise
                assert_lifetime_running()
                return status
            finally:
                sys.argv = original
    except LifetimeCancelled as error:
        return error.exit_code


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    status: int | None
    stdout: bytes
    stderr: bytes
    error: str | None = None
    process_group_quiescent: bool | None = None


def run_bounded(
    command: str,
    args: Sequence[str],
    *,
    cwd: Path,
    max_output_bytes: int,
    timeout_seconds: float,
    env: Mapping[str, str],
    inherited_fds: Sequence[int] = (),
    cancellation_fd: int | None = None,
    execution_deadline: float | None = None,
) -> BoundedProcessResult:
    _validate_request(
        command,
        max_output_bytes=max_output_bytes,
        timeout_seconds=timeout_seconds,
    )
    scope = current_lifetime()
    if scope is not None:
        return _run_managed(
            command,
            args,
            cwd=cwd,
            max_output_bytes=max_output_bytes,
            timeout_seconds=timeout_seconds,
            env=env,
            scope=scope,
            inherited_fds=inherited_fds,
            cancellation_fd=cancellation_fd,
            execution_deadline=execution_deadline,
        )
    if inherited_fds or cancellation_fd is not None or execution_deadline is not None:
        raise ValueError("lifetime descriptors require an explicit scope")
    try:
        process = subprocess.Popen(  # noqa: S603
            (command, *args),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return BoundedProcessResult(
            status=None,
            stdout=b"",
            stderr=b"",
            error=_spawn_error(command, error),
        )

    stdout = bytearray()
    stderr = bytearray()
    selector = selectors.DefaultSelector()
    total_output = 0
    termination_reason: str | None = None
    force_kill_at: float | None = None
    hard_stop_at: float | None = None
    direct_exited = False
    group_finalized = False

    try:
        _register_read_pipe(selector, process.stdout, stdout)
        _register_read_pipe(selector, process.stderr, stderr)
        timeout_at = time.monotonic() + timeout_seconds
        while True:
            now = time.monotonic()
            if not direct_exited:
                direct_exited = _child_exited_without_reaping(process)

            if now >= timeout_at and termination_reason is None:
                termination_reason = "process timeout"
                force_kill_at, hard_stop_at = _begin_termination(process.pid, now)

            if direct_exited and not group_finalized:
                # WNOWAIT keeps the dead group leader as an identity anchor until
                # every same-group member has been signalled and output is drained.
                _signal_process_group(process.pid, signal.SIGKILL)
                group_finalized = True

            if force_kill_at is not None and now >= force_kill_at:
                _signal_process_group(process.pid, signal.SIGKILL)
                force_kill_at = None

            if hard_stop_at is not None and now >= hard_stop_at:
                _close_registered_pipes(selector)
                if termination_reason is None:
                    termination_reason = "process pipes did not close"

            for key, _mask in selector.select(_POLL_INTERVAL_SECONDS):
                sink = key.data
                if not isinstance(sink, bytearray):
                    raise AssertionError("process capture sink is invalid")
                chunk = _read_pipe(selector, key.fileobj)
                if chunk is None:
                    continue
                remaining = max(0, max_output_bytes - total_output)
                retained = chunk[:remaining]
                sink.extend(retained)
                total_output += len(retained)
                if len(chunk) > remaining and termination_reason is None:
                    termination_reason = "process output limit"
                    force_kill_at, hard_stop_at = _begin_termination(
                        process.pid,
                        time.monotonic(),
                    )

            if direct_exited and not _has_registered_read_pipe(selector):
                if force_kill_at is not None:
                    _signal_process_group(process.pid, signal.SIGKILL)
                break

        return_code = process.wait()
    except OSError as error:
        termination_reason = _spawn_error(command, error)
        _signal_process_group(process.pid, signal.SIGKILL)
        return_code = _wait_after_kill(process)
    finally:
        _close_registered_pipes(selector)
        selector.close()
        if process.returncode is None:
            _signal_process_group(process.pid, signal.SIGKILL)
            _wait_after_kill(process)

    if termination_reason is None and return_code < 0:
        termination_reason = "process terminated by signal"
    return BoundedProcessResult(
        status=None if termination_reason is not None else return_code,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        error=termination_reason,
    )


def _drain_managed(process: subprocess.Popen[bytes], schedule: _StopSchedule) -> bool:
    first = schedule.first_force is None
    force, hard = schedule.bounds()
    if first and process.returncode is None:
        _signal_process_group(process.pid, signal.SIGTERM)
    killed = False
    while True:
        force, hard = schedule.bounds()
        now = time.monotonic()
        if process.returncode is None:
            exited = _child_exited_without_reaping(process)
            if not killed and (now >= force or exited):
                _signal_process_group(process.pid, signal.SIGKILL)
                killed = True
            if exited and (sys.platform != "linux" or not _group_alive(process.pid, deadline=hard)):
                process.wait(timeout=0)
        if process.returncode is not None and not _group_alive(process.pid, deadline=hard):
            return True
        if now >= hard:
            if process.returncode is None:
                _signal_process_group(process.pid, signal.SIGKILL)
                process.poll()
            return process.returncode is not None and not _group_alive(process.pid, deadline=hard)
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(0, hard - time.monotonic())))


def _run_managed(
    command: str,
    args: Sequence[str],
    *,
    cwd: Path,
    max_output_bytes: int,
    timeout_seconds: float,
    env: Mapping[str, str],
    scope: LifetimeScope,
    inherited_fds: Sequence[int],
    cancellation_fd: int | None,
    execution_deadline: float | None,
) -> BoundedProcessResult:
    assert_lifetime_running()
    if isinstance(scope, _CleanupScope):
        scope.admit(command, args, cwd)
    descriptors = tuple(dict.fromkeys((*scope.inherited_fds, *inherited_fds)))
    for descriptor in descriptors:
        if type(descriptor) is not int or descriptor < 3:
            raise ValueError("invalid inherited lifetime descriptor")
        os.fstat(descriptor)
    if cancellation_fd is not None:
        import fcntl

        flags = fcntl.fcntl(cancellation_fd, fcntl.F_GETFL)
        if (
            not stat.S_ISFIFO(os.fstat(cancellation_fd).st_mode)
            or flags & os.O_ACCMODE != os.O_WRONLY
            or not flags & os.O_NONBLOCK
        ):
            raise ValueError("invalid lifetime notification pipe")
    deadline = time.monotonic() + timeout_seconds
    limits = [scope.deadline if scope.deadline is not None else deadline + 2]
    if execution_deadline is not None:
        if type(execution_deadline) not in {int, float} or not math.isfinite(execution_deadline):
            raise ValueError("execution deadline must be finite")
        limits.append(execution_deadline)
    hard_limit = min(limits)
    available_work = hard_limit - min(2.0, max(0.0, hard_limit - time.monotonic()) / 2)
    if available_work <= time.monotonic():
        return BoundedProcessResult(None, b"", b"", "process timeout", True)
    schedule = _StopSchedule(scope, cancellation_fd, hard_limit)
    selector: selectors.BaseSelector | None = None
    stdout, stderr = bytearray(), bytearray()
    failure: str | None = None
    quiescent = False
    process: subprocess.Popen[bytes] | None = None
    primary: BaseException | None = None
    with _defer_cancellation():
        try:
            process = subprocess.Popen(  # noqa: S603
                (command, *args),
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                pass_fds=descriptors,
            )
            with _interruptible_cancellation():
                selector = selectors.DefaultSelector()
                _register_read_pipe(selector, process.stdout, stdout)
                _register_read_pipe(selector, process.stderr, stderr)
                deadline = min(
                    time.monotonic() + timeout_seconds,
                    hard_limit - min(2.0, max(0.0, hard_limit - time.monotonic()) / 2),
                )
                schedule.limit = min(hard_limit, deadline + 2)
                while True:
                    assert_lifetime_running()
                    now = time.monotonic()
                    if scope.deadline is not None:
                        remaining = max(0.0, scope.deadline - now)
                        deadline = min(deadline, scope.deadline - min(2.0, remaining / 2))
                    if now >= deadline:
                        failure = "process timeout"
                        break
                    if process.returncode is None and _child_exited_without_reaping(process):
                        # Keep the leader as the group identity until the final owned signal.
                        _signal_process_group(process.pid, signal.SIGKILL)
                        if sys.platform != "linux" or not _group_alive(
                            process.pid, deadline=deadline
                        ):
                            process.wait(timeout=0)
                    if process.returncode is not None and not _has_registered_read_pipe(selector):
                        quiescent = not _group_alive(process.pid, deadline=deadline)
                        if quiescent:
                            break
                    for key, _mask in selector.select(
                        min(_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic()))
                    ):
                        chunk = _read_pipe(selector, key.fileobj)
                        if chunk is None:
                            continue
                        sink = cast(bytearray, key.data)
                        remaining = max(0, max_output_bytes - len(stdout) - len(stderr))
                        sink.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            failure = "process output limit"
                            break
                    if failure is not None:
                        break
        except OSError as error:
            try:
                if process is None:
                    return BoundedProcessResult(None, b"", b"", _spawn_error(command, error), True)
                failure = _spawn_error(command, error)
            except BaseException as handler_error:
                primary = handler_error
                raise
        except BaseException as error:
            primary = error
            raise
        finally:
            if process is not None:
                cleanup_primary = primary
                try:
                    try:
                        if not quiescent:
                            quiescent = _drain_managed(process, schedule)
                    except BaseException as error:
                        if cleanup_primary is None:
                            cleanup_primary = error
                        raise
                    finally:
                        _close_capture(process, selector, cleanup_primary)
                except BaseException as error:
                    if primary is None and (failure is None or not isinstance(error, Exception)):
                        raise
                    if primary is not None:
                        primary.add_note("consumer lab owned cleanup failed")
    if not quiescent:
        failure = failure or "process group drain unobserved"
    if failure is None and process.returncode is not None and process.returncode < 0:
        failure = "process terminated by signal"
    return BoundedProcessResult(
        None if failure else process.returncode,
        bytes(stdout),
        bytes(stderr),
        failure,
        quiescent,
    )


@dataclass(slots=True)
class _CleanupScope:
    parent: LifetimeScope
    root: Path
    identity: tuple[int, int]
    _ceiling: float | None = None

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        return self.parent.inherited_fds

    @property
    def deadline(self) -> float | None:
        limits = [
            item
            for item in (self._ceiling, self.parent.deadline, self.parent.cancellation_deadline)
            if item is not None
        ]
        self._ceiling = min(limits) if limits else 0.0
        return self._ceiling

    @property
    def cancellation_deadline(self) -> float | None:
        return self.parent.cancellation_deadline

    def stop_requested(self) -> bool:
        return False

    def admit(self, command: str, args: Sequence[str], cwd: Path) -> None:
        identity = self.root.lstat()
        if (
            command != sys.executable
            or tuple(args) != ("-I", "-S", "-c", _DELETE_TREE, str(self.root))
            or cwd != self.root.parent
            or not stat.S_ISDIR(identity.st_mode)
            or (identity.st_dev, identity.st_ino) != self.identity
        ):
            raise ValueError("command is outside owned temporary cleanup")


@contextmanager
def owned_temporary_directory(*, prefix: str) -> Iterator[str]:
    parent = current_lifetime()
    if parent is None:
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
            yield temporary
        return
    assert_lifetime_running()
    root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    identity = root.stat()
    primary: BaseException | None = None
    try:
        yield str(root)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            scope = _CleanupScope(parent, root, (identity.st_dev, identity.st_ino))
            with borrowed_lifetime(scope):
                deadline = scope.deadline
                if deadline is None or deadline <= time.monotonic():
                    raise ConsumerLabCleanupError("owned temporary cleanup allowance exhausted")
                result = run_bounded(
                    sys.executable,
                    ("-I", "-S", "-c", _DELETE_TREE, str(root)),
                    cwd=root.parent,
                    max_output_bytes=4096,
                    timeout_seconds=deadline - time.monotonic(),
                    env={},
                )
                bound = scope.deadline
                if (
                    result.status != 0
                    or not result.process_group_quiescent
                    or root.exists()
                    or bound is None
                    or time.monotonic() > bound
                ):
                    raise ConsumerLabCleanupError(
                        "owned temporary directory remains or cleanup is unobserved"
                    )
        except BaseException as error:
            if primary is None:
                if isinstance(error, ConsumerLabCleanupError) or not isinstance(error, Exception):
                    raise
                raise ConsumerLabCleanupError("owned temporary cleanup failed") from error
            primary.add_note("consumer lab owned temporary cleanup incomplete")


def _validate_request(
    command: str,
    *,
    max_output_bytes: int,
    timeout_seconds: float,
) -> None:
    if not command:
        raise ValueError("process command is empty")
    if sys.platform not in _SUPPORTED_PLATFORMS:
        raise RuntimeError(f"bounded process execution is unsupported on {sys.platform}")
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("process output bound must be a positive integer")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("process timeout must be a positive finite number")


def _register_read_pipe(
    selector: selectors.BaseSelector,
    stream: IO[bytes] | None,
    sink: bytearray,
) -> None:
    if stream is None:
        raise RuntimeError("process output pipe was not created")
    os.set_blocking(stream.fileno(), False)
    selector.register(stream, selectors.EVENT_READ, sink)


def _read_pipe(
    selector: selectors.BaseSelector,
    file_object: object,
) -> bytes | None:
    stream = cast(IO[bytes], file_object)
    try:
        chunk = os.read(stream.fileno(), _READ_CHUNK_BYTES)
    except BlockingIOError:
        return None
    if chunk:
        return chunk
    _unregister_and_close(selector, stream)
    return None


def _has_registered_read_pipe(selector: selectors.BaseSelector) -> bool:
    return any(isinstance(key.data, bytearray) for key in selector.get_map().values())


def _close_registered_pipes(selector: selectors.BaseSelector) -> None:
    for key in tuple(selector.get_map().values()):
        _unregister_and_close(selector, cast(IO[bytes], key.fileobj))


def _unregister_and_close(selector: selectors.BaseSelector, stream: IO[bytes]) -> None:
    with suppress(KeyError):
        selector.unregister(stream)
    with suppress(OSError):
        stream.close()


def _begin_termination(process_group_id: int, now: float) -> tuple[float, float]:
    _signal_process_group(process_group_id, signal.SIGTERM)
    force_kill_at = now + _FORCE_KILL_DELAY_SECONDS
    return force_kill_at, force_kill_at + _FORCE_KILL_DELAY_SECONDS


def _signal_process_group(process_group_id: int, process_signal: signal.Signals) -> bool:
    try:
        os.killpg(process_group_id, process_signal)
        return True
    except OSError as error:
        if error.errno in {errno.ESRCH, errno.EPERM}:
            return False
        raise


def _child_exited_without_reaping(process: subprocess.Popen[bytes]) -> bool:
    if process.returncode is not None:
        return True
    try:
        result = os.waitid(
            os.P_PID,
            process.pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except ChildProcessError:
        return process.returncode is not None
    return result is not None


def _wait_after_kill(process: subprocess.Popen[bytes]) -> int:
    try:
        return process.wait(timeout=_FORCE_KILL_DELAY_SECONDS)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        return process.wait()


def _spawn_error(command: str, error: OSError) -> str:
    code = errno.errorcode.get(error.errno or -1, type(error).__name__)
    return f"{Path(command).name}: {code}"
