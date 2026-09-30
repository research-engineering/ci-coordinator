from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from scripts.bounded_process import _is_process_group_alive, _signal_process_group, spawn


@pytest.mark.parametrize("ignore_interrupt", [False, True])
def test_native_progress_survives_an_independently_interrupted_test(
    tmp_path: Path, ignore_interrupt: bool
) -> None:
    source = tmp_path / "backend/tests/test_blocked.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import time, signal, os, json\nfrom pathlib import Path\n"
        + "signal.signal(signal.SIGINT, "
        + ("signal.SIG_IGN" if ignore_interrupt else "signal.default_int_handler")
        + ")\n"
        + "def test_blocked(request):\n"
        + "    assert signal.getsignal(signal.SIGINT) == "
        + ("signal.SIG_IGN" if ignore_interrupt else "signal.default_int_handler")
        + "\n"
        "    assert not os.get_inheritable(request.config.getoption('--ci-progress-fd'))\n"
        "    assert signal.SIGINT not in signal.pthread_sigmask(signal.SIG_BLOCK, set())\n"
        "    Path('armed.tmp').write_text(json.dumps({'pid':os.getpid(), 'group':os.getpgrp()}))\n"
        "    Path('armed.tmp').replace('armed.json')\n"
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
    provider = "/usr/bin/gnutimeout"
    if not Path(provider).is_file():
        provider = shutil.which("timeout") or ""
    assert provider
    version = spawn(provider, ("--version",), cwd=tmp_path, max_buffer=4096, timeout_seconds=5)
    assert version.status == 0 and version.error is None and version.failure_kind is None
    header = version.stdout.splitlines()[0] if version.stdout else ""
    assert not version.stderr and header.startswith("timeout (GNU coreutils) ")
    print(json.dumps({"watchdogProvider": provider, "version": header}))
    command = [
        provider,
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
        cleanup_deadline: float | None = None
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
                    if (
                        (tmp_path / "armed.json").is_file()
                        and candidate["phase"] == "test_started"
                        and any(frame == candidate for frame in frames)
                    ):
                        observed = candidate
                        break
                time.sleep(0.05)
            assert observed is not None
            armed = json.loads((tmp_path / "armed.json").read_text())
            assert armed == {"pid": observed["pid"], "group": process.pid}
            expected_statuses = {-signal.SIGKILL, 137} if ignore_interrupt else {124}
            assert process.wait(timeout=20) in expected_statuses
            cleanup_deadline = time.monotonic() + 5
            while _is_process_group_alive(process.pid) and time.monotonic() < cleanup_deadline:
                time.sleep(0.01)
            assert not _is_process_group_alive(process.pid)
        finally:
            if cleanup_deadline is None:
                cleanup_deadline = time.monotonic() + 5
            if _is_process_group_alive(process.pid):
                _signal_process_group(process.pid, signal.SIGKILL)
            if process.poll() is None:
                process.wait(timeout=max(0.001, cleanup_deadline - time.monotonic()))
            while _is_process_group_alive(process.pid) and time.monotonic() < cleanup_deadline:
                time.sleep(0.01)
            assert not _is_process_group_alive(process.pid)
    assert observed["evidenceClass"] == "untrusted-diagnostic"
    assert observed["node"]["file"] == "backend/tests/test_blocked.py"
    assert observed["node"]["name"] == "test_blocked"
    assert len(observed["node"]["nodeIdSha256"]) == 64
    assert progress.stat().st_size < 8192
    assert streamed.stat().st_size <= 4 * 1024 * 1024
    native = tmp_path / "native.json"
    assert not native.exists() or json.loads(native.read_text())["exit_status"] != 0
