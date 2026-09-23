from __future__ import annotations

import http.client
import io
import json
import os
import pwd
import socket
import subprocess
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts.bounded_process import CommandResult
from scripts.dev_environment import log_transport
from scripts.dev_environment.compose import ComposeError

_OUT = b"\x01\x00\x00\x00\x00\x00\x00\x04out\n"
_ERR = b"\x02\x00\x00\x00\x00\x00\x00\x04err\n"


@pytest.mark.parametrize("selected", ["explicit", "home", "empty-override", "missing-explicit"])
def test_header_config_uses_cli_path_precedence_without_legacy_or_missing_path_fallback(
    tmp_path: Path, selected: str
) -> None:
    home = tmp_path / "home"
    (home / ".docker").mkdir(parents=True)
    (home / ".docker/config.json").write_text(json.dumps({"HttpHeaders": {"X-Scope": "home"}}))
    (home / ".dockercfg").write_text(json.dumps({"HttpHeaders": {"X-Scope": "legacy"}}))
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (explicit / "config.json").write_text(json.dumps({"HttpHeaders": {"X-Scope": "explicit"}}))
    environment = {"HOME": str(home)}
    if selected == "explicit":
        environment["DOCKER_CONFIG"] = "explicit"
    elif selected == "empty-override":
        environment["DOCKER_CONFIG"] = ""
    elif selected == "missing-explicit":
        environment["DOCKER_CONFIG"] = "absent"
    headers = log_transport._request_headers(environment, tmp_path)
    if selected == "missing-explicit":
        assert headers == {"Connection": "close"}
    else:
        assert headers == {
            "Connection": "close",
            "x-scope": "explicit" if selected == "explicit" else "home",
        }


def test_header_config_home_fallback_uses_user_database_not_ambient_python_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".docker").mkdir()
    (tmp_path / ".docker/config.json").write_text('{"HttpHeaders":{"X-Scope":"user-home"}}')
    monkeypatch.setenv("HOME", str(tmp_path / "unselected"))
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_dir=str(tmp_path)))
    assert log_transport._request_headers({}, tmp_path) == {
        "Connection": "close",
        "x-scope": "user-home",
    }


def test_configured_headers_cannot_override_request_framing_and_preserve_utf8_wire_values(
    tmp_path: Path,
) -> None:
    configuration = {
        "HttpHeaders": {
            "X-Scope": "caf\u00e9",
            "Authorization": "synthetic-only",
            "cOnNeCtIoN": "keep-alive",
            "HOST": "wrong-host",
            "Transfer-Encoding": "chunked",
            "Content-Length": "10",
            "Upgrade": "h2c",
            "Accept-Encoding": "gzip",
            "User-Agent": "ignored-override",
        },
        "auths": {"registry.invalid": {"auth": "secret-unused"}},
    }
    (tmp_path / "config.json").write_text(json.dumps(configuration))
    headers = log_transport._request_headers({"DOCKER_CONFIG": str(tmp_path)}, tmp_path)
    assert headers == {
        "x-scope": b"caf\xc3\xa9".decode("latin-1"),
        "authorization": "synthetic-only",
        "Connection": "close",
    }
    sent: list[bytes] = []
    connection = http.client.HTTPConnection("docker")
    connection.sock = cast("socket.socket", SimpleNamespace(sendall=sent.append))
    connection.request("GET", "/containers/owned/logs", headers=headers)
    wire = b"".join(sent)
    assert b"x-scope: caf\xc3\xa9\r\n" in wire
    assert b"Host: docker\r\n" in wire and b"Connection: close\r\n" in wire
    assert b"secret-unused" not in wire and b"Transfer-Encoding" not in wire
    assert b"Content-Length" not in wire and b"Upgrade" not in wire


@pytest.mark.parametrize(
    "payload",
    [
        b"secret-malformed",
        b"[]",
        b'{"HttpHeaders":[]}',
        b'{"HttpHeaders":{"X-Scope":42}}',
        b'{"HttpHeaders":{"bad key":"secret"}}',
        b'{"HttpHeaders":{"X-Scope":"secret\\r\\nInjected: bad"}}',
        b'{"HttpHeaders":{"X-Scope":"secret\\u0000"}}',
        pytest.param(
            json.dumps({"HttpHeaders": {"X-Scope": "s" * 65537}}).encode(),
            id="oversized-header-value",
        ),
        pytest.param(
            json.dumps({"HttpHeaders": {f"X-{i}": "secret" for i in range(65)}}).encode(),
            id="too-many-headers",
        ),
        pytest.param(
            b" " * (log_transport._MAX_CONFIG_BYTES + 1),
            id="oversized-config-document",
        ),
    ],
)
def test_invalid_or_oversized_header_configuration_fails_without_provider_data(
    tmp_path: Path, payload: bytes
) -> None:
    (tmp_path / "config.json").write_bytes(payload)
    with pytest.raises(ComposeError) as captured:
        log_transport._request_headers({"DOCKER_CONFIG": str(tmp_path)}, tmp_path)
    assert str(captured.value) in {
        "Docker log header configuration is invalid",
        "Docker log header configuration exceeds its read bound",
    }


def test_header_config_rejects_a_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "config.json")
    with pytest.raises(ComposeError, match="not a regular file"):
        log_transport._request_headers({"DOCKER_CONFIG": str(tmp_path)}, tmp_path)


class Wire:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def makefile(self, _mode: str) -> io.BytesIO:
        return io.BytesIO(self.payload)


def response_for(
    body: bytes,
    *,
    chunked: bool = False,
    status: int = 200,
    content_type: str = "application/vnd.docker.raw-stream",
) -> http.client.HTTPResponse:
    headers = f"HTTP/1.1 {status} Result\r\nContent-Type: {content_type}\r\n"
    if chunked:
        pieces = (body[:3], body[3:10], body[10:])
        body = (
            b"".join(f"{len(piece):x}\r\n".encode() + piece + b"\r\n" for piece in pieces if piece)
            + b"0\r\n\r\n"
        )
        headers += "Transfer-Encoding: chunked\r\n"
    else:
        headers += f"Content-Length: {len(body)}\r\n"
    response = http.client.HTTPResponse(
        cast("socket.socket", Wire(headers.encode() + b"\r\n" + body))
    )
    response.begin()
    return response


@pytest.mark.parametrize("chunked", [False, True])
def test_stdlib_http_and_sdk_decoder_preserve_both_channels(chunked: bool) -> None:
    response = response_for(_OUT + _ERR, chunked=chunked)
    output = io.BytesIO()
    log_transport._copy_response(
        response, cast("socket.socket", SimpleNamespace()), tty=False, output=output
    )
    assert output.getvalue() == b"out\nerr\n"


@pytest.mark.parametrize("body", [b"", b"\x01\x00\x00\x00\x00\x00\x00\x00" + _OUT])
def test_empty_replay_and_empty_frames_do_not_hide_later_output(body: bytes) -> None:
    output = io.BytesIO()
    log_transport._copy_response(
        response_for(body), cast("socket.socket", SimpleNamespace()), tty=False, output=output
    )
    assert output.getvalue() == (b"out\n" if body else b"")


def test_tty_response_keeps_its_unframed_application_bytes() -> None:
    output = io.BytesIO()
    log_transport._copy_response(
        response_for(b"out\r\nerr\r\n", chunked=True),
        cast("socket.socket", SimpleNamespace()),
        tty=True,
        output=output,
    )
    assert output.getvalue() == b"out\r\nerr\r\n"


@pytest.mark.parametrize(
    "body",
    [
        _OUT[:7],
        _OUT[:8],
        _OUT[:10],
        b"\x03\x00\x00\x00\x00\x00\x00\x04oops",
        b"\x01\x00\x00\x00\x01\x00\x00\x01",
    ],
)
def test_incomplete_provider_error_and_oversized_frames_never_succeed(body: bytes) -> None:
    output = io.BytesIO()
    with pytest.raises(ComposeError):
        log_transport._copy_response(
            response_for(body), cast("socket.socket", SimpleNamespace()), tty=False, output=output
        )
    assert output.getvalue() == b""


@pytest.mark.parametrize(
    ("status", "content_type"), [(404, "application/json"), (200, "application/json")]
)
def test_http_provider_diagnostics_never_reach_application_output(
    status: int, content_type: str
) -> None:
    output = io.BytesIO()
    response = response_for(
        b'{"message":"https://user:credential@provider.invalid"}',
        status=status,
        content_type=content_type,
    )
    with pytest.raises(ComposeError):
        log_transport._copy_response(
            response, cast("socket.socket", SimpleNamespace()), tty=False, output=output
        )
    assert output.getvalue() == b""


@pytest.mark.parametrize("follow", [False, True])
def test_phase_deadlines_and_connection_close_are_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    follow: bool,
) -> None:
    deadlines: list[float | None] = []
    socket_timeouts: list[float | None] = []
    environment = {
        "DOCKER_CONTEXT": "selected",
        "DOCKER_HOST": "unix:///wrong.sock",
        "HOME": str(tmp_path),
    }
    (tmp_path / ".docker").mkdir()
    (tmp_path / ".docker/config.json").write_text('{"HttpHeaders":{"X-Scope":"selected"}}')
    requests: list[tuple[str, str, Mapping[str, str]]] = []

    @contextmanager
    def deadline(seconds: float | None) -> Iterator[None]:
        deadlines.append(seconds)
        yield

    def inspection(command: str, args: Sequence[str], **kwargs: object) -> CommandResult:
        assert command == "docker" and tuple(args) == (
            "inspect",
            "--format",
            "{{.Config.Tty}}",
            "a" * 64,
        )
        assert kwargs["env"] is environment and kwargs["timeout_seconds"] == 10
        return CommandResult(0, "false\n", "")

    def request(method: str, path: str, *, headers: Mapping[str, str]) -> None:
        requests.append((method, path, headers))

    @contextmanager
    def transport(**kwargs: object) -> Iterator[tuple[object, object]]:
        assert kwargs == {"environment": environment, "cwd": tmp_path}
        yield (
            SimpleNamespace(request=request, getresponse=lambda: response_for(_OUT)),
            SimpleNamespace(settimeout=socket_timeouts.append),
        )

    monkeypatch.setattr(log_transport, "_deadline", deadline)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_transport, "spawn", inspection)
    monkeypatch.setattr(log_transport, "_transport", transport)
    output = io.BytesIO()
    log_transport.read_logs(
        "a" * 64, tail=17, follow=follow, output=output, environment=environment, cwd=tmp_path
    )
    assert requests == [
        (
            "GET",
            f"/containers/{'a' * 64}/logs?stdout=1&stderr=1&tail=17&follow={int(follow)}",
            {"Connection": "close", "x-scope": "selected"},
        )
    ]
    assert deadlines[0] == 10 and len(deadlines) == 3
    assert deadlines[1] is not None and 0 < deadlines[1] <= 10
    if follow:
        assert deadlines[2] is None and socket_timeouts == [None]
    else:
        assert deadlines[2] is not None and 0 < deadlines[2] <= 30
        assert socket_timeouts == [10]
    assert output.getvalue() == b"out\n"


def test_cli_transport_inherits_exact_context_and_python_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {
        "DOCKER_CONTEXT": "ssh-with-socket",
        "DOCKER_HOST": "unix:///unselected.sock",
        "DOCKER_CONFIG": "/private/config",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CERT_PATH": "/private/certs",
        "HOME": "/private/home",
        "PATH": "/private/bin",
    }
    calls: list[tuple[object, Mapping[str, object]]] = []

    def start(argv: Sequence[str], **kwargs: object) -> SimpleNamespace:
        calls.append((tuple(argv), kwargs))
        assert type(kwargs["stdin"]) is int and kwargs["stdin"] == kwargs["stdout"]
        assert kwargs["env"] == environment and kwargs["cwd"] == tmp_path
        assert kwargs["start_new_session"] is False and kwargs["stderr"] == subprocess.DEVNULL
        return SimpleNamespace(wait=lambda **_kwargs: 0)

    monkeypatch.setattr(subprocess, "Popen", start)
    with log_transport._transport(environment=environment, cwd=tmp_path) as (connection, native):
        assert connection.sock is native and native.gettimeout() == 10
    assert len(calls) == 1 and calls[0][0] == ("docker", "system", "dial-stdio")


def test_forced_transport_cleanup_cannot_be_success() -> None:
    calls: list[str] = []

    def wait(**_kwargs: object) -> int:
        if not calls:
            raise subprocess.TimeoutExpired("docker", 2)
        return 0

    process = SimpleNamespace(wait=wait, terminate=lambda: calls.append("terminate"))
    with pytest.raises(ComposeError, match="forced cleanup"):
        log_transport._reap(cast("subprocess.Popen[bytes]", process), completed=True)
    assert calls == ["terminate"]


def test_transport_cleanup_preserves_the_primary_phase_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace())

    def broken_cleanup(*_args: object, **_kwargs: object) -> None:
        raise OSError("private-cleanup-error")

    monkeypatch.setattr(log_transport, "_reap", broken_cleanup)
    with (
        pytest.raises(TimeoutError, match="header deadline") as caught,
        log_transport._transport(environment={}, cwd=tmp_path),
    ):
        raise TimeoutError("header deadline")
    assert caught.value.__notes__ == ["Log transport cleanup is also unproven."]


def test_handshake_deadline_interrupts_an_idle_read() -> None:
    with pytest.raises(TimeoutError, match="phase timed out"), log_transport._deadline(0.05):
        threading.Event().wait(2)
