from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from ci_coordinator.target_artifacts.cli import main as artifact_cli
from ci_coordinator.target_artifacts.reporter import render_measurement_reporter
from ci_coordinator.target_artifacts.resources import ci_measurement_reporter as producer

OPTIONS = [
    "--endpoint",
    "https://coordinator.example",
    "--audience",
    "ci-coordinator",
    "--installation-id",
    "101",
    "--sample-key",
    "backend-tests",
    "--inputs-digest",
    "a" * 64,
    "--runner-digest",
    "b" * 64,
    "--cache-digest",
    "c" * 64,
]


def test_export_and_drift_check_preserve_standalone_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "measure.py"
    arguments = ["--output", str(output)]
    assert artifact_cli(["check-reporter", *arguments]) == 1
    assert artifact_cli(["render-reporter", *arguments]) == 0
    assert output.read_bytes() == render_measurement_reporter()
    assert artifact_cli(["check-reporter", *arguments]) == 0
    assert "measurement_reporter_current" in capsys.readouterr().out
    output.write_bytes(output.read_bytes() + b"\n")
    assert artifact_cli(["check-reporter", *arguments]) == 1


@pytest.mark.parametrize("code", [0, 7])
def test_standalone_command_exit_survives_missing_reporting_configuration(
    tmp_path: Path, code: int
) -> None:
    output = tmp_path / "measure.py"
    output.write_bytes(render_measurement_reporter())
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(output),
            "--",
            sys.executable,
            "-I",
            "-c",
            f"raise SystemExit({code})",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == code
    assert completed.stdout == completed.stderr == b""


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ((1.0, 2.0), (1.25, 2.5), [(250_000, None), (500_000, None)]),
        ("unsupported_platform", "unsupported_platform", [(None, "unsupported_platform")] * 2),
        ((1.0, 2.0), "counter_error", [(None, "counter_error")] * 2),
        ((2.0, 3.0), (1.0, 2.0), [(None, "out_of_range")] * 2),
        ((1.0, 1.0), (0.9999999, 0.9999999), [(None, "out_of_range")] * 2),
        ((0.0, 0.0), (float("nan"), float("inf")), [(None, "counter_error")] * 2),
        ((0.0, 0.0), (1e30, 1e30), [(None, "out_of_range")] * 2),
    ],
)
def test_counter_differences_preserve_scope_and_unavailable_reason(
    before: tuple[float, float] | str,
    after: tuple[float, float] | str,
    expected: list[tuple[int | None, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter([before, after])
    monotonic = iter([5_000_000, 7_123_999])
    monkeypatch.setattr(producer, "_cpu", lambda: next(readings))
    monkeypatch.setattr(time, "monotonic_ns", lambda: next(monotonic))
    code, counters = producer._measure([sys.executable, "-I", "-c", "raise SystemExit(7)"])
    assert code == 7
    assert counters[0] == {
        "counter": "elapsed",
        "unit": "microsecond",
        "scope": "reporter_interval",
        "value": 2123,
        "unavailableReason": None,
    }
    assert [
        (counter["value"], counter["unavailableReason"]) for counter in counters[1:]
    ] == expected
    assert all(counter["scope"] == "waited_children" for counter in counters[1:])


@pytest.mark.parametrize("code", [0, 7, -signal.SIGTERM])
@pytest.mark.parametrize(
    "failure", [None, OSError("secret-error"), subprocess.TimeoutExpired("private", 15)]
)
def test_upload_cannot_change_command_exit_or_expose_secrets(
    code: int,
    failure: BaseException | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(producer, "_measure", lambda command: (code, []))
    calls: list[tuple[object, dict[str, Any]]] = []

    def upload(command: object, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        if failure is not None:
            raise failure
        return subprocess.CompletedProcess(["worker"], 1)

    monkeypatch.setattr(subprocess, "run", upload)
    assert producer.main([*OPTIONS, "--", "command", "private-argument"]) == (
        code if code >= 0 else 128 - code
    )
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        sys.executable,
        "-I",
        "-S",
        str(Path(producer.__file__).resolve()),
        "--_submit",
    ]
    assert kwargs["timeout"] == 15 and kwargs["check"] is False
    assert kwargs["stdout"] is None and kwargs["stderr"] == subprocess.DEVNULL
    payload = json.loads(kwargs["input"])
    assert payload["commandExitCode"] == code
    assert "private" not in kwargs["input"].decode()
    assert capsys.readouterr() == ("", "")


def test_timed_out_upload_worker_is_killed_and_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = tmp_path / "blocked.py"
    worker.write_text("import time\ntime.sleep(300)\n")
    monkeypatch.setattr(producer, "__file__", str(worker))
    monkeypatch.setattr(producer, "UPLOAD_SECONDS", 0.1)
    monkeypatch.setattr(producer, "_measure", lambda command: (7, []))
    popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", spawn)
    assert producer.main([*OPTIONS, "--", "unused"]) == 7
    assert len(children) == 1
    assert children[0].poll() is not None and children[0].returncode != 0


@pytest.mark.skipif(os.name != "posix", reason="Unix signal exit contract")
def test_signal_terminated_command_uses_shell_exit_mapping() -> None:
    command = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    assert producer.main(["--", sys.executable, "-I", "-c", command]) == 128 + signal.SIGTERM
