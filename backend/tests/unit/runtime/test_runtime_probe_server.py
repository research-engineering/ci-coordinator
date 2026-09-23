from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from http.client import HTTPConnection

import uvicorn
from starlette.types import Receive, Scope, Send

from ci_coordinator.runtime import healthcheck


def test_cold_probe_import_does_not_construct_the_application_graph() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ci_coordinator.runtime.healthcheck; import json, sys; "
            "print(json.dumps(sorted(set(name.split('.')[0] for name in sys.modules))))",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )

    loaded = set(json.loads(result.stdout))
    assert "ci_coordinator" in loaded
    assert not loaded.intersection({"fastapi", "sqlalchemy", "uvicorn", "httpx"})


def test_probe_survives_real_server_admission_and_recovers_after_saturation() -> None:
    async def exercise() -> None:
        dispatched: list[str] = []

        async def app(scope: Scope, _receive: Receive, send: Send) -> None:
            dispatched.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"alive"})

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    http="h11",
                    ws="none",
                    lifespan="off",
                    limit_concurrency=2,
                    access_log=False,
                    log_config=None,
                    timeout_graceful_shutdown=1,
                )
            )
            running = asyncio.create_task(server.serve(sockets=[listener]))
            writer: asyncio.StreamWriter | None = None
            environment = {
                "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
                "CI_COORDINATOR_BIND_PORT": str(port),
            }
            try:
                async with asyncio.timeout(10):
                    while not server.started:
                        if running.done():
                            await running
                            raise AssertionError("server exited before startup")
                        await asyncio.sleep(0.01)
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.write(b"GET /held HTTP/1.1\r\nHost: localhost\r\n")
                    await writer.drain()
                    while not server.server_state.connections:  # noqa: ASYNC110 - no Uvicorn event; outer deadline bounds polling
                        await asyncio.sleep(0.01)

                    def probe_status() -> int:
                        connection = HTTPConnection("127.0.0.1", port, timeout=3)
                        try:
                            connection.request("GET", "/healthz")
                            return connection.getresponse().status
                        finally:
                            connection.close()

                    assert await asyncio.to_thread(probe_status) == 503
                    assert await asyncio.to_thread(healthcheck.main, environment) == 0
                    assert dispatched == []

                    writer.close()
                    await writer.wait_closed()
                    writer = None
                    while server.server_state.connections:  # noqa: ASYNC110 - no Uvicorn event; outer deadline bounds polling
                        await asyncio.sleep(0.01)
                    assert await asyncio.to_thread(healthcheck.main, environment) == 0
                    assert dispatched == ["/healthz"]
            finally:
                try:
                    if writer is not None:
                        writer.close()
                        await asyncio.wait_for(writer.wait_closed(), timeout=2)
                finally:
                    server.should_exit = True
                    await asyncio.wait_for(running, timeout=5)

    asyncio.run(exercise())
