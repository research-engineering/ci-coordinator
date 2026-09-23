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
