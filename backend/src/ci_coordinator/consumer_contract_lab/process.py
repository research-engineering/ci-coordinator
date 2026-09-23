"""Bounded process-group execution for local consumer controls."""

from __future__ import annotations

import errno
import math
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final, cast

_FORCE_KILL_DELAY_SECONDS: Final = 1.0
_POLL_INTERVAL_SECONDS: Final = 0.02
_READ_CHUNK_BYTES: Final = 65_536
_SUPPORTED_PLATFORMS: Final = frozenset({"darwin", "linux"})


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    status: int | None
    stdout: bytes
    stderr: bytes
    error: str | None = None


def run_bounded(
    command: str,
    args: Sequence[str],
    *,
    cwd: Path,
    max_output_bytes: int,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> BoundedProcessResult:
    _validate_request(
        command,
        max_output_bytes=max_output_bytes,
        timeout_seconds=timeout_seconds,
    )
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
