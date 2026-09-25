"""Browser transport fixture; API mocks, not identity or provider qualification."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import uvicorn

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    ObservabilityRouteDependencies,
)
from ci_coordinator.observability import ReadinessStatus, RuntimeMetrics


async def _ready() -> ReadinessStatus:
    return ReadinessStatus(True, ())


class _BrowserServer(uvicorn.Server):
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if not self.started or sockets is None:
            raise RuntimeError("operator browser transport did not start")
        print(json.dumps({"origin": f"http://127.0.0.1:{sockets[0].getsockname()[1]}"}), flush=True)


def main() -> None:
    app = create_app(
        HttpRouteDependencies(
            observability=ObservabilityRouteDependencies(readiness=_ready, metrics=RuntimeMetrics())
        ),
        operator_ui_directory=Path(sys.argv[1]),
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        _BrowserServer(
            uvicorn.Config(app, log_level="error", access_log=False, timeout_graceful_shutdown=3)
        ).run(sockets=[listener])


if __name__ == "__main__":
    main()
