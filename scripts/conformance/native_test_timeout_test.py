from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("ignore_interrupt", [False, True])
def test_native_progress_survives_an_independently_interrupted_test(
    tmp_path: Path, ignore_interrupt: bool
) -> None:
    source = tmp_path / "backend/tests/test_blocked.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import time, signal, os\n"
        + ("signal.signal(signal.SIGINT, signal.SIG_IGN)\n" if ignore_interrupt else "")
        + "def test_blocked(request):\n"
        "    assert not os.get_inheritable(request.config.getoption('--ci-progress-fd'))\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTEST_ADDOPTS": "",
    }
    progress = tmp_path / "progress.json"
    streamed = tmp_path / "stream.jsonl"
    command = [
        "timeout",
        "--signal=INT",
        "--kill-after=2s",
        "15s",
        sys.executable,
        "-m",
        "pytest",
        "--rootdir",
        str(tmp_path),
        "-p",
        "scripts.ci_test_report",
        "-p",
        "scripts.python_coverage_diagnostics",
        f"--ci-report={tmp_path / 'native.json'}",
        str(source),
    ]
    with (
        streamed.open("wb") as output,
        subprocess.Popen(  # noqa: S603 -- fixed watchdog argv and an owned temporary probe
            [*command, f"--ci-progress-fd={output.fileno()}"],
            cwd=tmp_path,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(output.fileno(),),
        ) as process,
    ):
        try:
            deadline = time.monotonic() + 20
            observed = None
            while process.poll() is None and time.monotonic() < deadline:
                if progress.is_file():
                    candidate = json.loads(progress.read_text())
                    frames = [
                        json.loads(line)
                        for line in streamed.read_bytes().splitlines(keepends=True)
                        if line.endswith(b"\n")
                    ]
                    if candidate["phase"] == "test_started" and any(
                        frame == candidate for frame in frames
                    ):
                        observed = candidate
                        break
                time.sleep(0.05)
            assert observed is not None
            expected_statuses = {-signal.SIGKILL, 137} if ignore_interrupt else {124}
            assert process.wait(timeout=20) in expected_statuses
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    assert observed["evidenceClass"] == "untrusted-diagnostic"
    assert observed["node"]["file"] == "backend/tests/test_blocked.py"
    assert observed["node"]["name"] == "test_blocked"
    assert len(observed["node"]["nodeIdSha256"]) == 64
    assert progress.stat().st_size < 8192
    assert streamed.stat().st_size <= 4 * 1024 * 1024
    native = tmp_path / "native.json"
    assert not native.exists() or json.loads(native.read_text())["exit_status"] != 0
