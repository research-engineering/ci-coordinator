from __future__ import annotations

import errno
import math
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final, Literal, cast

_FORCE_KILL_DELAY_SECONDS: Final = 1.0
_MAX_PROCESS_GROUP_DIAGNOSTICS: Final = 8
_MAX_PROC_ENTRIES: Final = 65_536
_MAX_PROC_STAT_BYTES: Final = 4_096
_NON_EXECUTABLE_PROC_STATES: Final = frozenset({b"X", b"Z", b"x"})
_SAFE_PROCESS_NAME: Final = frozenset(
    b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-"
)
_POLL_INTERVAL_SECONDS: Final = 0.02
_PROCESS_GROUP_QUIESCENCE_SECONDS: Final = 0.25
_READ_CHUNK_BYTES: Final = 65_536
SUPPORTED_PLATFORMS: Final = frozenset({"darwin", "linux"})
DecodeErrors = Literal["replace", "strict", "surrogateescape"]
CommandFailureKind = Literal[
    "cancelled",
    "lifecycle",
    "output-limit",
    "pipe-closure",
    "residual-descendant",
    "signal",
    "spawn",
    "timeout",
]
StopPredicate = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class CommandResult:
    status: int | None
    stdout: str
    stderr: str
    error: str | None = None
    failure_kind: CommandFailureKind | None = None
    signal: str | None = None

    @property
    def timed_out(self) -> bool:
        return self.failure_kind == "timeout"


@dataclass(frozen=True, slots=True)
class _ProcessGroupEvidence:
    has_executable_member: bool
    diagnostics: tuple[str, ...] = ()


ResidualProcessGroupPolicy = Literal["reject", "terminate"]


@dataclass(frozen=True, slots=True)
class InteractiveResult:
    returncode: int | None
    process_group_quiescent: bool
    failure_kind: CommandFailureKind | None = None
    escalated: bool = False
    started: bool = True
    cancellation_signal_sent: bool = False


def run_interactive(
    command: str,
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    inherited_fds: Sequence[int] = (),
    stop_requested: StopPredicate | None = None,
    timeout_seconds: float | None = None,
    graceful_seconds: float = 2,
    kill_seconds: float = 1,
    stderr: int | None = None,
    stdout_fd: int | None = None,
    on_interrupt: Callable[[InteractiveResult, BaseException], None] | None = None,
) -> InteractiveResult:
    """Supervise inherited stdio; group exit does not establish provider drain.

    on_interrupt observes bounded cleanup before the original exception is
    re-raised, so an owner can retain terminal facts without changing signals.
    """
    _validate_request(
        command,
        decode_errors="strict",
        max_buffer=1,
        residual_process_group_policy="reject",
        stop_requested=stop_requested,
        timeout_seconds=1 if timeout_seconds is None else timeout_seconds,
    )
    for name, budget, limit in (
        ("graceful_seconds", graceful_seconds, 600),
        ("kill_seconds", kill_seconds, 30),
    ):
        if (
            isinstance(budget, bool)
            or not isinstance(budget, (int, float))
            or not math.isfinite(budget)
            or not 0 < budget <= limit
        ):
            raise ValueError(
                f"interactive {name} must be positive, finite and at most {limit} seconds"
            )
    if stderr is not None and (type(stderr) is not int or stderr != subprocess.DEVNULL):
        raise ValueError("interactive stderr must be inherited or discarded")
    if stdout_fd is not None:
        _admit_inherited_fds((stdout_fd,))
    descriptors = _admit_inherited_fds(inherited_fds)
    if stop_requested is not None and stop_requested():
        return InteractiveResult(None, True, "cancelled", started=False)
    timeout_at = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    try:
        process = subprocess.Popen(  # noqa: S603 - shell-free owned interactive argv
            [command, *args],
            cwd=cwd,
            env=dict(env),
            stdout=stdout_fd,
            stderr=stderr,
            start_new_session=True,
            pass_fds=descriptors,
        )
    except OSError:
        return InteractiveResult(None, True, "spawn", started=False)
    try:
        while True:
            returncode = process.poll()
            if returncode is not None and not _is_process_group_alive(process.pid):
                return InteractiveResult(
                    returncode,
                    True,
                    "signal" if returncode < 0 else None,
                )
            failure: CommandFailureKind | None = None
            if stop_requested is not None and stop_requested():
                failure = "cancelled"
            elif timeout_at is not None and time.monotonic() >= timeout_at:
                failure = "timeout"
            elif returncode is not None:
                failure = "residual-descendant"
            if failure is not None:
                return _stop_interactive(process, failure, graceful_seconds, kill_seconds)
            time.sleep(_POLL_INTERVAL_SECONDS)
    except BaseException as error:
        interrupted = _stop_interactive(process, "cancelled", graceful_seconds, kill_seconds)
        if on_interrupt is not None:
            on_interrupt(interrupted, error)
        raise


def _stop_interactive(
    process: subprocess.Popen[bytes],
    failure: CommandFailureKind,
    graceful_seconds: float,
    kill_seconds: float,
) -> InteractiveResult:
    running_before_stop = process.poll() is None
    signal_sent = _signal_process_group(process.pid, signal.SIGTERM)
    cancellation_signal_sent = (
        failure == "cancelled" and running_before_stop and signal_sent is True
    )
    force_at = time.monotonic() + graceful_seconds
    deadline = force_at + kill_seconds
    forced = False
    while True:
        returncode = process.poll()
        quiescent = returncode is not None and not _is_process_group_alive(process.pid)
        now = time.monotonic()
        if quiescent:
            return InteractiveResult(
                returncode,
                quiescent,
                failure,
                escalated=forced,
                cancellation_signal_sent=cancellation_signal_sent,
            )
        if not forced and now >= force_at:
            _signal_process_group(process.pid, signal.SIGKILL)
            forced = True
        if now >= deadline:
            return InteractiveResult(
                returncode,
                False,
                failure,
                escalated=forced,
                cancellation_signal_sent=cancellation_signal_sent,
            )
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))


def spawn(
    command: str,
    args: Sequence[str],
    *,
    cwd: Path,
    max_buffer: int,
    timeout_seconds: float = 600,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    decode_errors: DecodeErrors = "replace",
    residual_process_group_policy: ResidualProcessGroupPolicy = "reject",
    stop_requested: StopPredicate | None = None,
    inherited_fds: Sequence[int] = (),
) -> CommandResult:
    _validate_request(
        command,
        decode_errors=decode_errors,
        max_buffer=max_buffer,
        residual_process_group_policy=residual_process_group_policy,
        stop_requested=stop_requested,
        timeout_seconds=timeout_seconds,
    )
    descriptors = _admit_inherited_fds(inherited_fds)
    if stop_requested is not None and stop_requested():
        return CommandResult(
            status=None,
            stdout="",
            stderr="",
            error=f"{command}: execution cancelled before spawn",
            failure_kind="cancelled",
        )
    input_bytes = None if input_text is None else input_text.encode("utf-8")
    try:
        process = subprocess.Popen(  # noqa: S603 - shell-free bounded argv API
            [command, *args],
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.DEVNULL if input_bytes is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=descriptors,
        )
    except OSError as error:
        return CommandResult(
            status=None,
            stdout="",
            stderr="",
            error=_spawn_error(command, error),
            failure_kind="spawn",
        )

    stdout = bytearray()
    stderr = bytearray()
    selector = selectors.DefaultSelector()
    input_offset = 0
    total_output = 0
    termination_reason: str | None = None
    failure_kind: CommandFailureKind | None = None
    force_kill_at: float | None = None
    hard_stop_at: float | None = None
    residual_group_checked = False
    residual_group_cleanup_started = False
    residual_group_deadline: float | None = None

    try:
        _register_read_pipe(selector, process.stdout, stdout)
        _register_read_pipe(selector, process.stderr, stderr)
        if input_bytes is not None:
            if process.stdin is None:
                raise RuntimeError("subprocess stdin pipe was not created")
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")

        timeout_at = time.monotonic() + timeout_seconds
        while True:
            now = time.monotonic()
            return_code = process.poll()

            if stop_requested is not None and stop_requested() and termination_reason is None:
                termination_reason = f"{command}: execution cancelled"
                failure_kind = "cancelled"
                if hard_stop_at is None:
                    force_kill_at, hard_stop_at = _begin_termination(process.pid, now)

            if now >= timeout_at and termination_reason is None:
                termination_reason = f"{command}: timed out after {timeout_seconds:g} seconds"
                failure_kind = "timeout"
                if hard_stop_at is None:
                    force_kill_at, hard_stop_at = _begin_termination(process.pid, now)

            if (
                return_code is not None
                and not residual_group_checked
                and termination_reason is None
            ):
                if residual_group_deadline is None:
                    residual_group_deadline = min(
                        now + _PROCESS_GROUP_QUIESCENCE_SECONDS,
                        timeout_at,
                    )
                if not _is_process_group_alive(process.pid):
                    # A descendant can retain a capture pipe while a bounded
                    # process-table observation temporarily misses it.
                    if not _has_registered_read_pipe(selector):
                        residual_group_checked = True
                elif now >= residual_group_deadline and not residual_group_cleanup_started:
                    if residual_process_group_policy == "terminate":
                        residual_group_cleanup_started = True
                        force_kill_at, hard_stop_at = _begin_termination(process.pid, now)
                    else:
                        detail = _residual_process_group_detail(process.pid)
                        termination_reason = (
                            f"{command}: direct child exited with residual descendants{detail}"
                        )
                        failure_kind = "residual-descendant"
                        force_kill_at, hard_stop_at = _begin_termination(process.pid, now)

            if residual_group_cleanup_started and not _is_process_group_alive(process.pid):
                residual_group_checked = True

            if force_kill_at is not None and now >= force_kill_at:
                _signal_process_group(process.pid, signal.SIGKILL)
                force_kill_at = None

            if hard_stop_at is not None and now >= hard_stop_at:
                _close_registered_pipes(selector)
                if termination_reason is None:
                    termination_reason = f"{command}: process pipes did not close after termination"
                    failure_kind = "pipe-closure"

            events = selector.select(_POLL_INTERVAL_SECONDS)
            for key, _mask in events:
                if key.data == "stdin":
                    if input_bytes is None:
                        raise AssertionError("stdin was registered without input")
                    input_offset = _write_input(selector, key.fileobj, input_bytes, input_offset)
                    continue

                sink = key.data
                if not isinstance(sink, bytearray):
                    raise TypeError("subprocess capture sink is not a bytearray")
                chunk = _read_pipe(selector, key.fileobj)
                if chunk is None:
                    continue
                remaining = max(0, max_buffer - total_output)
                retained = chunk[:remaining]
                sink.extend(retained)
                total_output += len(retained)
                if len(chunk) > remaining and termination_reason is None:
                    termination_reason = f"{command}: output exceeded {max_buffer} bytes"
                    failure_kind = "output-limit"
                    if hard_stop_at is None:
                        force_kill_at, hard_stop_at = _begin_termination(
                            process.pid,
                            time.monotonic(),
                        )

            now = time.monotonic()
            if now >= timeout_at and termination_reason is None:
                termination_reason = f"{command}: timed out after {timeout_seconds:g} seconds"
                failure_kind = "timeout"
                if hard_stop_at is None:
                    force_kill_at, hard_stop_at = _begin_termination(process.pid, now)

            if (
                process.poll() is not None
                and not _has_registered_read_pipe(selector)
                and (termination_reason is not None or residual_group_checked)
            ):
                if force_kill_at is not None and _is_process_group_alive(process.pid):
                    continue
                break

        return_code = process.wait()
    except OSError as error:
        lifecycle_failure = _spawn_error(command, error)
        if termination_reason is None:
            termination_reason = lifecycle_failure
            failure_kind = "lifecycle"
        else:
            termination_reason = (
                f"{termination_reason}; secondary lifecycle error: {lifecycle_failure}"
            )
        _signal_process_group(process.pid, signal.SIGKILL)
        return_code = _wait_after_kill(process)
    finally:
        _close_registered_pipes(selector)
        selector.close()
        if process.poll() is None:
            _signal_process_group(process.pid, signal.SIGKILL)
            _wait_after_kill(process)

    if termination_reason is None and stop_requested is not None and stop_requested():
        termination_reason = f"{command}: execution cancelled"
        failure_kind = "cancelled"
    if termination_reason is None and return_code < 0:
        termination_reason = f"{command}: terminated by signal {-return_code}"
        failure_kind = "signal"
    status = None if termination_reason is not None else return_code
    return CommandResult(
        status=status,
        stdout=stdout.decode("utf-8", errors=decode_errors),
        stderr=stderr.decode("utf-8", errors=decode_errors),
        error=termination_reason,
        failure_kind=failure_kind,
        signal=_signal_name(return_code),
    )


def _admit_inherited_fds(inherited_fds: Sequence[int]) -> tuple[int, ...]:
    if isinstance(inherited_fds, (str, bytes, bytearray)):
        raise ValueError("inherited descriptors must be a sequence of open non-stdio descriptors")
    descriptors = tuple(inherited_fds)
    if any(type(descriptor) is not int or descriptor < 3 for descriptor in descriptors):
        raise ValueError("inherited descriptors must be open non-stdio descriptors")
    if len(set(descriptors)) != len(descriptors):
        raise ValueError("inherited descriptors must be unique")
    try:
        for descriptor in descriptors:
            os.fstat(descriptor)
    except OSError as error:
        raise ValueError("an inherited descriptor is unavailable") from error
    return descriptors


def _validate_request(
    command: str,
    *,
    decode_errors: DecodeErrors,
    max_buffer: int,
    residual_process_group_policy: ResidualProcessGroupPolicy,
    stop_requested: StopPredicate | None,
    timeout_seconds: float,
) -> None:
    if not command:
        raise TypeError("The argument 'file' cannot be empty. Received ''")
    if sys.platform not in SUPPORTED_PLATFORMS:
        raise RuntimeError(
            "bounded subprocess execution is supported only on Linux and macOS; "
            f"unsupported platform: {sys.platform}"
        )
    if type(max_buffer) is not int or max_buffer <= 0:
        raise ValueError("max_buffer must be a positive integer")
    if decode_errors not in {"replace", "strict", "surrogateescape"}:
        raise ValueError("decode_errors is not admitted")
    if residual_process_group_policy not in {"reject", "terminate"}:
        raise ValueError("residual_process_group_policy is not admitted")
    if stop_requested is not None and not callable(stop_requested):
        raise TypeError("stop_requested must be callable")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a positive finite number")


def _register_read_pipe(
    selector: selectors.BaseSelector,
    stream: IO[bytes] | None,
    sink: bytearray,
) -> None:
    if stream is None:
        raise RuntimeError("subprocess output pipe was not created")
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


def _write_input(
    selector: selectors.BaseSelector,
    file_object: object,
    input_bytes: bytes,
    offset: int,
) -> int:
    stream = cast(IO[bytes], file_object)
    try:
        written = os.write(stream.fileno(), input_bytes[offset:])
    except BlockingIOError:
        return offset
    except BrokenPipeError:
        _unregister_and_close(selector, stream)
        return len(input_bytes)
    offset += written
    if offset == len(input_bytes):
        _unregister_and_close(selector, stream)
    return offset


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


def _is_process_group_alive(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    if sys.platform != "linux":
        return True
    return _linux_process_group_has_executable_member(process_group_id)


def _linux_process_group_has_executable_member(
    process_group_id: int,
    *,
    proc_root: Path = Path("/proc"),
) -> bool:
    return _linux_process_group_evidence(
        process_group_id,
        proc_root=proc_root,
    ).has_executable_member


def _linux_process_group_evidence(
    process_group_id: int,
    *,
    proc_root: Path = Path("/proc"),
) -> _ProcessGroupEvidence:
    try:
        entries = os.scandir(proc_root)
    except OSError:
        return _ProcessGroupEvidence(True, ("procfs-unavailable",))

    process_entries: list[tuple[int, Path]] = []
    diagnostics: list[str] = []
    with entries:
        for entry in entries:
            if not entry.name.isascii() or not entry.name.isdecimal():
                continue
            process_entries.append((int(entry.name), Path(entry.path)))
            if len(process_entries) > _MAX_PROC_ENTRIES:
                return _ProcessGroupEvidence(
                    True,
                    ("procfs-entry-limit-exceeded",),
                )

    for _pid, process_path in sorted(process_entries):
        try:
            with open(
                process_path / "stat",
                "rb",
                buffering=0,
            ) as stream:
                raw_stat = stream.read(_MAX_PROC_STAT_BYTES + 1)
        except FileNotFoundError:
            continue
        except OSError:
            return _ProcessGroupEvidence(
                True,
                ("process-metadata-unavailable",),
            )
        if len(raw_stat) > _MAX_PROC_STAT_BYTES:
            return _ProcessGroupEvidence(
                True,
                ("process-metadata-limit-exceeded",),
            )
        closing_name = raw_stat.rfind(b") ")
        if closing_name < 0:
            return _ProcessGroupEvidence(
                True,
                ("process-metadata-malformed",),
            )
        fields = raw_stat[closing_name + 2 :].split()
        if len(fields) < 18:
            return _ProcessGroupEvidence(
                True,
                ("process-metadata-malformed",),
            )
        try:
            observed_group_id = int(fields[2])
            observed_thread_count = int(fields[17])
        except ValueError:
            return _ProcessGroupEvidence(
                True,
                ("process-metadata-malformed",),
            )
        if observed_group_id == process_group_id and (
            fields[0] not in _NON_EXECUTABLE_PROC_STATES or observed_thread_count != 1
        ):
            diagnostics.append(
                _process_diagnostic(
                    raw_stat,
                    closing_name,
                    fields[0],
                    observed_thread_count,
                )
            )
            if len(diagnostics) >= _MAX_PROCESS_GROUP_DIAGNOSTICS:
                break
    return _ProcessGroupEvidence(bool(diagnostics), tuple(diagnostics))


def _process_diagnostic(
    raw_stat: bytes,
    closing_name: int,
    state: bytes,
    thread_count: int,
) -> str:
    opening_name = raw_stat.find(b"(")
    encoded_name = raw_stat[opening_name + 1 : closing_name] if opening_name >= 0 else b""
    name = (
        encoded_name.decode("ascii")
        if 0 < len(encoded_name) <= 64 and all(byte in _SAFE_PROCESS_NAME for byte in encoded_name)
        else "unidentified"
    )
    encoded_state = state.decode("ascii") if len(state) == 1 and state.isalpha() else "?"
    return f"name={name},state={encoded_state},threads={thread_count}"


def _residual_process_group_detail(process_group_id: int) -> str:
    if sys.platform != "linux":
        return ""
    evidence = _linux_process_group_evidence(process_group_id)
    if not evidence.diagnostics:
        return ""
    return f" ({'; '.join(evidence.diagnostics)})"


def _wait_after_kill(process: subprocess.Popen[bytes]) -> int:
    try:
        return process.wait(timeout=_FORCE_KILL_DELAY_SECONDS)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        return process.wait()


def _spawn_error(command: str, error: OSError) -> str:
    code = errno.errorcode.get(error.errno or -1, type(error).__name__)
    return f"{command}: {code}"


def _signal_name(return_code: int) -> str | None:
    if return_code >= 0:
        return None
    try:
        return signal.Signals(-return_code).name
    except ValueError:
        return f"SIG{-return_code}"
