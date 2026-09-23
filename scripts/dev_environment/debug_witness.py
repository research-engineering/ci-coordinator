"""DAP effect witness; the caller owns debug startup, exclusion and ordinary-mode restore."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

DEBUG_CONTAINER_PORT: Final = 5678
DEBUG_OVERRIDE_PATH: Final = Path("docker/development/compose.debug.yaml")
DEBUG_REMOTE_SOURCE_ROOT: Final = "/workspace/backend/src"
_ENTRYPOINT: Final = Path("ci_coordinator/runtime/__main__.py")
_MAX_MESSAGE_BYTES: Final = 65_536
_MAX_MESSAGES: Final = 256
_MAX_HEADER_BYTES: Final = 1_024
_REQUEST_COMMANDS: Final = frozenset(
    {
        "initialize",
        "attach",
        "setBreakpoints",
        "configurationDone",
        "stackTrace",
        "continue",
        "disconnect",
    }
)
_EXPECTED_EVENTS: Final = frozenset({"initialized", "stopped", "continued"})


class DebugWitnessError(RuntimeError):
    """A bounded debugger failure without adapter output or debuggee values."""


class DebugSocket(Protocol):
    def settimeout(self, value: float, /) -> None: ...

    def sendall(self, data: bytes, /) -> None: ...

    def recv(self, size: int, /) -> bytes: ...

    def close(self) -> None: ...


type Connector = Callable[[tuple[str, int], float], DebugSocket]
type Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class DebugWitnessResult:
    source_sha256: str
    breakpoint_line: int
    thread_id: int
    state: str = "breakpoint_continued"


def backend_attach_configuration(repo_root: Path, port: int) -> dict[str, object]:
    if type(port) is not int or not 1 <= port <= 65_535:
        raise DebugWitnessError("debugger port is invalid")
    root = repo_root.resolve(strict=True)
    source = (root / "backend/src").resolve(strict=True)
    if not source.is_dir() or not source.is_relative_to(root):
        raise DebugWitnessError("debugger source mapping escaped the repository")
    return {
        "name": "Coordinator backend",
        "type": "debugpy",
        "request": "attach",
        "connect": {"host": "127.0.0.1", "port": port},
        "pathMappings": [{"localRoot": str(source), "remoteRoot": DEBUG_REMOTE_SOURCE_ROOT}],
        "justMyCode": False,
        "subProcess": False,
    }


def verify_backend_debugger(
    repo_root: Path,
    *,
    port: int,
    timeout_seconds: float = 45.0,
    connect: Connector = socket.create_connection,
    clock: Clock = time.monotonic,
) -> DebugWitnessResult:
    """Require a real mapped breakpoint and continue event; this does not prove readiness."""
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
        raise DebugWitnessError("debugger witness deadline is invalid")
    configuration = backend_attach_configuration(repo_root, port)
    source = (repo_root / "backend/src" / _ENTRYPOINT).resolve(strict=True)
    if (
        not source.is_relative_to((repo_root / "backend/src").resolve(strict=True))
        or source.stat().st_size > _MAX_MESSAGE_BYTES
    ):
        raise DebugWitnessError("debugger entrypoint escaped the source boundary")
    source_bytes = source.read_bytes()
    module = ast.parse(source_bytes)
    mains = [
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    if len(mains) != 1 or not mains[0].body or not isinstance(mains[0].body[0], ast.Assign):
        raise DebugWitnessError("debugger entrypoint breakpoint is unavailable")
    line = mains[0].body[0].lineno
    deadline = clock() + timeout_seconds
    try:
        while True:
            try:
                connection = connect(("127.0.0.1", port), min(1.0, _remaining(deadline, clock)))
                break
            except (ConnectionRefusedError, TimeoutError):
                time.sleep(min(0.1, _remaining(deadline, clock)))
        try:
            dap = _DAP(connection, deadline=deadline, clock=clock)
            _verify_session(dap, configuration, source, line)
            if source.read_bytes() != source_bytes:
                raise DebugWitnessError("debugger source changed during qualification")
            return DebugWitnessResult(hashlib.sha256(source_bytes).hexdigest(), line, dap.thread_id)
        finally:
            connection.close()
    except (OSError, ValueError) as error:
        raise DebugWitnessError("debugger protocol connection failed") from error


def _verify_session(
    dap: _DAP,
    configuration: Mapping[str, object],
    source: Path,
    line: int,
) -> None:
    capabilities = dap.call(
        "initialize",
        {
            "adapterID": "debugpy",
            "pathFormat": "path",
            "linesStartAt1": True,
            "columnsStartAt1": True,
        },
    )
    if capabilities.get("supportsConfigurationDoneRequest") is not True:
        raise DebugWitnessError("debugger does not support configuration completion")
    attach = dap.send(
        "attach", {key: value for key, value in configuration.items() if key != "connect"}
    )
    dap.event("initialized")
    body = dap.call(
        "setBreakpoints", {"source": {"path": str(source)}, "breakpoints": [{"line": line}]}
    )
    breakpoints = body.get("breakpoints")
    if not isinstance(breakpoints, list) or len(breakpoints) != 1:
        raise DebugWitnessError("debugger did not register the requested breakpoint")
    dap.call("configurationDone", {})
    dap.response(attach, "attach")
    stopped = dap.event("stopped")
    thread_id = stopped.get("threadId")
    if stopped.get("reason") != "breakpoint" or type(thread_id) is not int or thread_id <= 0:
        raise DebugWitnessError("debugger did not stop at a breakpoint")
    dap.thread_id = thread_id
    frames = dap.call("stackTrace", {"threadId": thread_id, "startFrame": 0, "levels": 1}).get(
        "stackFrames"
    )
    if not isinstance(frames, list) or len(frames) != 1:
        raise DebugWitnessError("debugger breakpoint stack is unavailable")
    frame = _object(frames[0])
    mapped_path = _object(frame.get("source")).get("path")
    if frame.get("line") != line or mapped_path != str(source):
        raise DebugWitnessError("debugger stopped outside the mapped source breakpoint")
    dap.call("setBreakpoints", {"source": {"path": str(source)}, "breakpoints": []})
    dap.call("continue", {"threadId": thread_id})
    continued = dap.event("continued")
    if continued.get("threadId") != thread_id and continued.get("allThreadsContinued") is not True:
        raise DebugWitnessError("debugger did not continue the stopped thread")
    dap.call("disconnect", {"terminateDebuggee": False})


class _DAP:
    def __init__(self, connection: DebugSocket, *, deadline: float, clock: Clock) -> None:
        self.connection = connection
        self.deadline = deadline
        self.clock = clock
        self.sequence = 0
        self.received = 0
        self.thread_id = 0
        self.disconnecting = False
        self.last_request = "none"
        self.waiting = "none"
        self.buffer = bytearray()
        self.pending: list[dict[str, object]] = []

    def send(self, command: str, arguments: Mapping[str, object]) -> int:
        self.last_request = command if command in _REQUEST_COMMANDS else "unknown"
        if command == "disconnect":
            self.disconnecting = True
        self.sequence += 1
        body = json.dumps(
            {"seq": self.sequence, "type": "request", "command": command, "arguments": arguments},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > _MAX_MESSAGE_BYTES:
            raise DebugWitnessError("debugger request exceeded the byte bound")
        self.connection.settimeout(_remaining(self.deadline, self.clock))
        self.connection.sendall(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        return self.sequence

    def call(self, command: str, arguments: Mapping[str, object]) -> dict[str, object]:
        return self.response(self.send(command, arguments), command)

    def response(self, sequence: int, command: str) -> dict[str, object]:
        self.waiting = "response:" + (command if command in _REQUEST_COMMANDS else "unknown")
        message = self._take(
            lambda item: item.get("type") == "response" and item.get("request_seq") == sequence
        )
        if message.get("command") != command or message.get("success") is not True:
            raise DebugWitnessError("debugger rejected a required request")
        return _object(message.get("body", {}))

    def event(self, name: str) -> dict[str, object]:
        self.waiting = "event:" + (name if name in _EXPECTED_EVENTS else "unknown")
        message = self._take(lambda item: item.get("type") == "event" and item.get("event") == name)
        return _object(message.get("body", {}))

    def _take(self, matches: Callable[[dict[str, object]], bool]) -> dict[str, object]:
        while True:
            _remaining(self.deadline, self.clock)
            for index, item in enumerate(self.pending):
                if matches(item):
                    return self.pending.pop(index)
            item = self._read()
            if item.get("type") not in ("event", "response"):
                raise DebugWitnessError("debugger sent an unsupported reverse request")
            if item.get("event") in ("terminated", "exited") and not self.disconnecting:
                raise DebugWitnessError("debugger exited before witness completion")
            if matches(item):
                return item
            self.pending.append(item)

    def _read(self) -> dict[str, object]:
        self.received += 1
        if self.received > _MAX_MESSAGES:
            raise DebugWitnessError("debugger exceeded the message bound")
        while b"\r\n\r\n" not in self.buffer:
            if len(self.buffer) > _MAX_HEADER_BYTES:
                raise DebugWitnessError("debugger header exceeded the byte bound")
            self._receive()
        header, _, body = self.buffer.partition(b"\r\n\r\n")
        if len(header) > _MAX_HEADER_BYTES or not header.startswith(b"Content-Length: "):
            raise DebugWitnessError("debugger framing is invalid")
        length_text = header.removeprefix(b"Content-Length: ")
        if not length_text.isdigit() or len(length_text) > 6:
            raise DebugWitnessError("debugger content length is invalid")
        length = int(length_text)
        if not 0 < length <= _MAX_MESSAGE_BYTES:
            raise DebugWitnessError("debugger message exceeded the byte bound")
        self.buffer = body
        while len(self.buffer) < length:
            self._receive()
        payload = bytes(self.buffer[:length])
        del self.buffer[:length]
        return _object(json.loads(payload))

    def _receive(self) -> None:
        self.connection.settimeout(_remaining(self.deadline, self.clock))
        chunk = self.connection.recv(4_096)
        if not chunk:
            raise DebugWitnessError(
                "debugger closed the protocol connection; "
                f"waiting={self.waiting}; last_request={self.last_request}"
            )
        self.buffer.extend(chunk)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DebugWitnessError("debugger message shape is invalid")
    return dict(value)


def _remaining(deadline: float, clock: Clock) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise DebugWitnessError("debugger witness deadline exceeded")
    return remaining
