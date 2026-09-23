from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.dev_environment import debug as debug_adapter
from scripts.dev_environment import debug_witness
from scripts.dev_environment.compose import (
    CommandResult,
    ComposeError,
    LocalEndpoints,
    ProviderInvocation,
    ServiceRuntimeIdentity,
    ServiceStatus,
)
from scripts.dev_environment.debug import BackendDebugger, DebugError
from scripts.dev_environment.debug_witness import (
    DebugWitnessError,
    backend_attach_configuration,
    verify_backend_debugger,
)
from scripts.dev_environment.lifecycle import InstanceMutationLease, OperationBlocked

_ADAPTER_ENDPOINTS = (
    b'{"client": {"host": "0.0.0.0", "port": 5678}, '
    b'"server": {"host": "127.0.0.1", "port": 47621}}\n'
)


class ScriptedSocket:
    def __init__(
        self,
        *,
        wrong_source: bool = False,
        missing_stop: bool = False,
        close_command: str | None = None,
    ) -> None:
        self.buffer = bytearray()
        self.requests: list[dict[str, object]] = []
        self.closed = False
        self.attach_sequence = 0
        self.source = ""
        self.line = 0
        self.wrong_source = wrong_source
        self.missing_stop = missing_stop
        self.close_command = close_command

    def settimeout(self, value: float) -> None:
        assert 0 < value <= 45

    def sendall(self, data: bytes) -> None:
        header, _, body = data.partition(b"\r\n\r\n")
        assert int(header.removeprefix(b"Content-Length: ")) == len(body)
        request = json.loads(body)
        self.requests.append(request)
        sequence = request["seq"]
        command = request["command"]
        arguments = request["arguments"]
        if command == self.close_command and command != "disconnect":
            return
        response: dict[str, object] = {}
        if command == "initialize":
            self.queue({"type": "event", "event": "output", "body": {"output": "ignored"}})
            response = {"supportsConfigurationDoneRequest": True}
        elif command == "attach":
            self.attach_sequence = sequence
            self.queue({"type": "event", "event": "initialized"})
            return
        elif command == "setBreakpoints":
            self.source = arguments["source"]["path"]
            if arguments["breakpoints"]:
                self.line = arguments["breakpoints"][0]["line"]
                response = {"breakpoints": [{"verified": False}]}
            else:
                response = {"breakpoints": []}
        elif command == "configurationDone":
            if not self.missing_stop:
                self.queue(
                    {
                        "type": "event",
                        "event": "stopped",
                        "body": {"reason": "breakpoint", "threadId": 7},
                    }
                )
            self.reply(self.attach_sequence, "attach", {})
        elif command == "stackTrace":
            path = "/unmapped/file.py" if self.wrong_source else self.source
            response = {"stackFrames": [{"id": 1, "line": self.line, "source": {"path": path}}]}
        elif command == "continue":
            self.queue({"type": "event", "event": "continued", "body": {"threadId": 7}})
        elif command == "disconnect":
            self.queue({"type": "event", "event": "terminated"})
            if self.close_command == command:
                return
        self.reply(sequence, command, response)

    def reply(self, sequence: int, command: str, body: dict[str, object]) -> None:
        self.queue(
            {
                "type": "response",
                "request_seq": sequence,
                "command": command,
                "success": True,
                "body": body,
            }
        )

    def queue(self, value: dict[str, object]) -> None:
        body = json.dumps(value).encode()
        self.buffer.extend(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

    def recv(self, size: int) -> bytes:
        count = min(size, 3)
        chunk = bytes(self.buffer[:count])
        del self.buffer[:count]
        return chunk

    def close(self) -> None:
        self.closed = True


def _source(root: Path) -> Path:
    source = root / "backend/src/ci_coordinator/runtime/__main__.py"
    source.parent.mkdir(parents=True)
    source.write_text("def main():\n    marker = 1\n    return marker\n", encoding="utf-8")
    return source


def test_attach_configuration_maps_real_source_and_loopback(tmp_path: Path) -> None:
    _source(tmp_path)
    configuration = backend_attach_configuration(tmp_path, 54321)
    assert configuration["connect"] == {"host": "127.0.0.1", "port": 54321}
    assert configuration["pathMappings"] == [
        {
            "localRoot": str((tmp_path / "backend/src").resolve()),
            "remoteRoot": "/workspace/backend/src",
        }
    ]
    assert configuration["subProcess"] is False


@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_attach_rejects_invalid_dynamic_port(tmp_path: Path, port: int) -> None:
    with pytest.raises(DebugWitnessError, match="port is invalid"):
        backend_attach_configuration(tmp_path, port)


def test_breakpoint_and_continue_require_correlated_fragmented_dap_exchange(tmp_path: Path) -> None:
    source = _source(tmp_path)
    transport = ScriptedSocket()

    def connect(address: tuple[str, int], timeout: float) -> ScriptedSocket:
        assert address == ("127.0.0.1", 54321)
        assert 0 < timeout <= 1
        return transport

    result = verify_backend_debugger(tmp_path, port=54321, connect=connect)
    assert result.state == "breakpoint_continued"
    assert result.breakpoint_line == 2
    assert result.thread_id == 7
    assert len(result.source_sha256) == 64
    assert transport.source == str(source.resolve())
    assert transport.closed
    assert [request["command"] for request in transport.requests] == [
        "initialize",
        "attach",
        "setBreakpoints",
        "configurationDone",
        "stackTrace",
        "setBreakpoints",
        "continue",
        "disconnect",
    ]
    assert transport.requests[-1]["arguments"] == {"terminateDebuggee": False}
    assert not any(request["command"] == "evaluate" for request in transport.requests)


@pytest.mark.parametrize("wrong_source,missing_stop", [(True, False), (False, True)])
def test_successful_attach_alone_never_proves_breakpoint_continue(
    tmp_path: Path, wrong_source: bool, missing_stop: bool
) -> None:
    _source(tmp_path)
    transport = ScriptedSocket(wrong_source=wrong_source, missing_stop=missing_stop)
    with pytest.raises(DebugWitnessError):
        verify_backend_debugger(tmp_path, port=54321, connect=lambda *_: transport)
    assert transport.closed
    assert not any(request["command"] == "continue" for request in transport.requests)


@pytest.mark.parametrize(
    ("command", "waiting"),
    [
        ("initialize", "response:initialize"),
        ("attach", "event:initialized"),
        ("setBreakpoints", "response:setBreakpoints"),
        ("configurationDone", "response:configurationDone"),
        ("stackTrace", "response:stackTrace"),
        ("continue", "response:continue"),
        ("disconnect", "response:disconnect"),
    ],
)
def test_dap_eof_remains_failure_with_closed_protocol_phase(
    tmp_path: Path, command: str, waiting: str
) -> None:
    _source(tmp_path)
    transport = ScriptedSocket(close_command=command)

    with pytest.raises(DebugWitnessError) as caught:
        verify_backend_debugger(tmp_path, port=54321, connect=lambda *_: transport)

    assert str(caught.value) == (
        f"debugger closed the protocol connection; waiting={waiting}; last_request={command}"
    )
    assert transport.closed
    assert transport.requests[-1]["command"] == command


def test_dap_eof_distinguishes_missing_breakpoint_from_completed_attach(tmp_path: Path) -> None:
    _source(tmp_path)
    transport = ScriptedSocket(missing_stop=True)

    with pytest.raises(DebugWitnessError) as caught:
        verify_backend_debugger(tmp_path, port=54321, connect=lambda *_: transport)

    assert str(caught.value) == (
        "debugger closed the protocol connection; "
        "waiting=event:stopped; last_request=configurationDone"
    )
    assert transport.closed
    assert not any(request["command"] == "continue" for request in transport.requests)


@pytest.mark.parametrize("kind", ["request", "event"])
def test_dap_eof_diagnostic_never_renders_unadmitted_names_or_payload(kind: str) -> None:
    transport = ScriptedSocket(close_command="private-command")
    transport.queue({"type": "event", "event": "private-event", "body": {"output": "secret"}})
    dap = debug_witness._DAP(transport, deadline=1, clock=lambda: 0)

    with pytest.raises(DebugWitnessError) as caught:
        if kind == "request":
            dap.call("private-command", {"path": "/private/source", "value": "secret"})
        else:
            dap.event("private-expected-event")

    expected = (
        "response:unknown; last_request=unknown"
        if kind == "request"
        else ("event:unknown; last_request=none")
    )
    assert str(caught.value) == "debugger closed the protocol connection; waiting=" + expected


@pytest.mark.parametrize(
    "payload",
    [
        b"Content-Length: 999999\r\n\r\n",
        b"Content-Length: -1\r\n\r\n",
        b"Wrong-Header: 5\r\n\r\n",
        b"Content-Length: 2\r\n\r\n[]",
    ],
)
def test_dap_rejects_invalid_framing_before_admitting_a_message(payload: bytes) -> None:
    transport = ScriptedSocket()
    transport.buffer.extend(payload)
    dap = debug_witness._DAP(transport, deadline=1, clock=lambda: 0)
    with pytest.raises(DebugWitnessError):
        dap.event("initialized")


def test_unsolicited_events_cannot_extend_message_budget() -> None:
    transport = ScriptedSocket()
    for _ in range(257):
        transport.queue({"type": "event", "event": "output"})
    dap = debug_witness._DAP(transport, deadline=1, clock=lambda: 0)
    with pytest.raises(DebugWitnessError, match="message bound"):
        dap.event("initialized")


def test_elapsed_deadline_cannot_be_replaced_by_adapter_activity() -> None:
    dap = debug_witness._DAP(ScriptedSocket(), deadline=1, clock=lambda: 2)
    with pytest.raises(DebugWitnessError, match="deadline exceeded"):
        dap.event("initialized")


def test_source_mapping_rejects_foreign_symlink(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/src").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(DebugWitnessError, match="escaped"):
        backend_attach_configuration(tmp_path, 54321)


class DebugProject:
    def __init__(self, root: Path) -> None:
        self.repo_root = root.resolve()
        self.lease = InstanceMutationLease(
            hashlib.sha256(str(self.repo_root).encode()).hexdigest(), 99
        )
        self.events: list[str] = []
        self.foreign = False
        self.mode = "normal"
        self.container_id = "a" * 64
        self.healthy = True
        self.adapter_endpoints_path = ""

    def validate(self) -> None:
        self.events.append("validate")
        if self.foreign:
            raise ComposeError("foreign instance")

    def watch_invocation(self) -> ProviderInvocation:
        self.events.append("ownership")
        if self.foreign:
            raise ComposeError("foreign instance")
        return ProviderInvocation(
            (
                "docker",
                "compose",
                "--project-name",
                "owned",
                "--env-file",
                "/private/runtime.env",
                "--file",
                str(self.repo_root / "compose.yaml"),
                "watch",
                "--no-up",
            ),
            self.repo_root,
            {"PATH": "/bin"},
        )

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]:
        return tuple(
            ServiceStatus(
                service,
                "running",
                "healthy" if service != "backend" or self.healthy else "unhealthy",
                0,
                self.container_id,
            )
            for service in ("backend", "postgres", "frontend")
        )

    @contextmanager
    def observation_budget(self, seconds: float = 30) -> Iterator[None]:
        assert 0 < seconds <= 30
        yield

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
        assert service == "backend"
        return ServiceRuntimeIdentity(self.container_id, "2026-09-08T00:00:00Z")

    def service_file(self, service: str, path: str) -> bytes | None:
        assert service == "backend" and path == self.adapter_endpoints_path
        self.events.append("listener-observation")
        return _ADAPTER_ENDPOINTS

    def assert_runtime_boundary(
        self, service: str, *, expected_uid: int, protected_path: str | None
    ) -> None:
        assert service == "backend" and expected_uid == 65532
        assert protected_path == "/run/ci-coordinator-secrets/runtime-dsn"
        self.events.append("runtime-boundary")

    def endpoints(self) -> LocalEndpoints:
        return LocalEndpoints(
            "http://127.0.0.1:3001", "http://127.0.0.1:5174", "postgresql://127.0.0.1:5433"
        )


class DebugRunner:
    def __init__(
        self,
        project: DebugProject,
        *,
        fail_config: bool = False,
        fail_debug_up: bool = False,
        public_port: bool = False,
    ) -> None:
        self.project = project
        self.fail_config = fail_config
        self.fail_debug_up = fail_debug_up
        self.public_port = public_port
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult:
        assert cwd == self.project.repo_root and capture
        assert env["PATH"] == "/bin"
        assert timeout_seconds is not None and 0 < timeout_seconds <= 300
        args = tuple(argv)
        if args.count("--file") == 2:
            assert set(env) == {"PATH", "CI_COORDINATOR_DEBUGPY_ENDPOINTS_FILE"}
            self.project.adapter_endpoints_path = env["CI_COORDINATOR_DEBUGPY_ENDPOINTS_FILE"]
        else:
            assert env == {"PATH": "/bin"}
        self.calls.append(args)
        if "config" in args:
            return CommandResult(1 if self.fail_config else 0)
        if "up" in args:
            assert self.project.events[-1] == "ownership"
            self.project.events.append("mutation")
            if args.count("--file") == 2:
                self.project.mode = "debug"
                self.project.healthy = False
                self.project.container_id = "b" * 64
                return CommandResult(1 if self.fail_debug_up else 0)
            self.project.mode = "normal"
            self.project.healthy = True
            self.project.container_id = "c" * 64
        if args[-3:] == ("port", "backend", "5678"):
            return CommandResult(0, "127.0.0.1:54321\n")
        if "inspect" in args:
            ports = {}
            if self.project.mode == "debug":
                ports = {
                    "5678/tcp": [
                        {
                            "HostIp": "0.0.0.0" if self.public_port else "127.0.0.1",  # noqa: S104 - rejected provider observation
                            "HostPort": "54321",
                        }
                    ]
                }
            return CommandResult(0, json.dumps(ports))
        return CommandResult(0)


def _debugger(root: Path, **options: bool) -> tuple[BackendDebugger, DebugProject, DebugRunner]:
    _source(root)
    override = root / "docker/development/compose.debug.yaml"
    override.parent.mkdir(parents=True)
    override.write_bytes(
        (Path(__file__).resolve().parents[2] / "docker/development/compose.debug.yaml").read_bytes()
    )
    project = DebugProject(root)
    runner = DebugRunner(project, **options)
    return BackendDebugger(project, operation_lease=project.lease, runner=runner), project, runner


def test_debug_lifecycle_keeps_waiting_attach_distinct_and_restores_only_backend(
    tmp_path: Path,
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    endpoint = debugger.start()
    assert endpoint.state == "awaiting_debugger"
    assert endpoint.port == 54321 and endpoint.host == "127.0.0.1"
    debug_up = next(args for args in runner.calls if "up" in args)
    assert "--wait" not in debug_up and "--no-deps" in debug_up
    project.healthy = True
    assert debugger.wait_ready().state == "ready"
    assert debugger.restore() == project.endpoints()
    assert debugger.restore() is None
    restore_up = [args for args in runner.calls if "up" in args][-1]
    assert restore_up.count("--file") == 1
    assert (
        restore_up[-1] == "backend" and "--wait" in restore_up and "--force-recreate" in restore_up
    )
    assert all("down" not in args and "--volumes" not in args for args in runner.calls)


@pytest.mark.parametrize(
    ("backend", "detail"),
    [
        (None, "backend=missing"),
        (
            ServiceStatus("backend", "exited", "", 17, "b" * 64),
            "state=exited; health=none; exit=17; same_container=1; id_width=64",
        ),
        (
            ServiceStatus("backend", "running", "healthy", 0, "b" * 12 + "c" * 52),
            "state=running; health=healthy; exit=0; same_container=0; id_width=64",
        ),
        (
            ServiceStatus("backend", "running", "healthy", 0, "b" * 12),
            "state=running; health=healthy; exit=0; same_container=0; id_width=12",
        ),
        (
            ServiceStatus("backend", "private-state", "private-health", -1, "private-id"),
            "state=unknown; health=unknown; exit=unknown; same_container=0; id_width=unknown",
        ),
    ],
    ids=["missing", "exited", "same-prefix-replacement", "truncated", "private-observation"],
)
def test_wait_ready_rejects_changed_or_stopped_backend_with_bounded_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: ServiceStatus | None,
    detail: str,
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    endpoint = debugger.start()
    before = list(runner.calls)
    monkeypatch.setattr(project, "diagnostic_status", lambda: () if backend is None else (backend,))

    def unexpected_wait(_: float) -> None:
        pytest.fail("an invalid backend observation must fail before retrying")

    monkeypatch.setattr("scripts.dev_environment.debug.time.sleep", unexpected_wait)
    with pytest.raises(DebugError) as caught:
        debugger.wait_ready()
    assert str(caught.value) == (
        "debugger backend changed or stopped while awaiting attach; " + detail
    )
    assert endpoint.state == "awaiting_debugger"
    assert runner.calls == before
    assert debugger.restore() == project.endpoints()


def test_wait_ready_rejects_restart_of_same_healthy_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debugger, project, _ = _debugger(tmp_path)
    endpoint = debugger.start()
    project.healthy = True
    monkeypatch.setattr(
        project,
        "service_runtime_identity",
        lambda _: ServiceRuntimeIdentity(project.container_id, "2026-09-08T00:00:01Z"),
    )
    with pytest.raises(DebugError, match="runtime restarted"):
        debugger.wait_ready()
    assert endpoint.state == "awaiting_debugger"
    assert debugger.restore() == project.endpoints()


def test_invalid_debug_model_has_no_mutation_or_restore(tmp_path: Path) -> None:
    debugger, project, runner = _debugger(tmp_path, fail_config=True)
    with pytest.raises(DebugError):
        debugger.start()
    assert debugger.restore() is None
    assert "mutation" not in project.events
    assert all("up" not in args for args in runner.calls)


@pytest.mark.parametrize("service", ["backend", "postgres", "frontend"])
@pytest.mark.parametrize(
    ("state", "health"),
    [("running", "starting"), ("running", "unhealthy"), ("exited", "healthy"), ("missing", "")],
)
def test_debug_start_still_rejects_each_nonready_core_service_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    state: str,
    health: str,
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    statuses = tuple(item for item in project.diagnostic_status() if item.service != service)
    if state != "missing":
        statuses = (*statuses, ServiceStatus(service, state, health, 0))
    monkeypatch.setattr(project, "diagnostic_status", lambda: statuses)
    with pytest.raises(DebugError, match="ordinary healthy stack") as caught:
        debugger.start()
    assert f"{service}={state}" in str(caught.value)
    assert debugger.restore() is None
    assert "mutation" not in project.events
    assert all("up" not in args for args in runner.calls)


def test_completed_one_shots_do_not_block_debug_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debugger, project, _ = _debugger(tmp_path)
    ordinary_status = project.diagnostic_status
    completed = tuple(
        ServiceStatus(service, "exited", "", 0)
        for service in ("database-provision", "migrate", "database-access")
    )
    monkeypatch.setattr(project, "diagnostic_status", lambda: (*ordinary_status(), *completed))
    assert debugger.start().state == "awaiting_debugger"
    assert debugger.restore() == project.endpoints()


def test_uncertain_debug_up_still_requires_deterministic_restore(tmp_path: Path) -> None:
    debugger, project, _ = _debugger(tmp_path, fail_debug_up=True)
    with pytest.raises(DebugError):
        debugger.start()
    assert project.mode == "debug"
    assert debugger.restore() == project.endpoints()
    assert project.mode == "normal"


def test_foreign_resource_blocks_restore_before_provider_mutation(tmp_path: Path) -> None:
    debugger, project, runner = _debugger(tmp_path)
    debugger.start()
    prior_calls = len(runner.calls)
    project.foreign = True
    with pytest.raises(ComposeError, match="foreign"):
        debugger.restore()
    assert len(runner.calls) == prior_calls


def test_public_debug_binding_is_rejected_and_can_be_restored(tmp_path: Path) -> None:
    debugger, project, _ = _debugger(tmp_path, public_port=True)
    with pytest.raises(DebugError, match="published binding"):
        debugger.start()
    assert debugger.restore() == project.endpoints()


def test_override_is_immutable_for_start_but_not_required_for_ordinary_restore(
    tmp_path: Path,
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    override = tmp_path / "docker/development/compose.debug.yaml"
    override.write_bytes(override.read_bytes() + b"# changed\n")
    with pytest.raises(DebugError, match="override changed"):
        debugger.start()
    assert all("up" not in args for args in runner.calls)
    debugger = BackendDebugger(project, operation_lease=project.lease, runner=runner)
    debugger.start()
    override.unlink()
    assert debugger.restore() == project.endpoints()


def test_attach_timeout_remains_unready_and_can_be_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debugger, project, _ = _debugger(tmp_path)
    debugger.start()
    now = [0.0]
    monkeypatch.setattr("scripts.dev_environment.debug.time.monotonic", lambda: now[0])

    def wait(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr("scripts.dev_environment.debug.time.sleep", wait)
    with pytest.raises(DebugError, match="deadline exceeded"):
        debugger.wait_ready(timeout_seconds=1)
    assert debugger.restore() == project.endpoints()


def test_debug_start_waits_for_the_observed_privilege_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debugger, project, _ = _debugger(tmp_path)
    original = project.assert_runtime_boundary
    calls = [0]

    def boundary(service: str, *, expected_uid: int, protected_path: str | None) -> None:
        calls[0] += 1
        if calls[0] == 1:
            raise ComposeError("entrypoint is still copying private files")
        original(service, expected_uid=expected_uid, protected_path=protected_path)

    monkeypatch.setattr(project, "assert_runtime_boundary", boundary)
    monkeypatch.setattr("scripts.dev_environment.debug.time.sleep", lambda _: None)
    assert debugger.start().state == "awaiting_debugger"
    assert calls[0] == 2
    assert debugger.restore() == project.endpoints()


@pytest.mark.parametrize("loopback", [b"127.0.0.1", b"::1"])
def test_native_adapter_endpoints_admit_only_bound_owned_listener(loopback: bytes) -> None:
    assert debug_adapter._adapter_listener_ready(_ADAPTER_ENDPOINTS.replace(b"127.0.0.1", loopback))


@pytest.mark.parametrize("content", [None, b"", b'{"client":'])
def test_native_adapter_endpoint_file_can_still_be_unpublished(content: bytes | None) -> None:
    assert debug_adapter._adapter_listener_ready(content) is False


@pytest.mark.parametrize(
    "content",
    [
        b"x" * 1025,
        b"[]\n",
        b"{\n",
        b"\xff\n",
        b'{"error": "private adapter error"}\n',
        _ADAPTER_ENDPOINTS.replace(b"5678", b"true"),
        _ADAPTER_ENDPOINTS.replace(b"5678", b"5679"),
        _ADAPTER_ENDPOINTS.replace(b"0.0.0.0", b"127.0.0.1"),
        _ADAPTER_ENDPOINTS.replace(b"127.0.0.1", b"192.0.2.1"),
        _ADAPTER_ENDPOINTS.replace(b"47621", b"65536"),
        _ADAPTER_ENDPOINTS.replace(b"47621", b"0"),
        _ADAPTER_ENDPOINTS.replace(b"47621", b"NaN"),
        _ADAPTER_ENDPOINTS.replace(b'"port": 5678', b'"port": 1, "port": 5678'),
        _ADAPTER_ENDPOINTS.replace(b'"port": 5678', b'"private": "value", "port": 5678'),
    ],
)
def test_native_adapter_endpoint_file_rejects_complete_invalid_evidence(content: bytes) -> None:
    with pytest.raises(DebugError, match=r"^debugger endpoint observation") as caught:
        debug_adapter._adapter_listener_ready(content)
    assert "private" not in str(caught.value)


def test_debug_start_does_not_publish_endpoint_before_native_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    samples = iter((None, b'{"client":', _ADAPTER_ENDPOINTS))
    sleeps: list[float] = []

    def observe(service: str, path: str) -> bytes | None:
        assert service == "backend" and path == project.adapter_endpoints_path
        assert not any("port" in command for command in runner.calls)
        return next(samples)

    monkeypatch.setattr(project, "service_file", observe)
    monkeypatch.setattr("scripts.dev_environment.debug.time.sleep", sleeps.append)

    assert debugger.start().state == "awaiting_debugger"
    assert len(sleeps) == 2
    assert debugger.restore() == project.endpoints()


@pytest.mark.parametrize("late_publication", [False, True])
def test_debug_listener_wait_keeps_existing_deadline_and_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, late_publication: bool
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    now = [0.0]
    monkeypatch.setattr("scripts.dev_environment.debug.time.monotonic", lambda: now[0])

    def observe(_service: str, _path: str) -> bytes | None:
        if late_publication:
            now[0] = 30
            return _ADAPTER_ENDPOINTS
        return None

    def wait(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr("scripts.dev_environment.debug.time.sleep", wait)
    monkeypatch.setattr(project, "service_file", observe)
    with pytest.raises(DebugError, match="privilege/listener transition did not complete"):
        debugger.start()
    assert now[0] == 30
    assert not any("port" in command for command in runner.calls)
    assert debugger.restore() == project.endpoints()


def test_debug_listener_file_is_unique_per_transition(tmp_path: Path) -> None:
    first, project, _ = _debugger(tmp_path)
    first.start()
    first_path = project.adapter_endpoints_path
    first.restore()
    first.start()
    assert project.adapter_endpoints_path != first_path
    assert Path(project.adapter_endpoints_path).parent == Path("/tmp")  # noqa: S108 -- isolated container path
    assert first.restore() == project.endpoints()


def test_debug_listener_file_cannot_admit_a_restarted_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    identity = project.service_runtime_identity
    restarted = [False]

    def observe(service: str, path: str) -> bytes:
        assert service == "backend" and path == project.adapter_endpoints_path
        restarted[0] = True
        return _ADAPTER_ENDPOINTS

    def current(service: str) -> ServiceRuntimeIdentity:
        actual = identity(service)
        if restarted[0]:
            return ServiceRuntimeIdentity(actual.container_id, "2026-09-08T00:00:01Z")
        return actual

    monkeypatch.setattr(project, "service_file", observe)
    monkeypatch.setattr(project, "service_runtime_identity", current)
    with pytest.raises(DebugError, match="changed during listener observation"):
        debugger.start()
    assert not any("port" in command for command in runner.calls)
    assert debugger.restore() == project.endpoints()


@pytest.mark.parametrize("status,failure_kind", [(None, "timeout"), (0, "residual-descendant")])
def test_incomplete_provider_result_never_becomes_debug_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int | None, failure_kind: str
) -> None:
    result = SimpleNamespace(status=status, failure_kind=failure_kind, stdout="provider details")
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(debug_adapter, "spawn", lambda *_args, **_kwargs: result)
    with pytest.raises(DebugError, match=f"operation=process; .*failure={failure_kind}"):
        debug_adapter._run(["docker", "compose"], cwd=tmp_path, env={}, timeout_seconds=1)


@pytest.mark.parametrize("operation", ["config", "start", "port", "restore", "inspect"])
@pytest.mark.parametrize("timeout", [False, True])
def test_debug_failures_identify_the_actual_operation_without_provider_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, timeout: bool
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    if operation == "restore":
        debugger.start()

    def failing(
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult:
        selected = (operation in ("config", "port", "inspect") and operation in argv) or (
            operation in ("start", "restore") and "up" in argv
        )
        if selected:
            if timeout:
                raise debug_adapter.DebugProviderFailure("process", None, "timeout")
            return CommandResult(17, "secret-provider-output")
        return runner(argv, cwd=cwd, capture=capture, env=env, timeout_seconds=timeout_seconds)

    debugger._runner = failing
    clock = iter(range(0, 100_000_000, 1_000_000))
    monkeypatch.setattr("scripts.dev_environment.debug.time.monotonic_ns", lambda: next(clock))
    with pytest.raises(debug_adapter.DebugProviderFailure) as caught:
        if operation == "restore":
            debugger.restore()
        else:
            debugger.start()
    message = str(caught.value)
    assert message == (
        f"bounded debug provider operation failed; operation={operation}; "
        f"status={'unknown' if timeout else 17}; "
        f"failure={'timeout' if timeout else 'exit'}; elapsed_ms=1"
    )
    assert "secret" not in message and "/private" not in message
    debugger._runner = runner
    if operation != "config":
        assert debugger.restore() == project.endpoints()
    else:
        assert debugger.restore() is None


def test_debug_mutations_inherit_the_active_root_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debugger, project, runner = _debugger(tmp_path)
    debugger._runner = None
    observed: list[tuple[int, ...]] = []

    def run(
        command: str,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
        max_buffer: int,
        inherited_fds: Sequence[int],
    ) -> SimpleNamespace:
        observed.append(tuple(inherited_fds))
        response = runner((command, *args), cwd=cwd, env=env, timeout_seconds=timeout_seconds)
        return SimpleNamespace(status=response.status, stdout=response.stdout, failure_kind=None)

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(debug_adapter, "spawn", run)
    debugger.start()
    debugger.restore()
    assert len([args for args in runner.calls if "up" in args]) == 2
    assert observed and all(descriptors == project.lease.inherited_fds for descriptors in observed)


def test_expired_debug_lease_blocks_mutation_even_with_an_admitted_runner(tmp_path: Path) -> None:
    debugger, project, runner = _debugger(tmp_path)
    project.lease._expire()
    with pytest.raises(OperationBlocked):
        debugger.start()
    assert all("up" not in args for args in runner.calls)


def test_foreign_debug_lease_is_rejected_before_provider_observation(tmp_path: Path) -> None:
    _, project, runner = _debugger(tmp_path)
    before = list(project.events)
    with pytest.raises(DebugError, match="lease does not match"):
        BackendDebugger(project, operation_lease=InstanceMutationLease("0" * 64, 99), runner=runner)
    assert project.events == before and runner.calls == []
