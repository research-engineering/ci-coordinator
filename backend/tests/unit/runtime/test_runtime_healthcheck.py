from __future__ import annotations

import ast
import http.client
import inspect

import pytest

from ci_coordinator.runtime import healthcheck
from ci_coordinator.runtime_settings import UnsupportedPythonRuntime


def test_healthcheck_control_flow_survives_optimized_python() -> None:
    tree = ast.parse(inspect.getsource(healthcheck))

    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def test_healthcheck_rejects_an_unsupported_interpreter_before_network_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        healthcheck,
        "admit_python_runtime",
        lambda: UnsupportedPythonRuntime("PyPy", "3.13.14", "CPython", ("3.13.15",)),
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        healthcheck,
        "HTTPConnection",
        lambda *_args, **_kwargs: pytest.fail(
            "network access must not occur on an unsupported interpreter"
        ),
    )

    assert healthcheck.main({}) == 1


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def read(self, _amount: int | None = None) -> bytes:
        raise AssertionError("health probes must not consume response bodies")


class _Connection:
    def __init__(self, host: str, port: int, timeout: int, *, status: int = 200) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.status = status
        self.closed = False
        self.request_target: tuple[str, str] | None = None
        self.response = _Response(status)

    def request(self, method: str, path: str) -> None:
        self.request_target = (method, path)

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("bind_host", "probe_host"),
    [
        ("0.0.0.0", "127.0.0.1"),  # noqa: S104 - explicit wildcard falsifier
        ("::", "::1"),
        ("api.example.test", "api.example.test"),
    ],
)
def test_healthcheck_targets_the_admitted_listener(
    monkeypatch: pytest.MonkeyPatch,
    bind_host: str,
    probe_host: str,
) -> None:
    connections: list[_Connection] = []

    def connect(host: str, port: int, timeout: int) -> _Connection:
        connection = _Connection(host, port, timeout)
        connections.append(connection)
        return connection

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(healthcheck, "HTTPConnection", connect)

    result = healthcheck.main(
        {
            "CI_COORDINATOR_BIND_HOST": bind_host,
            "CI_COORDINATOR_BIND_PORT": "3080",
        }
    )

    assert result == 0
    assert len(connections) == 1
    connection = connections[0]
    assert (connection.host, connection.port, connection.timeout) == (probe_host, 3080, 3)
    assert connection.request_target == ("GET", "/healthz")
    assert connection.closed is True


def test_healthcheck_fails_closed_for_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_connection(_host: str, _port: int, _timeout: int) -> _Connection:
        raise AssertionError("invalid configuration must not open a connection")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(healthcheck, "HTTPConnection", unexpected_connection)

    assert healthcheck.main({"CI_COORDINATOR_BIND_HOST": "bad host"}) == 1
    assert healthcheck.main({"CI_COORDINATOR_BIND_PORT": "0"}) == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, 0), (503, 0), (204, 1), (302, 1), (401, 1), (404, 1), (429, 1), (500, 1)],
)
def test_healthcheck_distinguishes_protocol_progress_from_other_http_results(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: int,
) -> None:
    connection = _Connection("127.0.0.1", 3000, 3, status=status)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        healthcheck,
        "HTTPConnection",
        lambda _host, _port, timeout: connection,
    )

    assert healthcheck.main({}) == expected
    assert connection.closed is True


def test_healthcheck_maps_transport_failure_to_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingConnection(_Connection):
        def request(self, method: str, path: str) -> None:
            raise http.client.HTTPException(f"unavailable: {method} {path}")

    connection = _FailingConnection("127.0.0.1", 3000, 3)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        healthcheck,
        "HTTPConnection",
        lambda _host, _port, timeout: connection,
    )

    assert healthcheck.main({}) == 1
    assert connection.closed is True


def test_healthcheck_cleanup_failure_does_not_override_the_http_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CloseFailingConnection(_Connection):
        def close(self) -> None:
            raise OSError("simulated close failure")

    connection = _CloseFailingConnection("127.0.0.1", 3000, 3)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        healthcheck,
        "HTTPConnection",
        lambda _host, _port, timeout: connection,
    )

    assert healthcheck.main({}) == 0
