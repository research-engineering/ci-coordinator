"""Container health probe bound to the admitted runtime listener."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from http.client import HTTPConnection, HTTPException

from ci_coordinator.runtime.environment import snapshot_process_environment
from ci_coordinator.runtime_settings import admit_python_runtime, normalize_bind_host

_HOST = "CI_COORDINATOR_BIND_HOST"
_PORT = "CI_COORDINATOR_BIND_PORT"
_TIMEOUT_SECONDS = 3


def main(environment: Mapping[str, str] | None = None) -> int:
    if admit_python_runtime() is not None:
        return 1
    source = snapshot_process_environment() if environment is None else dict(environment)
    host = _probe_host(source.get(_HOST, "127.0.0.1"))
    port = _probe_port(source.get(_PORT, "3000"))
    if host is None or port is None:
        return 1
    connection = HTTPConnection(host, port, timeout=_TIMEOUT_SECONDS)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        # Admission can reject a probe before ASGI dispatch; 503 is not readiness.
        return 0 if response.status in {200, 503} else 1
    except (OSError, HTTPException):
        return 1
    finally:
        with suppress(OSError):
            connection.close()


def _probe_host(value: object) -> str | None:
    host = normalize_bind_host(value)
    if host == "0.0.0.0":  # noqa: S104 - an admitted wildcard maps to its loopback probe
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _probe_port(value: object) -> int | None:
    if type(value) is not str or not value.isascii() or not value.isdecimal():
        return None
    significant = value.lstrip("0")
    if not significant or len(significant) > 5:
        return None
    port = int(significant)
    return port if 1 <= port <= 65_535 else None


if __name__ == "__main__":
    raise SystemExit(main())
