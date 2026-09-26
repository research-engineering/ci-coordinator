from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn
from fastapi import FastAPI, Response
from scripts.bounded_process import spawn
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import Receive, Scope, Send
from uvicorn.config import STARTUP_FAILURE
from uvicorn.protocols.http.h11_impl import H11Protocol

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    ObservabilityRouteDependencies,
)
from ci_coordinator.integrations import GitHubActionsJwksProvider
from ci_coordinator.integrations.github import GitHubAppTransportFactory
from ci_coordinator.observability import ReadinessStatus, RuntimeDiagnosticObserver, RuntimeMetrics
from ci_coordinator.runtime import __main__ as runtime_main
from ci_coordinator.runtime import application as runtime_application
from ci_coordinator.runtime.application import RuntimeApplication
from ci_coordinator.runtime.environment import load_runtime_settings_from_environment
from ci_coordinator.runtime.event_logging import runtime_event_logger
from ci_coordinator.runtime.resources import RuntimeResources
from ci_coordinator.runtime_settings import RuntimeSettingsRejection, redacted_settings_projection

ROOT = Path(__file__).resolve().parents[4]
PRIVATE = "synthetic-private-diagnostic-marker"


class _Resource:
    def __init__(self, scenario: str) -> None:
        self._scenario = scenario

    async def prepare(self) -> None:
        if self._scenario == "startup":
            raise RuntimeError(PRIVATE)

    async def aclose(self) -> None:
        pass

    async def dispose(self) -> None:
        pass


class _AfterStart(Response):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"100")]}
        )
        error = KeyError(PRIVATE)
        error.add_note(PRIVATE)
        raise error from ValueError(PRIVATE)


class _FailingStream:
    def __init__(self, stream: Any, scenario: str) -> None:
        self._stream = stream
        self._scenario = scenario
        self.writes = 0

    def write(self, value: str) -> int:
        self.writes += 1
        if self._scenario == "sink_always" or (self._scenario == "sink_once" and self.writes == 1):
            raise OSError(PRIVATE)
        return cast(int, self._stream.write(value))

    def flush(self) -> None:
        if self._scenario == "sink_flush":
            raise OSError(PRIVATE)
        self._stream.flush()


async def _request(port: int, path: str, *, malformed: bool = False) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(
            b"GET / HTTP/1.1\r\ninvalid-header\r\n\r\n"
            if malformed
            else f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        response = await reader.read(65_537)
        while not reader.at_eof() and len(response) <= 65_536:
            response += await reader.read(65_537 - len(response))
        assert len(response) <= 65_536
        return response
    finally:
        writer.close()
        await writer.wait_closed()


def _child(case: str, descriptor: int) -> int:
    scenario, sink_fault = case.split(":")
    assert scenario in {"normal", "startup", "shutdown"}
    assert sink_fault in {"none", "sink_once", "sink_always", "sink_flush"}
    assert version("uvicorn") == "0.53.0"
    assert version("starlette") == "1.6.0"
    events = runtime_event_logger()
    metrics = RuntimeMetrics()
    resource = _Resource(scenario)
    resources = RuntimeResources(
        cast(AsyncEngine, resource),
        cast(GitHubAppTransportFactory, resource),
        cast(GitHubActionsJwksProvider, resource),
        additional_resources=(resource,),
    )
    result: dict[str, Any] = {"responses": [], "served": []}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with resources.lifespan(app):
            yield
            if scenario == "shutdown":
                raise RuntimeError(PRIVATE)

    async def readiness() -> ReadinessStatus:
        return ReadinessStatus(ready=False, unavailable_dependencies=("runtime_mode_disabled",))

    app = create_app(
        HttpRouteDependencies(
            observability=ObservabilityRouteDependencies(
                readiness=readiness,
                metrics=metrics,
                request_logger=events,
                diagnostics=RuntimeDiagnosticObserver(events),
            )
        ),
        lifespan=lifespan,
        include_operator_ui=False,
    )

    @app.get("/before")
    async def before() -> Response:
        result["served"].append("before")
        raise RuntimeError(PRIVATE)

    @app.get("/after")
    async def after() -> Response:
        result["served"].append("after")
        return _AfterStart()

    with socket.socket(fileno=descriptor) as listener, pytest.MonkeyPatch.context() as patch:
        port = listener.getsockname()[1]
        settings = load_runtime_settings_from_environment(
            {
                "CI_COORDINATOR_RUNTIME_MODE": "disabled",
                "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
                "CI_COORDINATOR_BIND_PORT": str(port),
                "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "4",
            }
        )
        assert not isinstance(settings, RuntimeSettingsRejection)
        candidate = RuntimeApplication(app, redacted_settings_projection(settings))
        patch.setattr(runtime_main, "load_runtime_settings_from_environment", lambda: settings)
        patch.setattr(runtime_application, "compose_runtime_application", lambda _: candidate)

        def run(actual_app: Any, **options: Any) -> None:
            assert actual_app is app
            assert options == {
                "host": "127.0.0.1",
                "port": port,
                "access_log": False,
                "log_config": None,
                "proxy_headers": False,
                "forwarded_allow_ips": "",
                "limit_concurrency": 128,
                "timeout_graceful_shutdown": 2,
            }
            handler = logging.getLogger("uvicorn").handlers[0]
            server_events = cast(Any, handler)._events
            sink = cast(
                logging.StreamHandler[Any], logging.getLogger("ci_coordinator.events").handlers[0]
            )
            if sink_fault != "none":
                sink.setStream(_FailingStream(sink.stream, sink_fault))
            config = uvicorn.Config(actual_app, **options)
            config.load()
            assert config.http_protocol_class is H11Protocol
            assert logging.getLogger("uvicorn").handlers == [handler]
            server = uvicorn.Server(config)

            async def drive() -> None:
                try:
                    async with asyncio.timeout(8):
                        while not server.started:  # noqa: ASYNC110 - Uvicorn has no startup event; outer timeout bounds polling.
                            await asyncio.sleep(0.01)
                        for path in ("/healthz", "/before", "/after"):
                            response = await _request(port, path)
                            result["responses"].append(response.decode("ascii"))
                        result["malformedResponse"] = (
                            await _request(port, "/", malformed=True)
                        ).decode("ascii")
                finally:
                    server.should_exit = True

            async def exercise() -> None:
                driver = None if scenario == "startup" else asyncio.create_task(drive())
                try:
                    async with asyncio.timeout(12):
                        await server.serve(sockets=[listener])
                finally:
                    if driver is not None:
                        if not driver.done():
                            driver.cancel()
                        try:
                            await driver
                        except asyncio.CancelledError:
                            raise AssertionError("native request driver did not complete") from None

            try:
                asyncio.run(exercise())
            finally:
                result.update(
                    started=server.started,
                    startupFailed=server.lifespan.startup_failed,
                    shutdownFailed=server.lifespan.shutdown_failed,
                    logFailures=events.failure_count + server_events.failure_count,
                )
            if not server.started and not config.should_reload and config.workers == 1:
                raise SystemExit(STARTUP_FAILURE)

        patch.setattr(uvicorn, "run", run)
        try:
            code = runtime_main.main()
        except SystemExit as error:
            assert type(error.code) is int
            code = error.code
        result["exitCode"] = code
        print(json.dumps(result))
        return code


@pytest.mark.parametrize("scenario", ["normal", "startup", "shutdown"])
@pytest.mark.parametrize("sink_fault", ["none", "sink_once", "sink_always", "sink_flush"])
def test_actual_entrypoint_projects_native_h11_and_lifespan_failures(
    scenario: str, sink_fault: str
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        result = spawn(
            sys.executable,
            (
                str(Path(__file__).resolve()),
                "--server-child",
                f"{scenario}:{sink_fault}",
                str(listener.fileno()),
            ),
            cwd=ROOT,
            timeout_seconds=20,
            max_buffer=131_072,
            inherited_fds=(listener.fileno(),),
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": os.pathsep.join((str(ROOT / "backend/src"), str(ROOT))),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    assert result.failure_kind is None, result
    assert result.status == (3 if scenario == "startup" else 0), result
    assert PRIVATE not in result.stdout + result.stderr
    assert "--- Logging error ---" not in result.stderr
    assert "Traceback" not in result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["exitCode"] == result.status
    rows = [json.loads(line) for line in result.stderr.splitlines()]
    assert all(len(line.encode()) <= 65_536 for line in result.stderr.splitlines())
    reasons = [row.get("reason") for row in rows]
    assert receipt["shutdownFailed"] is (scenario == "shutdown")
    if sink_fault != "none":
        assert receipt["logFailures"] > 0
        if sink_fault == "sink_always":
            assert rows == []
        else:
            assert any(row["event"] == "structured_log_failure" for row in rows)
    else:
        assert receipt["logFailures"] == 0
    if scenario == "startup":
        assert receipt["responses"] == receipt["served"] == []
        assert receipt["started"] is False and receipt["startupFailed"] is True
        if sink_fault != "sink_always":
            assert "startup_failed" in reasons and "unclassified_server_event" in reasons
        assert "startup_complete" not in reasons
        return
    healthy, before, after = receipt["responses"]
    assert healthy.startswith("HTTP/1.1 200")
    assert before.startswith("HTTP/1.1 500") and '"code":"internal_error"' in before
    assert "cache-control: no-store" in before.lower() and "x-correlation-id:" in before.lower()
    assert after.startswith("HTTP/1.1 200") and after.count("HTTP/1.1") == 1
    assert after.partition("\r\n\r\n")[2] == ""
    assert receipt["served"] == ["before", "after"]
    assert receipt["malformedResponse"].startswith("HTTP/1.1 400")
    assert receipt["startupFailed"] is False
    if sink_fault != "sink_always":
        assert "startup_complete" in reasons and "asgi_exception" in reasons
        assert any(
            row.get("reason") == "invalid_http_request" and row["level"] == "WARNING"
            for row in rows
        )
        assert all(row["level"] == "ERROR" for row in rows if row.get("exceptionType"))
        assert any(
            row.get("stage") == "http_request" and row["exceptionType"] == "RuntimeError"
            for row in rows
        )
        assert ("shutdown_failed" in reasons) is (scenario == "shutdown")
        assert ("shutdown_complete" in reasons) is (scenario != "shutdown")
        if scenario == "shutdown":
            assert "unclassified_server_event" in reasons


if __name__ == "__main__":
    assert len(sys.argv) == 4 and sys.argv[1] == "--server-child"
    raise SystemExit(_child(sys.argv[2], int(sys.argv[3])))
