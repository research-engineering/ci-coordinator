from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import cast

import pytest
from process_timeout_support import descendant_timeout_probe
from scripts import bounded_process
from scripts.bounded_process import (
    DecodeErrors,
    ResidualProcessGroupPolicy,
    StopPredicate,
    spawn,
)


@pytest.mark.parametrize("parent_only", [False, True], ids=["group-kill", "parent-only-mutant"])
def test_spawn_terminates_the_owned_process_group_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parent_only: bool
) -> None:
    with descendant_timeout_probe(monkeypatch, bounded_process, parent_only=parent_only) as (
        parent,
        survived,
    ):
        result = spawn(
            sys.executable,
            ("-c", parent),
            cwd=tmp_path,
            max_buffer=1024,
            timeout_seconds=0.1,
        )
        assert result.status is None
        assert result.error is not None and "timed out" in result.error
        assert result.failure_kind == "timeout"
        assert result.timed_out
        assert survived() is parent_only


def test_spawn_bounds_combined_stdout_and_stderr(tmp_path: Path) -> None:
    result = spawn(
        sys.executable,
        (
            "-c",
            "import os, time; os.write(1, b'x' * 700); os.write(2, b'y' * 700); time.sleep(5)",
        ),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result.status is None
    assert result.error is not None and "output exceeded 1024 bytes" in result.error
    assert result.failure_kind == "output-limit"
    assert len(result.stdout.encode()) + len(result.stderr.encode()) == 1024


def test_spawn_projects_exact_environment_and_stdin(tmp_path: Path) -> None:
    result = spawn(
        sys.executable,
        (
            "-c",
            (
                "import os, sys; "
                "sys.stdout.write(os.getenv('HIDDEN', 'absent') + ':' + "
                "os.environ['VISIBLE'] + ':' + sys.stdin.read())"
            ),
        ),
        cwd=tmp_path,
        env={"VISIBLE": "present"},
        input_text="payload",
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result == result.__class__(
        status=0,
        stdout="absent:present:payload",
        stderr="",
    )


def test_spawn_preserves_nonzero_status(tmp_path: Path) -> None:
    result = spawn(
        sys.executable,
        ("-c", "raise SystemExit(7)"),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result.status == 7
    assert result.error is None


def test_spawn_rejects_residual_descendants_after_direct_child_exit(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "residual-survived"
    descendant = (
        "import time; from pathlib import Path; "
        f"time.sleep(0.5); Path({str(marker)!r}).write_text('survived')"
    )
    parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {descendant!r}])"

    result = spawn(
        sys.executable,
        ("-c", parent),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
    )
    time.sleep(0.6)

    assert result.status is None
    assert result.error is not None and "residual descendants" in result.error
    assert result.failure_kind == "residual-descendant"
    assert not marker.exists()


def test_spawn_preserves_the_default_reject_termination_grace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = tmp_path / "residual-ready"
    descendant = (
        "import signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(ready)!r}).write_text('ready'); time.sleep(5)"
    )
    parent = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL); "
        f"ready = Path({str(ready)!r}); "
        "\nwhile not ready.exists(): time.sleep(0.01)"
    )
    real_signal = bounded_process._signal_process_group
    observed_signals: list[tuple[signal.Signals, float]] = []

    def recording_signal(
        process_group_id: int,
        process_signal: signal.Signals,
    ) -> bool:
        observed_signals.append((process_signal, time.monotonic()))
        return real_signal(process_group_id, process_signal)

    monkeypatch.setattr(bounded_process, "_signal_process_group", recording_signal)

    result = spawn(
        sys.executable,
        ("-c", parent),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=3,
    )

    term_at = next(at for observed, at in observed_signals if observed == signal.SIGTERM)
    kill_at = next(at for observed, at in observed_signals if observed == signal.SIGKILL)
    assert result.status is None
    assert result.error is not None and "residual descendants" in result.error
    assert 0.95 <= kill_at - term_at <= 1.5


def test_termination_schedule_has_two_independent_one_second_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_signals: list[signal.Signals] = []

    def recording_signal(
        _process_group_id: int,
        process_signal: signal.Signals,
    ) -> bool:
        observed_signals.append(process_signal)
        return True

    monkeypatch.setattr(bounded_process, "_signal_process_group", recording_signal)

    force_kill_at, hard_stop_at = bounded_process._begin_termination(123, 10.0)

    assert observed_signals == [signal.SIGTERM]
    assert force_kill_at == 11.0
    assert hard_stop_at == 12.0


@pytest.mark.parametrize("exit_status", [0, 7])
@pytest.mark.parametrize("transient_liveness_miss", [False, True])
def test_spawn_terminates_an_explicitly_disposable_residual_process_group(
    tmp_path: Path,
    exit_status: int,
    transient_liveness_miss: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "disposable-residual-survived"
    descendant = (
        "import time; from pathlib import Path; "
        f"time.sleep(10); Path({str(marker)!r}).write_text('survived')"
    )
    parent = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); "
        f"raise SystemExit({exit_status})"
    )
    if transient_liveness_miss:
        real_liveness_probe = bounded_process._is_process_group_alive
        probe_count = 0

        def liveness_probe_with_one_miss(process_group_id: int) -> bool:
            nonlocal probe_count
            probe_count += 1
            return False if probe_count == 1 else real_liveness_probe(process_group_id)

        monkeypatch.setattr(
            bounded_process,
            "_is_process_group_alive",
            liveness_probe_with_one_miss,
        )
    result = spawn(
        sys.executable,
        ("-c", parent),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
        residual_process_group_policy="terminate",
    )

    assert result.status == exit_status
    assert result.error is None
    assert result.failure_kind is None
    if transient_liveness_miss:
        assert probe_count >= 2
    assert not marker.exists()


def test_spawn_admits_descendants_that_quiesce_immediately_after_parent_exit(
    tmp_path: Path,
) -> None:
    descendant = "import time; time.sleep(0.05)"
    parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {descendant!r}])"

    result = spawn(
        sys.executable,
        ("-c", parent),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result.status == 0
    assert result.error is None


def test_spawn_applies_timeout_to_post_exit_process_group_drain(tmp_path: Path) -> None:
    descendant = "import time; time.sleep(5)"
    parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {descendant!r}])"

    started = time.monotonic()
    result = spawn(
        sys.executable,
        ("-c", parent),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=0.05,
    )

    assert result.status is None
    assert result.error is not None and "timed out" in result.error
    assert result.failure_kind == "timeout"
    assert time.monotonic() - started < 1.0


def test_spawn_bounds_post_deadline_termination_grace_without_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout_seconds = 0.4
    descendant = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(5)"
    )
    parent = (
        "import subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {descendant!r}], "
        "stdout=subprocess.PIPE, text=True); "
        "assert child.stdout is not None; child.stdout.readline()"
    )
    real_begin_termination = bounded_process._begin_termination
    termination_starts: list[float] = []

    def recording_begin_termination(
        process_group_id: int,
        now: float,
    ) -> tuple[float, float]:
        termination_starts.append(now)
        return real_begin_termination(process_group_id, now)

    monkeypatch.setattr(
        bounded_process,
        "_begin_termination",
        recording_begin_termination,
    )

    started = time.monotonic()
    result = spawn(
        sys.executable,
        ("-c", parent),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=timeout_seconds,
        residual_process_group_policy="terminate",
    )
    elapsed = time.monotonic() - started

    assert result.status is None
    assert result.error is not None and "timed out" in result.error
    assert len(termination_starts) == 1
    assert timeout_seconds <= elapsed < timeout_seconds + 2.5


def test_spawn_rechecks_timeout_after_lifecycle_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_monotonic = time.monotonic
    clock_offset = 0.0
    probe_calls = 0

    def shifted_monotonic() -> float:
        return real_monotonic() + clock_offset

    def deadline_crossing_quiescence(_process_group_id: int) -> bool:
        nonlocal clock_offset, probe_calls
        probe_calls += 1
        clock_offset = 10.0
        return False

    monkeypatch.setattr(time, "monotonic", shifted_monotonic)
    monkeypatch.setattr(
        bounded_process,
        "_is_process_group_alive",
        deadline_crossing_quiescence,
    )

    result = spawn(
        sys.executable,
        ("-c", "pass"),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=5,
    )

    assert probe_calls >= 1
    assert result.status is None
    assert result.error is not None and "timed out" in result.error


@pytest.mark.parametrize(
    ("max_buffer", "timeout_seconds", "message"),
    [
        (0, 1.0, "max_buffer"),
        (1, 0.0, "timeout_seconds"),
        (1, float("inf"), "timeout_seconds"),
    ],
)
def test_spawn_rejects_unbounded_requests(
    tmp_path: Path,
    max_buffer: int,
    timeout_seconds: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        spawn(
            sys.executable,
            ("-c", "pass"),
            cwd=tmp_path,
            max_buffer=max_buffer,
            timeout_seconds=timeout_seconds,
        )


def test_spawn_reports_start_failure_without_leaking_arguments(tmp_path: Path) -> None:
    missing = tmp_path / "missing-executable"
    sensitive_argument = "opaque-probe-value"

    result = spawn(
        str(missing),
        (sensitive_argument,),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result.status is None
    assert result.error is not None and "ENOENT" in result.error
    assert result.failure_kind == "spawn"
    assert sensitive_argument not in result.error


def test_spawn_does_not_inherit_environment_when_exact_env_is_supplied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIDDEN", "secret")

    result = spawn(
        sys.executable,
        ("-c", "import os; print(os.getenv('HIDDEN', 'absent'))"),
        cwd=tmp_path,
        env={},
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result.stdout == "absent\n"
    assert result.status == 0


def test_spawn_uses_devnull_when_stdin_is_not_declared(tmp_path: Path) -> None:
    result = spawn(
        sys.executable,
        ("-c", "import sys; print(sys.stdin.read())"),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result.stdout == "\n"
    assert result.status == 0


def test_spawn_does_not_raise_subprocess_timeout_expired(tmp_path: Path) -> None:
    try:
        result = spawn(
            sys.executable,
            ("-c", "import time; time.sleep(2)"),
            cwd=tmp_path,
            max_buffer=1024,
            timeout_seconds=0.05,
        )
    except subprocess.TimeoutExpired as error:  # pragma: no cover - explicit falsifier
        pytest.fail(f"lifecycle error escaped: {error}")

    assert result.error is not None


@pytest.mark.parametrize(
    ("decode_errors", "expected"),
    (
        ("replace", "\ufffd\ufffd"),
        ("surrogateescape", "\ufffd\udcff"),
    ),
)
def test_spawn_applies_the_admitted_output_decoder(
    tmp_path: Path,
    decode_errors: DecodeErrors,
    expected: str,
) -> None:
    result = spawn(
        sys.executable,
        ("-c", "import os; os.write(1, bytes.fromhex('efbfbdff'))"),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
        decode_errors=decode_errors,
    )

    assert result.status == 0
    assert result.stdout == expected


def test_spawn_strict_decoder_rejects_invalid_utf8(tmp_path: Path) -> None:
    with pytest.raises(UnicodeDecodeError):
        spawn(
            sys.executable,
            ("-c", "import os; os.write(1, bytes.fromhex('efbfbdff'))"),
            cwd=tmp_path,
            max_buffer=1024,
            timeout_seconds=2,
            decode_errors="strict",
        )


def test_spawn_rejects_an_unadmitted_output_decoder(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="decode_errors is not admitted"):
        spawn(
            sys.executable,
            ("-c", "pass"),
            cwd=tmp_path,
            max_buffer=1024,
            timeout_seconds=2,
            decode_errors=cast(DecodeErrors, "ignore"),
        )


def test_spawn_rejects_an_unadmitted_residual_process_group_policy(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="residual_process_group_policy is not admitted"):
        spawn(
            sys.executable,
            ("-c", "pass"),
            cwd=tmp_path,
            max_buffer=1024,
            timeout_seconds=2,
            residual_process_group_policy=cast(ResidualProcessGroupPolicy, "preserve"),
        )


def test_spawn_suppresses_process_creation_when_stop_is_already_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_spawn(*_args: object, **_kwargs: object) -> None:
        pytest.fail("process creation must be suppressed after a stop request")

    monkeypatch.setattr(subprocess, "Popen", forbidden_spawn)

    result = spawn(
        sys.executable,
        ("-c", "pass"),
        cwd=tmp_path,
        max_buffer=1024,
        stop_requested=lambda: True,
        timeout_seconds=2,
    )

    assert result.status is None
    assert result.failure_kind == "cancelled"
    assert result.timed_out is False


def test_spawn_terminates_the_owned_group_when_stop_is_observed_in_flight(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "descendant-started"
    marker = tmp_path / "cancelled-descendant-survived"
    descendant = (
        "import time; from pathlib import Path; "
        f"time.sleep(0.5); Path({str(marker)!r}).write_text('survived')"
    )
    parent = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); "
        f"Path({str(ready)!r}).write_text('ready'); "
        "time.sleep(5)"
    )

    def stop_after_spawn() -> bool:
        return ready.exists()

    result = spawn(
        sys.executable,
        ("-c", parent),
        cwd=tmp_path,
        max_buffer=1024,
        stop_requested=stop_after_spawn,
        timeout_seconds=2,
    )
    time.sleep(0.6)

    assert result.status is None
    assert result.failure_kind == "cancelled"
    assert result.timed_out is False
    assert ready.exists()
    assert not marker.exists()


def test_spawn_normalizes_direct_signal_termination(tmp_path: Path) -> None:
    result = spawn(
        sys.executable,
        ("-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result.status is None
    assert result.failure_kind == "signal"
    assert result.signal == "SIGTERM"


@pytest.mark.parametrize("status", [0, 7])
def test_spawn_does_not_unconditionally_signal_after_direct_child_reap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    observed_signals: list[signal.Signals] = []

    def record_signal(
        _process_group_id: int,
        process_signal: signal.Signals,
    ) -> bool:
        observed_signals.append(process_signal)
        return False

    monkeypatch.setattr(bounded_process, "_signal_process_group", record_signal)

    result = spawn(
        sys.executable,
        ("-c", f"raise SystemExit({status})"),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=2,
    )

    assert result.status == status
    assert observed_signals == []


def test_spawn_preserves_the_first_terminal_failure_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_termination(_process_group_id: int, _now: float) -> tuple[float, float]:
        raise OSError(errno.EIO, "injected termination failure")

    monkeypatch.setattr(bounded_process, "_begin_termination", fail_termination)

    result = spawn(
        sys.executable,
        ("-c", "import time; time.sleep(5)"),
        cwd=tmp_path,
        max_buffer=1024,
        timeout_seconds=0.05,
    )

    assert result.status is None
    assert result.failure_kind == "timeout"
    assert result.timed_out
    assert result.error is not None
    assert "timed out" in result.error
    assert "secondary lifecycle error" in result.error


def test_spawn_rejects_a_noncallable_stop_predicate(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="stop_requested must be callable"):
        spawn(
            sys.executable,
            ("-c", "pass"),
            cwd=tmp_path,
            max_buffer=1024,
            stop_requested=cast(StopPredicate, object()),
            timeout_seconds=2,
        )


@pytest.mark.parametrize(
    ("target_state", "expected"),
    [("S", True), ("T", True), ("Z", False), ("X", False), ("x", False)],
)
def test_linux_process_group_admits_only_executable_members(
    tmp_path: Path,
    target_state: str,
    expected: bool,
) -> None:
    _write_proc_stat(tmp_path, pid=101, state=target_state, process_group_id=77)
    _write_proc_stat(tmp_path, pid=102, state="S", process_group_id=88)

    assert (
        bounded_process._linux_process_group_has_executable_member(
            77,
            proc_root=tmp_path,
        )
        is expected
    )


@pytest.mark.parametrize("thread_count", [0, 2])
def test_linux_process_group_fails_closed_for_nonunit_dead_thread_groups(
    tmp_path: Path,
    thread_count: int,
) -> None:
    _write_proc_stat(
        tmp_path,
        pid=101,
        state="Z",
        process_group_id=77,
        thread_count=thread_count,
    )

    assert bounded_process._linux_process_group_has_executable_member(
        77,
        proc_root=tmp_path,
    )


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux procfs semantics")
def test_linux_process_group_retains_zombie_leader_with_live_threads() -> None:
    child = (
        "import ctypes, threading, time\n"
        "threading.Thread(target=time.sleep, args=(30,)).start()\n"
        "pthread_exit = ctypes.CDLL(None).pthread_exit\n"
        "pthread_exit.argtypes = [ctypes.c_void_p]\n"
        "pthread_exit.restype = None\n"
        "pthread_exit(None)\n"
    )
    process = subprocess.Popen(
        (sys.executable, "-c", child),
        start_new_session=True,
    )

    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            raw_stat = Path(f"/proc/{process.pid}/stat").read_bytes()
            closing_name = raw_stat.rfind(b") ")
            if closing_name >= 0 and raw_stat[closing_name + 2 :].startswith(b"Z "):
                break
            time.sleep(0.01)
        else:
            pytest.fail("thread-group leader did not enter zombie state")

        assert process.poll() is None
        assert bounded_process._linux_process_group_has_executable_member(process.pid)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)


def test_linux_process_group_parser_handles_parentheses_in_process_name(
    tmp_path: Path,
) -> None:
    _write_proc_stat(
        tmp_path,
        pid=101,
        state="Z",
        process_group_id=77,
        process_name="helper ) worker",
    )

    assert not bounded_process._linux_process_group_has_executable_member(
        77,
        proc_root=tmp_path,
    )


def test_linux_process_group_parser_fails_closed_on_malformed_stat(
    tmp_path: Path,
) -> None:
    process = tmp_path / "101"
    process.mkdir()
    (process / "stat").write_bytes(b"malformed")

    assert bounded_process._linux_process_group_has_executable_member(
        77,
        proc_root=tmp_path,
    )


def test_linux_process_group_evidence_is_bounded_and_redacts_untrusted_names(
    tmp_path: Path,
) -> None:
    for offset in range(bounded_process._MAX_PROCESS_GROUP_DIAGNOSTICS + 2):
        _write_proc_stat(
            tmp_path,
            pid=101 + offset,
            state="S",
            process_group_id=77,
            process_name="credential=value" if offset == 0 else f"worker-{offset}",
        )

    evidence = bounded_process._linux_process_group_evidence(77, proc_root=tmp_path)

    assert evidence.has_executable_member
    assert len(evidence.diagnostics) == bounded_process._MAX_PROCESS_GROUP_DIAGNOSTICS
    assert evidence.diagnostics[0] == "name=unidentified,state=S,threads=1"
    assert all("credential" not in diagnostic for diagnostic in evidence.diagnostics)


def _write_proc_stat(
    proc_root: Path,
    *,
    pid: int,
    state: str,
    process_group_id: int,
    process_name: str = "worker",
    thread_count: int = 1,
) -> None:
    process = proc_root / str(pid)
    process.mkdir()
    fields = [state, "1", str(process_group_id), *(["0"] * 14), str(thread_count)]
    (process / "stat").write_text(
        f"{pid} ({process_name}) {' '.join(fields)}\n",
    )
