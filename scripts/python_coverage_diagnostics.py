from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Generator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import pytest

_STARTED = pytest.StashKey[float]()
_NODE = pytest.StashKey[dict[str, str]]()


@dataclass
class _ProgressStream:
    descriptor: int
    pid: int
    remaining: int = 4 * 1024 * 1024

    def emit(self, payload: bytes) -> None:
        if os.getpid() != self.pid or len(payload) > min(self.remaining, 4096):
            return
        self.remaining -= len(payload)
        try:
            if os.write(self.descriptor, payload) != len(payload):
                self.remaining = 0
        except OSError:
            self.remaining = 0


_STREAM = pytest.StashKey[_ProgressStream]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ci-progress-fd", type=int, help="Owned descriptor for bounded native progress"
    )


def pytest_configure(config: pytest.Config) -> None:
    descriptor = config.getoption("--ci-progress-fd", default=None)
    if descriptor is not None:
        if type(descriptor) is not int or descriptor < 3:
            raise pytest.UsageError("native progress requires a non-stdio descriptor")
        os.set_inheritable(descriptor, False)
        config.stash[_STREAM] = _ProgressStream(descriptor, os.getpid())


def pytest_unconfigure(config: pytest.Config) -> None:
    stream = config.stash.get(_STREAM, None)
    if stream is not None:
        with suppress(OSError):
            os.close(stream.descriptor)


def _progress(session: pytest.Session, phase: str, elapsed: float) -> None:
    report = session.config.getoption("--ci-report", default=None)
    if report is None:
        return
    path = Path(report).with_name("progress.json")
    document = {
        "schemaVersion": "ci-coordinator-native-progress/v1",
        "evidenceClass": "untrusted-diagnostic",
        "phase": phase,
        "elapsedSeconds": elapsed,
        "pid": os.getpid(),
        "node": session.config.stash.get(_NODE, None),
    }
    text = json.dumps(document, sort_keys=True, ensure_ascii=False) + "\n"
    stream = session.config.stash.get(_STREAM, None)
    if stream is not None:
        stream.emit(text.encode())
    with suppress(OSError):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)


def _emit(session: pytest.Session, phase: str) -> None:
    elapsed = monotonic() - session.config.stash[_STARTED]
    _progress(session, phase, elapsed)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    with suppress(OSError):
        reporter.write_line(
            "\n"
            + json.dumps(
                {
                    "coveragePhase": phase,
                    "elapsedSeconds": elapsed,
                }
            )
        )
        reporter.flush()


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[_STARTED] = monotonic()
    _emit(session, "session_started")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    try:
        file = item.path.relative_to(item.config.rootpath).as_posix()
    except ValueError:
        file = "outside-root"
    item.config.stash[_NODE] = {
        "file": file[:256],
        "name": item.name.partition("[")[0][:128],
        "nodeIdSha256": hashlib.sha256(item.nodeid.encode()).hexdigest(),
    }
    _progress(item.session, "test_started", monotonic() - item.config.stash[_STARTED])


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtestloop(session: pytest.Session) -> Generator[None, object, object]:
    _emit(session, "test_loop_started")
    try:
        return (yield)
    finally:
        _emit(session, "test_loop_finished")


def pytest_sessionfinish(session: pytest.Session) -> None:
    _emit(session, "session_finished")
