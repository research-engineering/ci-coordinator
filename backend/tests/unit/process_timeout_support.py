from __future__ import annotations

import os
import selectors
import signal
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import IO, cast

import pytest


@contextmanager
def descendant_timeout_probe(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, *, parent_only: bool
) -> Iterator[tuple[str, Callable[[], bool]]]:
    register = cast(
        Callable[[selectors.BaseSelector, IO[bytes] | None, bytearray], None],
        module._register_read_pipe,
    )
    channel: socket.socket | None = None
    child_pid: int | None = None
    group_id: int | None = None
    observed = False
    # A short private path also fits Darwin's UNIX-domain socket path limit.
    with (
        TemporaryDirectory(prefix="ci-t-", dir="/tmp") as directory,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
    ):
        address = directory + "/child.sock"
        listener.settimeout(5)
        listener.bind(address)
        listener.listen(1)
        child = (
            "import os,socket; "
            "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(30); "
            f"s.connect({address!r}); "
            "s.sendall(f'{os.getpid()}:{os.getpgrp()}\\n'.encode()); "
            "s.recv(1); s.sendall(b'A'); s.recv(1)"
        )
        parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
        )

        def register_after_ready(
            selector: selectors.BaseSelector, stream: IO[bytes] | None, sink: bytearray
        ) -> None:
            nonlocal channel, child_pid, group_id
            if channel is None:
                deadline = time.monotonic() + 5
                channel, _ = listener.accept()
                data = bytearray()
                while not data.endswith(b"\n"):
                    remaining = deadline - time.monotonic()
                    assert remaining > 0, "descendant setup deadline"
                    channel.settimeout(remaining)
                    part = channel.recv(1)
                    assert part and len(data) < 64, "invalid descendant handshake"
                    data.extend(part)
                channel.settimeout(5)
                child_pid, group_id = map(int, data.decode().strip().split(":"))
                assert child_pid > 0 and group_id > 0 and group_id != os.getpgrp()
                assert os.getpgid(child_pid) == group_id
                # Hold the child across hard-stop; elapsed time cannot release this barrier.
                os.kill(child_pid, signal.SIGSTOP)
            register(selector, stream, sink)

        def survived() -> bool:
            nonlocal observed
            assert channel is not None and child_pid is not None and group_id is not None
            observed = True
            with suppress(ProcessLookupError):
                assert os.getpgid(child_pid) == group_id
                os.kill(child_pid, signal.SIGCONT)
            try:
                channel.sendall(b"R")
                reply = channel.recv(1)
            except (BrokenPipeError, ConnectionResetError):
                return False
            assert reply in (b"", b"A")
            return reply == b"A"

        monkeypatch.setattr(module, "_register_read_pipe", register_after_ready)
        if parent_only:

            def signal_parent(process_group_id: int, process_signal: signal.Signals) -> bool:
                try:
                    os.kill(process_group_id, process_signal)
                except ProcessLookupError:
                    return False
                return True

            monkeypatch.setattr(module, "_signal_process_group", signal_parent)
        try:
            yield parent, survived
            assert observed, "the caller must observe the descendant after timeout"
        finally:
            if group_id is not None and child_pid is not None:
                with suppress(ProcessLookupError):
                    if os.getpgid(child_pid) == group_id:
                        os.killpg(group_id, signal.SIGKILL)
            if channel is not None:
                channel.close()
