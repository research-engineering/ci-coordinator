from __future__ import annotations

import http.client
import importlib
import json
import os
import pwd
import re
import signal
import socket
import stat
import subprocess
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import FrameType, SimpleNamespace
from typing import BinaryIO, cast
from urllib.parse import urlencode

from scripts.bounded_process import spawn
from scripts.dev_environment.compose import ComposeError

LOG_REQUEST_TIMEOUT_SECONDS = 10
_FINITE_SECONDS = 30
_CLEANUP_SECONDS = 2
_MAX_FRAME_BYTES = 16 * 1024 * 1024
_MAX_CONFIG_BYTES = 1_048_576
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "connection",
        "content-length",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-connection",
        "accept-encoding",
        "user-agent",
    }
)


def _request_headers(environment: Mapping[str, str], cwd: Path) -> dict[str, str]:
    directory = environment.get("DOCKER_CONFIG")
    if not directory:
        home = environment.get("HOME") or pwd.getpwuid(os.getuid()).pw_dir
        directory = str(Path(home) / ".docker")
    path = cwd / directory / "config.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"Connection": "close"}
    except OSError:
        raise ComposeError("Docker log header configuration is unreadable") from None
    try:
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ComposeError("Docker log header configuration is not a regular file")
            payload = handle.read(_MAX_CONFIG_BYTES + 1)
        if len(payload) > _MAX_CONFIG_BYTES:
            raise ComposeError("Docker log header configuration exceeds its read bound")
        configuration = json.loads(payload)
        if not isinstance(configuration, dict):
            raise ValueError
        configured: object = {}
        for key, value in configuration.items():
            if key.lower() == "httpheaders":
                configured = value
        if configured is None:
            configured = {}
        if not isinstance(configured, dict) or len(configured) > 64:
            raise ValueError
        headers: dict[str, str] = {}
        size = 0
        for key, value in configured.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError
            if re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is None:
                raise ValueError
            if any(
                (ord(character) < 32 and character != "\t") or ord(character) == 127
                for character in value
            ):
                raise ValueError
            encoded = value.encode("utf-8")
            size += len(key) + len(encoded)
            if size > 65536:
                raise ValueError
            if key.lower() not in _RESERVED_HEADERS:
                # Go writes UTF-8 field values; HTTPConnection serializes strings as Latin-1.
                headers[key.lower()] = encoded.decode("latin-1")
        headers["Connection"] = "close"
        return headers
    except (OSError, ValueError, RecursionError):
        raise ComposeError("Docker log header configuration is invalid") from None


class _FrameBody:
    def __init__(self, response: http.client.HTTPResponse) -> None:
        self.response = response
        self.header = True

    def read(self, size: int) -> bytes:
        if not 0 <= size <= _MAX_FRAME_BYTES:
            raise ComposeError("log frame exceeds the read bound")
        data = self.response.read(size)
        if not data and self.header:
            return data
        if len(data) != size:
            raise ComposeError("log frame is incomplete")
        if self.header:
            # SDK 7.2 ignores the stream discriminator; daemon error frames are not app logs.
            if len(data) != 8 or data[0] not in (1, 2) or data[1:4] != b"\0\0\0":
                raise ComposeError("log provider returned a non-application frame")
            self.header = data[4:] == b"\0\0\0\0"
        else:
            self.header = True
        return data


class _DecoderContext:
    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection

    def _get_raw_response_socket(self, _response: object) -> socket.socket:
        return self.connection

    def _disable_socket_timeout(self, _connection: socket.socket) -> None:
        # The reader owns phase deadlines; SDK's unconditional timeout removal must not win.
        pass


def _copy_response(
    response: http.client.HTTPResponse,
    connection: socket.socket,
    *,
    tty: bool,
    output: BinaryIO,
) -> None:
    if response.status != 200:
        raise ComposeError("log provider rejected the request")
    expected = {"application/vnd.docker.raw-stream"}
    if not tty:
        expected.add("application/vnd.docker.multiplexed-stream")
    if response.headers.get_content_type() not in expected:
        raise ComposeError("log provider returned an invalid content type")
    if tty:
        chunks = iter(lambda: response.read1(65536), b"")
    else:
        decoder = importlib.import_module("docker.api.client").APIClient
        chunks = cast(
            "Iterator[bytes]",
            decoder._multiplexed_response_stream_helper(
                _DecoderContext(connection), SimpleNamespace(raw=_FrameBody(response))
            ),
        )
    for chunk in chunks:
        output.write(chunk)
        output.flush()


def _timeout(_signal: int, _frame: FrameType | None) -> None:
    raise TimeoutError("log provider phase timed out")


@contextmanager
def _deadline(seconds: float | None) -> Iterator[None]:
    previous = signal.signal(signal.SIGALRM, _timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0 if seconds is None else seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if previous_timer[0]:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.001, previous_timer[0] - (time.monotonic() - started)),
                previous_timer[1],
            )


def _reap(process: subprocess.Popen[bytes], *, completed: bool) -> None:
    try:
        result = process.wait(timeout=_CLEANUP_SECONDS)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=_CLEANUP_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=_CLEANUP_SECONDS)
        if completed:
            raise ComposeError("log transport required forced cleanup") from None
    else:
        if completed and result != 0:
            raise ComposeError("log transport failed")


@contextmanager
def _transport(
    *, environment: Mapping[str, str], cwd: Path
) -> Iterator[tuple[http.client.HTTPConnection, socket.socket]]:
    connection = http.client.HTTPConnection("docker", timeout=LOG_REQUEST_TIMEOUT_SECONDS)
    process: subprocess.Popen[bytes] | None = None
    completed = False
    primary: BaseException | None = None
    with _socket_pair() as (client, provider):
        try:
            # Keep the CLI transport in the Python worker's owned process group.
            process = subprocess.Popen(
                ("docker", "system", "dial-stdio"),  # noqa: S607 - preserve admitted provider PATH
                stdin=provider.fileno(),
                stdout=provider.fileno(),
                stderr=subprocess.DEVNULL,
                cwd=cwd,
                env=dict(environment),
                close_fds=True,
                start_new_session=False,
            )
            provider.close()
            client.settimeout(LOG_REQUEST_TIMEOUT_SECONDS)
            connection.sock = client
            yield connection, client
            completed = True
        except BaseException as error:
            primary = error
            raise
        finally:
            connection.close()
            client.close()
            if process is not None:
                try:
                    _reap(process, completed=completed)
                except (OSError, subprocess.TimeoutExpired, ComposeError):
                    if primary is None:
                        raise ComposeError("log transport cleanup failed") from None
                    primary.add_note("Log transport cleanup is also unproven.")


@contextmanager
def _socket_pair() -> Iterator[tuple[socket.socket, socket.socket]]:
    client, provider = socket.socketpair()
    try:
        yield client, provider
    finally:
        client.close()
        provider.close()


def read_logs(
    container: str,
    *,
    tail: int,
    follow: bool,
    output: BinaryIO,
    environment: Mapping[str, str],
    cwd: Path,
) -> None:
    started = time.monotonic()
    with _deadline(LOG_REQUEST_TIMEOUT_SECONDS):
        headers = _request_headers(environment, cwd)
        inspection = spawn(
            "docker",
            ("inspect", "--format", "{{.Config.Tty}}", container),
            cwd=cwd,
            env=environment,
            max_buffer=65536,
            timeout_seconds=LOG_REQUEST_TIMEOUT_SECONDS,
        )
    if (
        inspection.status != 0
        or inspection.error is not None
        or inspection.failure_kind is not None
        or inspection.stdout.strip() not in {"true", "false"}
    ):
        raise ComposeError("log container inspection is unavailable")
    with _transport(environment=environment, cwd=cwd) as (connection, raw_socket):
        with _deadline(max(0.001, LOG_REQUEST_TIMEOUT_SECONDS - (time.monotonic() - started))):
            query = urlencode({"stdout": 1, "stderr": 1, "tail": tail, "follow": int(follow)})
            connection.request("GET", f"/containers/{container}/logs?{query}", headers=headers)
            response = connection.getresponse()
        with response:
            raw_socket.settimeout(None if follow else LOG_REQUEST_TIMEOUT_SECONDS)
            duration = (
                None if follow else max(0.001, _FINITE_SECONDS - (time.monotonic() - started))
            )
            with _deadline(duration):
                _copy_response(
                    response, raw_socket, tty=inspection.stdout.strip() == "true", output=output
                )
