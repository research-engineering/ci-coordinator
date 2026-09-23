from __future__ import annotations

import hashlib
import os
import socket
from collections.abc import Generator, Iterator
from importlib.metadata import version
from pathlib import Path
from typing import Never

import httpx2
import pytest
import requests
from schemathesis.python import asgi

from .harness import Harness
from .lifespan_compatibility import REQUALIFIED_SCHEMATHESIS_VERSION, ClosingLifespan
from .profile import campaign_from_environment

REPORT_BYTE_LIMIT = 64 * 1024
PACKAGE = Path(__file__).parent.resolve()


def pytest_configure(config: pytest.Config) -> None:
    manager = config.pluginmanager
    if not manager.has_plugin("schemathesis") and not manager.has_plugin(
        "schemathesis.pytest.plugin"
    ):
        manager.unblock("schemathesis")
        manager.import_plugin("schemathesis", consider_entry_points=True)


@pytest.fixture(autouse=True)
def close_native_lifespan_streams(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if version("schemathesis") != REQUALIFIED_SCHEMATHESIS_VERSION:
        raise pytest.UsageError("Requalify and retire the pinned ASGI lifespan cleanup repair")
    monkeypatch.setattr(asgi, "_Lifespan", ClosingLifespan)
    try:
        yield
    finally:
        asgi.shutdown_lifespans()


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts: list[str] = []
    initialize_session = requests.Session.__init__

    def isolated_session(session: requests.Session) -> None:
        initialize_session(session)
        session.trust_env = False

    monkeypatch.setattr(requests.Session, "__init__", isolated_session)

    def forbidden(*args: object, **kwargs: object) -> Never:
        attempts.append("real network transport")
        raise AssertionError("Real network transport is forbidden in API contract tests")

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", forbidden)
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", forbidden)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", forbidden)
    yield
    assert not attempts, "An outbound attempt was caught or swallowed by application middleware"


@pytest.fixture
def harness() -> Harness:
    return Harness()


def pytest_report_header() -> str:
    campaign = campaign_from_environment()
    return (
        f"API contract: Schemathesis={version('schemathesis')} "
        f"Hypothesis={version('hypothesis')} profile={campaign.name} "
        f"seed={campaign.seed} examples={campaign.max_examples}/operation"
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    target = os.environ.get("CI_COORDINATOR_API_ARTIFACTS")
    if not target or not report.failed or not item.path.resolve().is_relative_to(PACKAGE):
        return report
    directory = Path(target).resolve()
    if ".ci-native" in directory.parts:
        report.sections.append(
            ("API diagnostics", "Invalid artifact directory; evidence incomplete")
        )
        return report
    key = hashlib.sha256(f"{report.nodeid}:{report.when}".encode()).hexdigest()
    payload = (pytest_report_header() + "\n" + report.longreprtext).encode("utf-8")
    if len(payload) > REPORT_BYTE_LIMIT:
        marker = b"\n[report truncated at 64 KiB; full pytest output may contain the exact case]\n"
        payload = payload[: REPORT_BYTE_LIMIT - len(marker)] + marker
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.txt").write_bytes(payload)
    except OSError:
        report.sections.append(("API diagnostics", "Storage unavailable; evidence incomplete"))
    return report
