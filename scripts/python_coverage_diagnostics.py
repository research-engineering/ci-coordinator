from __future__ import annotations

import hashlib
import json
import os
import sys
from argparse import SUPPRESS
from collections.abc import Generator
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from scripts.dev_environment.environment import ManagedProcessBorrow

_STARTED = pytest.StashKey[float]()
_NODE = pytest.StashKey[dict[str, str]]()
_MANAGED: pytest.StashKey[ManagedProcessBorrow] = pytest.StashKey()
_MANAGED_ARGUMENT = "--managed-process-context"


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
    parser.addoption(_MANAGED_ARGUMENT, help=SUPPRESS)
    parser.addoption(
        "--ci-progress-fd", type=int, help="Owned descriptor for bounded native progress"
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_load_initial_conftests(
    early_config: pytest.Config, args: list[str]
) -> Generator[None, object, object]:
    if not any(
        item == _MANAGED_ARGUMENT or item.startswith(_MANAGED_ARGUMENT + "=") for item in args
    ):
        return (yield)
    if sys.platform not in {"linux", "darwin"}:
        raise pytest.UsageError("managed process context requires Linux or macOS")
    from scripts.dev_environment.environment import borrow_managed_process, managed_process_argument

    try:
        context = managed_process_argument(args)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    if context is None:
        return (yield)
    lifetime = ExitStack()
    try:
        borrow = lifetime.enter_context(borrow_managed_process(context, interrupt=True))
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise pytest.UsageError("managed process admission failed") from error
    early_config.stash[_MANAGED] = borrow
    early_config.add_cleanup(lifetime.close)
    try:
        return (yield)
    except BaseException:
        lifetime.__exit__(*sys.exc_info())
        raise


def _admit_managed_work(config: pytest.Config) -> None:
    borrow = config.stash.get(_MANAGED, None)
    if borrow is not None and (status := borrow.completion_status(0)) != 0:
        pytest.exit("managed execution stopped", returncode=status)


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
    _admit_managed_work(session.config)
    session.config.stash[_STARTED] = monotonic()
    _emit(session, "session_started")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    _admit_managed_work(item.config)
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
    borrow = session.config.stash.get(_MANAGED, None)
    if borrow is not None:
        session.exitstatus = borrow.completion_status(session.exitstatus)
    _emit(session, "session_finished")
