from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts import python_coverage_diagnostics as diagnostics
from scripts.bounded_process import current_process_scope, spawn
from scripts.dev_environment.environment import MANAGED_PROCESS_ARGUMENT
from scripts.tests.test_dev_environment_dependencies import (
    dependency_identity_fixture,
    managed_entry_context,
)


def test_progress_stream_bounds_records_and_total_bytes_and_drops_forked_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[bytes] = []

    def write(_descriptor: int, payload: bytes) -> int:
        observed.append(payload)
        return len(payload)

    monkeypatch.setattr(os, "write", write)
    stream = diagnostics._ProgressStream(9, os.getpid(), remaining=5000)
    stream.emit(b"x" * 4097)
    stream.emit(b"a" * 4096)
    stream.emit(b"b" * 905)
    stream.emit(b"c" * 904)
    stream.emit(b"d")
    assert observed == [b"a" * 4096, b"c" * 904]
    stream = diagnostics._ProgressStream(9, os.getpid() + 1)
    stream.emit(b"foreign-pid")
    assert len(observed) == 2


@pytest.mark.parametrize("failure", ["partial", "unavailable"])
def test_progress_stream_stops_after_a_partial_or_failed_write(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls = []

    def write(_descriptor: int, _payload: bytes) -> int:
        calls.append(1)
        if failure == "unavailable":
            raise OSError("closed progress output")
        return 0

    monkeypatch.setattr(os, "write", write)
    stream = diagnostics._ProgressStream(9, os.getpid())
    stream.emit(b"first")
    stream.emit(b"second")
    assert calls == [1]


@pytest.mark.parametrize("raises", (False, True))
@pytest.mark.parametrize("output", ("available", "absent", "write_failed", "flush_failed"))
def test_phase_wrapper_preserves_result_or_exception(
    monkeypatch: pytest.MonkeyPatch,
    raises: bool,
    output: str,
) -> None:
    messages: list[str] = []
    pending: list[str] = []

    def write_line(message: str) -> None:
        if output == "write_failed":
            raise OSError("diagnostic output unavailable")
        pending.append(message)

    def flush() -> None:
        if output == "flush_failed":
            raise OSError("diagnostic flush failed")
        messages.extend(pending)
        pending.clear()

    manager = pytest.PytestPluginManager()
    if output != "absent":
        manager.register(SimpleNamespace(write_line=write_line, flush=flush), "terminalreporter")
    session = cast(
        pytest.Session,
        SimpleNamespace(
            config=SimpleNamespace(
                pluginmanager=manager,
                stash=pytest.Stash(),
                getoption=lambda *_args, **_kwargs: None,
            )
        ),
    )
    times = iter((10.0, 11.0, 12.0, 13.0, 14.0))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagnostics, "monotonic", lambda: next(times))
    diagnostics.pytest_sessionstart(session)
    wrapper = diagnostics.pytest_runtestloop(session)
    assert next(wrapper) is None
    if raises:
        failure = RuntimeError("original failure")
        with pytest.raises(RuntimeError) as caught:
            wrapper.throw(failure)
        assert caught.value is failure
    else:
        marker = object()
        with pytest.raises(StopIteration) as stopped:
            wrapper.send(marker)
        assert stopped.value.value is marker
    diagnostics.pytest_sessionfinish(session)
    assert all(message.startswith("\n") for message in messages)
    assert [json.loads(message) for message in messages] == (
        [
            {"coveragePhase": phase, "elapsedSeconds": elapsed}
            for phase, elapsed in (
                ("session_started", 1.0),
                ("test_loop_started", 2.0),
                ("test_loop_finished", 3.0),
                ("session_finished", 4.0),
            )
        ]
        if output == "available"
        else []
    )


@pytest.mark.parametrize(
    ("source", "status"),
    (
        ("def test_case(): assert True\n", 0),
        ("def test_case(): assert False\n", 1),
        ("def test_case(:\n", 2),
    ),
)
def test_native_entrypoint_retains_outcomes_with_composed_plugin(
    tmp_path: Path,
    source: str,
    status: int,
) -> None:
    path = tmp_path / "test_case.py"
    path.write_text(source, encoding="utf-8")
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTEST_ADDOPTS": "",
    }
    scope = current_process_scope()
    outcomes = [
        subprocess.run(
            [sys.executable, "-m", "pytest", *plugins, "--color=no", "-q", str(path)],
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            pass_fds=() if scope is None else scope.inherited_fds,
        )
        for plugins in ([], ["-p", "scripts.python_coverage_diagnostics"])
    ]
    assert [outcome.returncode for outcome in outcomes] == [status, status]
    assert "coveragePhase" not in outcomes[0].stdout
    phases = [
        json.loads(line)
        for line in outcomes[1].stdout.splitlines()
        if line.startswith('{"coveragePhase":')
    ]
    assert [row["coveragePhase"] for row in phases] == [
        "session_started",
        "test_loop_started",
        "test_loop_finished",
        "session_finished",
    ], outcomes[1].stdout
    assert all(set(row) == {"coveragePhase", "elapsedSeconds"} for row in phases)
    elapsed = [row["elapsedSeconds"] for row in phases]
    assert elapsed == sorted(elapsed) and elapsed[0] >= 0


_PYTEST_MANAGED_RECEIPT = """
import json, os, signal, sys
from pathlib import Path
import pytest
from scripts.bounded_process import current_process_scope
receipt, inherited, *arguments = sys.argv[1:]
before = current_process_scope()
handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
status = pytest.main(arguments)
try:
    os.fstat(int(inherited))
    closed = False
except OSError:
    closed = True
Path(receipt).write_text(json.dumps({
    'status': int(status), 'scopeRestored': current_process_scope() is before,
    'handlersRestored': all(signal.getsignal(number) is handler
        for number, handler in handlers.items()),
    'leaseClosed': closed,
}))
"""


@pytest.mark.parametrize("cut", ["positive", "invalid", "early-failure"])
def test_native_managed_admission_precedes_cov_erase_and_restores_early_failure(
    tmp_path: Path, cut: str
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    data = tmp_path / ".coverage"
    data.write_bytes(b"pre-admission-coverage-sentinel")
    collected = tmp_path / "collected"
    receipt = tmp_path / "receipt.json"
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "fixture_module.py").write_text("VALUE = 3\n")
    test = tmp_path / "test_case.py"
    test.write_text(
        "from pathlib import Path\n"
        "from scripts.dev_environment.environment import current_managed_process\n"
        f"Path({str(collected)!r}).write_text('collected')\n"
        "import fixture_module\n"
        "def test_case():\n"
        "    assert current_managed_process() is not None\n"
        "    assert fixture_module.VALUE == 3\n"
    )
    (tmp_path / "early_failure.py").write_text(
        "import pytest\n"
        "@pytest.hookimpl(trylast=True)\n"
        "def pytest_load_initial_conftests():\n"
        "    raise pytest.UsageError('owned early failure')\n"
    )
    with managed_entry_context(identity) as (context, _stop, inherited):
        stop_read = json.loads(context)["stopFd"]
        result = spawn(
            sys.executable,
            (
                "-c",
                _PYTEST_MANAGED_RECEIPT,
                str(receipt),
                str(inherited),
                "-p",
                "scripts.python_coverage_diagnostics",
                "-p",
                "pytest_cov.plugin",
                *(["-p", "early_failure"] if cut == "early-failure" else []),
                "-c",
                str(tmp_path / "pytest.ini"),
                "--cov=fixture_module",
                "--cov-report=",
                "--color=no",
                "-q",
                str(test),
                MANAGED_PROCESS_ARGUMENT,
                "{}" if cut == "invalid" else context,
            ),
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    (str(Path(__file__).resolve().parents[2]), str(tmp_path))
                ),
                "PYTEST_ADDOPTS": "",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "COVERAGE_FILE": str(data),
            },
            max_buffer=1024 * 1024,
            timeout_seconds=20,
            graceful_seconds=3,
            inherited_fds=(inherited, stop_read),
        )
        assert result.status == 0 and result.error is None, result.stderr
        assert json.loads(receipt.read_text()) == {
            "status": 0 if cut == "positive" else 4,
            "scopeRestored": True,
            "handlersRestored": True,
            "leaseClosed": cut != "invalid",
        }
        if cut == "positive":
            assert collected.read_text() == "collected"
            assert data.read_bytes().startswith(b"SQLite format 3")
        elif cut == "invalid":
            assert not collected.exists()
            assert data.read_bytes() == b"pre-admission-coverage-sentinel"
            assert "managed process admission failed" in result.stderr
        else:
            assert not collected.exists()
            assert "owned early failure" in result.stderr


def test_native_unmanaged_diagnostics_does_not_import_managed_environment(tmp_path: Path) -> None:
    test = tmp_path / "test_case.py"
    test.write_text("def test_case(): assert True\n")
    program = (
        "import sys, pytest\n"
        "sys.modules['scripts.dev_environment.environment'] = None\n"
        "raise SystemExit(pytest.main(sys.argv[1:]))\n"
    )
    result = spawn(
        sys.executable,
        ("-c", program, "-p", "scripts.python_coverage_diagnostics", "-q", str(test)),
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        max_buffer=1024 * 1024,
        timeout_seconds=20,
    )
    assert result.status == 0 and result.error is None, result.stderr
    assert '"coveragePhase": "session_finished"' in result.stdout
