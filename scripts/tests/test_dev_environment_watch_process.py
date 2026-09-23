from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import bounded_process
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    OperationBusy,
    instance_operation_lock,
    operation_paths,
)
from scripts.dev_environment.private_files import PrivateLockBusy, bounded_private_lock
from scripts.dev_environment.watch_session import cancel_watch, owned_watch_session

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_PYTHON = getattr(sys, "_base_executable", sys.executable)
_PROVIDER = """
import os, select, signal, sys
mode = sys.argv[1]
def stop(_signal, _frame):
    print('PROVIDER_TERM', flush=True)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN if mode == 'ignore-term' else stop)
if mode == 'close-descriptors':
    os.closerange(3, 1024)
print('PROVIDER_READY', flush=True)
ready, _, _ = select.select([0], [], [], 10)
if ready:
    os.read(0, 32)
print('PROVIDER_DONE', flush=True)
"""


class _Witness:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self._buffer = bytearray()
        self._stderr = bytearray()

    def line(self) -> str:
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        deadline = time.monotonic() + 5
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            selector.register(self.process.stderr, selectors.EVENT_READ)
            while b"\n" not in self._buffer:
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"witness barrier timed out; stderr={self._stderr!r}"
                events = selector.select(remaining)
                assert events, f"witness barrier was not observed; stderr={self._stderr!r}"
                for key, _ in events:
                    chunk = os.read(key.fd, 4096)
                    if key.fileobj is self.process.stderr:
                        self._stderr.extend(chunk)
                        assert len(self._stderr) <= 8192, "fixture stderr exceeded its bound"
                        if not chunk:
                            selector.unregister(key.fileobj)
                    else:
                        assert chunk, (
                            f"witness exited before the barrier; returncode={self.process.poll()}; "
                            f"stderr={self._stderr!r}"
                        )
                        self._buffer.extend(chunk)
                        assert len(self._buffer) <= 8192
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = bytearray(rest)
        return line.decode()

    def release_provider(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(b"exit\n")
        self.process.stdin.flush()


@contextmanager
def _witness(
    identity: InstanceIdentity,
    mode: str = "graceful",
    *,
    program: str = _PROVIDER,
    joined_client_fixture: bool = False,
    parent_phase: str | None = None,
) -> Iterator[_Witness]:
    phase_read, phase_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                _PYTHON,
                "-S",
                "-m",
                "scripts.tests.watch_session_witness",
                "--repo-root",
                str(identity.repo_root),
                "--state-home",
                str(identity.state_home),
                *(["--joined-client-fixture"] if joined_client_fixture else []),
                *(
                    ["--parent-phase", parent_phase, "--phase-release-fd", str(phase_read)]
                    if parent_phase is not None
                    else []
                ),
                _PYTHON,
                "-S",
                "-c",
                program,
                mode,
            ],
            cwd=_SOURCE_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(phase_read,),
        )
        yield _Witness(process)
    finally:
        os.close(phase_read)
        os.close(phase_write)
        if process is not None:
            if process.stdin is not None:
                with suppress(BrokenPipeError):
                    process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


@pytest.fixture
def identity(tmp_path: Path) -> InstanceIdentity:
    root = tmp_path / "worktree"
    root.mkdir()
    return derive_instance_identity(root, state_home=tmp_path / "state")


@pytest.mark.parametrize("mode", ["graceful", "ignore-term"])
def test_only_the_joined_clean_client_releases_ownership(
    identity: InstanceIdentity,
    mode: str,
) -> None:
    with _witness(identity, mode, joined_client_fixture=True) as witness:
        nonce = json.loads(witness.line())["publishedNonce"]
        assert witness.line() == "PROVIDER_READY"
        result = cancel_watch(identity, expected_nonce=nonce, timeout_seconds=3)
        assert (result.state, result.reason) == (
            ("quiescent", "watch_client_stopped")
            if mode == "graceful"
            else ("blocked", "watch_client_forced")
        )
        if mode == "graceful":
            assert witness.line() == "PROVIDER_TERM"
        receipt = json.loads(witness.line())
        assert receipt["process"]["process_group_quiescent"] is True
        assert receipt["process"]["failure_kind"] == "cancelled"
        assert receipt["process"]["returncode"] == (0 if mode == "graceful" else -signal.SIGKILL)
        assert receipt["process"]["escalated"] == (mode == "ignore-term")
        assert receipt["process"]["cancellation_signal_sent"] is True
    if mode == "graceful":
        assert cancel_watch(identity, expected_nonce=nonce).state == "quiescent"
        with instance_operation_lock(identity):
            pass
    else:
        with pytest.raises(OperationBlocked), instance_operation_lock(identity):
            pytest.fail("a forced client stop admitted the next mutation")


def test_keyboard_interrupt_retains_the_actual_clean_client_stop(
    identity: InstanceIdentity,
) -> None:
    with _witness(identity, joined_client_fixture=True, parent_phase="supervised") as witness:
        nonce = json.loads(witness.line())["publishedNonce"]
        assert {witness.line(), witness.line()} == {"PROVIDER_READY", "PARENT_SUPERVISING"}
        witness.process.send_signal(signal.SIGINT)
        assert witness.line() == "PROVIDER_TERM"
        assert witness.process.wait(timeout=5) != 0
    result = cancel_watch(identity, expected_nonce=nonce)
    assert (result.state, result.reason) == ("quiescent", "watch_client_stopped")
    completion = json.loads(operation_paths(identity).completed.read_text())
    assert completion["nonce"] == nonce
    assert completion["process"] == {
        "returncode": 0,
        "process_group_quiescent": True,
        "failure_kind": "cancelled",
        "escalated": False,
        "started": True,
        "cancellation_signal_sent": True,
    }
    with instance_operation_lock(identity):
        pass


def test_interrupt_during_spawn_transfer_preserves_the_ambiguous_mutation_fence(
    identity: InstanceIdentity,
) -> None:
    paths = operation_paths(identity)
    with _witness(identity, joined_client_fixture=True, parent_phase="transfer") as witness:
        nonce = json.loads(witness.line())["publishedNonce"]
        assert {witness.line(), witness.line()} == {"PROVIDER_READY", "PARENT_TRANSFERRING"}
        witness.process.send_signal(signal.SIGINT)
        assert witness.process.wait(timeout=5) != 0
        with pytest.raises(PrivateLockBusy), bounded_private_lock(paths.mutation):
            pytest.fail("ownership transfer lost the surviving child's mutation descriptor")
        assert not paths.completed.exists()
        record = json.loads(paths.session.read_text())
        assert record["nonce"] == nonce and record["reason"] == "watch_abandoned"
        with pytest.raises(OperationBusy), instance_operation_lock(identity):
            pytest.fail("interrupted ownership transfer admitted a concurrent mutation")
        witness.release_provider()
        assert witness.line() == "PROVIDER_DONE"
        with bounded_private_lock(paths.mutation, timeout_seconds=3):
            pass
    result = cancel_watch(identity, expected_nonce=nonce)
    assert (result.state, result.reason) == ("blocked", "watch_abandoned")
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("child exit erased the unresolved ownership transfer fence")


@pytest.mark.parametrize("owner_signal", [None, signal.SIGINT, signal.SIGTERM])
def test_only_owned_graceful_cancellation_admits_the_physical_exit_130(
    identity: InstanceIdentity, owner_signal: signal.Signals | None
) -> None:
    program = _PROVIDER.replace("raise SystemExit(0)", "raise SystemExit(130)")
    with _witness(
        identity,
        program=program,
        joined_client_fixture=True,
        parent_phase="supervised" if owner_signal is not None else None,
    ) as witness:
        nonce = json.loads(witness.line())["publishedNonce"]
        if owner_signal is None:
            assert witness.line() == "PROVIDER_READY"
        else:
            assert {witness.line(), witness.line()} == {"PROVIDER_READY", "PARENT_SUPERVISING"}
        if owner_signal is None:
            cancellation = cancel_watch(identity, expected_nonce=nonce, timeout_seconds=3)
            assert cancellation.reason == "watch_client_stopped"
            assert witness.line() == "PROVIDER_TERM"
            process = json.loads(witness.line())["process"]
            assert process["returncode"] == 130
            assert process["cancellation_signal_sent"] is True
        else:
            witness.process.send_signal(owner_signal)
            assert witness.line() == "PROVIDER_TERM"
            assert witness.process.wait(timeout=5) != 0
    cancellation = cancel_watch(identity, expected_nonce=nonce)
    assert (cancellation.state, cancellation.reason) == ("quiescent", "watch_client_stopped")
    completion = json.loads(operation_paths(identity).completed.read_text())
    assert completion["nonce"] == nonce
    assert completion["process"]["returncode"] == 130
    assert completion["process"]["failure_kind"] == "cancelled"
    assert completion["process"]["cancellation_signal_sent"] is True
    assert completion["process"]["process_group_quiescent"] is True
    assert completion["process"]["escalated"] is False
    with instance_operation_lock(identity):
        pass


def test_unsolicited_exit_130_remains_a_fenced_provider_failure(identity: InstanceIdentity) -> None:
    with _witness(identity, program="raise SystemExit(130)", joined_client_fixture=True) as witness:
        nonce = json.loads(witness.line())["publishedNonce"]
        receipt = json.loads(witness.line())
        assert receipt["process"]["returncode"] == 130
        assert receipt["process"]["cancellation_signal_sent"] is False
    assert cancel_watch(identity, expected_nonce=nonce).reason == "watch_client_failed"
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("unsolicited 130 became a controlled cancellation")


@pytest.mark.parametrize("exited_before_signal, signal_sent", [(True, True), (False, False)])
def test_a_racing_exit_or_failed_signal_does_not_claim_causal_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exited_before_signal: bool,
    signal_sent: bool,
) -> None:
    polls = iter([None, 130 if exited_before_signal else None, 130])
    stops = iter([False, True])

    class Process:
        pid = 123

        def poll(self) -> int | None:
            return next(polls)

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(bounded_process, "_signal_process_group", lambda *_args: signal_sent)
    monkeypatch.setattr(bounded_process, "_is_process_group_alive", lambda _pid: False)
    result = bounded_process.run_interactive(
        "provider", (), cwd=tmp_path, env={}, stop_requested=lambda: next(stops)
    )
    assert result.returncode == 130
    assert result.failure_kind == "cancelled"
    assert result.cancellation_signal_sent is False


@pytest.mark.parametrize("mode", ["graceful", "close-descriptors"])
def test_hard_parent_death_preserves_mutation_fence_with_or_without_inheritance(
    identity: InstanceIdentity,
    mode: str,
    tmp_path: Path,
) -> None:
    paths = operation_paths(identity)
    with _witness(identity, mode, joined_client_fixture=True) as witness:
        nonce = json.loads(witness.line())["publishedNonce"]
        assert witness.line() == "PROVIDER_READY"
        witness.process.kill()
        assert witness.process.wait(timeout=5) == -signal.SIGKILL
        expected_error: type[OperationBusy] | type[OperationBlocked]
        if mode == "graceful":
            with pytest.raises(PrivateLockBusy), bounded_private_lock(paths.mutation):
                pytest.fail("the surviving child lost its inherited lock")
            expected_error = OperationBusy
        else:
            with bounded_private_lock(paths.mutation):
                pass
            expected_error = OperationBlocked
        with pytest.raises(expected_error), instance_operation_lock(identity):
            pytest.fail("hard parent death released mutation authority")
        assert cancel_watch(identity, expected_nonce=nonce, timeout_seconds=0).state == "blocked"
        other_root = tmp_path / "other-worktree"
        other_root.mkdir()
        other = derive_instance_identity(other_root, state_home=identity.state_home)
        with instance_operation_lock(other):
            pass
        witness.release_provider()
        assert witness.line() == "PROVIDER_DONE"
        with bounded_private_lock(paths.mutation, timeout_seconds=3):
            pass
    record = json.loads(paths.session.read_text())
    assert record["nonce"] == nonce
    assert "pid" not in record
    result = cancel_watch(identity, expected_nonce=nonce)
    assert (result.state, result.reason) == ("blocked", "watch_abandoned")
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("released kernel ownership erased the abandonment fence")


def test_a_finite_stubborn_child_result_never_uses_wait(
    identity: InstanceIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[signal.Signals] = []

    class StubbornProcess:
        pid = 123

        def poll(self) -> None:
            return None

        def wait(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("interactive termination called wait")

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: StubbornProcess())
    monkeypatch.setattr(bounded_process, "_is_process_group_alive", lambda _pid: True)
    monkeypatch.setattr(
        bounded_process, "_signal_process_group", lambda _pid, number: signals.append(number)
    )
    start = time.monotonic()
    result = bounded_process.run_interactive(
        "provider",
        (),
        cwd=identity.repo_root,
        env={},
        timeout_seconds=0.01,
        graceful_seconds=0.02,
        kill_seconds=0.02,
    )
    assert time.monotonic() - start < 1
    assert result.failure_kind == "timeout"
    assert result.returncode is None
    assert result.process_group_quiescent is False
    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize("graceful_seconds, kill_seconds", [(420, 1), (600, 30)])
def test_extended_restore_grace_preserves_both_finite_stop_deadlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    graceful_seconds: float,
    kill_seconds: float,
) -> None:
    now = 0.0
    signals: list[tuple[signal.Signals, float]] = []
    stops = iter([False, True])

    class StubbornProcess:
        pid = 123

        def poll(self) -> None:
            return None

        def wait(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("extended restore grace introduced an unconditional wait")

    def advance_to_deadline(_seconds: float) -> None:
        nonlocal now
        now = graceful_seconds if now < graceful_seconds else graceful_seconds + kill_seconds

    def send_signal(_pid: int, number: signal.Signals) -> bool:
        signals.append((number, now))
        return True

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: StubbornProcess())
    monkeypatch.setattr(bounded_process, "_signal_process_group", send_signal)
    monkeypatch.setattr(bounded_process, "_is_process_group_alive", lambda _pid: True)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        bounded_process,
        "time",
        SimpleNamespace(monotonic=lambda: now, sleep=advance_to_deadline),
    )
    result = bounded_process.run_interactive(
        "provider",
        (),
        cwd=tmp_path,
        env={},
        stop_requested=lambda: next(stops),
        graceful_seconds=graceful_seconds,
        kill_seconds=kill_seconds,
    )
    assert signals == [(signal.SIGTERM, 0), (signal.SIGKILL, graceful_seconds)]
    assert now == graceful_seconds + kill_seconds
    assert result.returncode is None and result.process_group_quiescent is False
    assert result.failure_kind == "cancelled" and result.escalated is True


@pytest.mark.parametrize(
    "graceful_seconds, kill_seconds, invalid",
    [
        (float("nan"), 1, "graceful_seconds"),
        (float("inf"), 1, "graceful_seconds"),
        (601, 1, "graceful_seconds"),
        (0, 1, "graceful_seconds"),
        (True, 1, "graceful_seconds"),
        (420, float("nan"), "kill_seconds"),
        (420, float("inf"), "kill_seconds"),
        (420, 31, "kill_seconds"),
        (420, 0, "kill_seconds"),
        (420, True, "kill_seconds"),
    ],
)
def test_interactive_grace_and_kill_caps_are_independently_admitted_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    graceful_seconds: float,
    kill_seconds: float,
    invalid: str,
) -> None:
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("invalid stop budget spawned")
    )
    with pytest.raises(ValueError, match=f"interactive {invalid}"):
        bounded_process.run_interactive(
            "provider",
            (),
            cwd=tmp_path,
            env={},
            graceful_seconds=graceful_seconds,
            kill_seconds=kill_seconds,
        )


def test_interactive_stdout_descriptor_routes_output_without_taking_ownership(
    tmp_path: Path,
) -> None:
    output = tmp_path / "stdout"
    with output.open("wb") as stream:
        result = bounded_process.run_interactive(
            _PYTHON,
            ("-S", "-c", "print('observed-ready')"),
            cwd=tmp_path,
            env={},
            timeout_seconds=3,
            stdout_fd=stream.fileno(),
        )
        assert result.returncode == 0 and result.process_group_quiescent
        os.fstat(stream.fileno())
    assert output.read_bytes() == b"observed-ready\n"


@pytest.mark.parametrize("descriptor", [subprocess.PIPE, subprocess.DEVNULL, 0, 1, 2, True])
def test_interactive_stdout_rejects_unowned_capture_sentinels_and_stdio_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, descriptor: int
) -> None:
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("invalid stdout reached spawn")
    )
    with pytest.raises(ValueError, match="descriptor"):
        bounded_process.run_interactive(_PYTHON, (), cwd=tmp_path, env={}, stdout_fd=descriptor)


def test_detached_effect_fixture_exposes_the_group_exit_non_implication(
    identity: InstanceIdentity,
) -> None:
    effect = """
import os, select, sys
ready_descriptor = int(sys.argv[1])
os.write(ready_descriptor, b'ready')
os.close(ready_descriptor)
ready, _, _ = select.select([0], [], [], 10)
if ready:
    os.read(0, 32)
print('EFFECT_DONE', flush=True)
"""
    client = f"""
import os, select, subprocess, sys
read_descriptor, write_descriptor = os.pipe()
effect = subprocess.Popen(
    [sys.executable, '-S', '-c', {effect!r}, str(write_descriptor)],
    pass_fds=(write_descriptor,), start_new_session=True,
)
os.close(write_descriptor)
ready, _, _ = select.select([read_descriptor], [], [], 5)
assert ready and os.read(read_descriptor, 5) == b'ready'
os.close(read_descriptor)
print('PROVIDER_READY', flush=True)
"""
    paths = operation_paths(identity)
    with _witness(identity, program=client) as witness:
        nonce = json.loads(witness.line())["publishedNonce"]
        assert witness.line() == "PROVIDER_READY"
        receipt = json.loads(witness.line())
        assert receipt["process"]["process_group_quiescent"] is True
        assert receipt["outcome"]["reason"] == "client_contract_unadmitted"
        assert witness.process.wait(timeout=5) == 0
        with bounded_private_lock(paths.mutation):
            pass
        with pytest.raises(OperationBlocked), instance_operation_lock(identity):
            pytest.fail("an unadmitted detached client released ownership")
        assert cancel_watch(identity, expected_nonce=nonce).state == "blocked"
        witness.release_provider()
        assert witness.line() == "EFFECT_DONE"


def test_watch_suppresses_raw_provider_stderr(identity: InstanceIdentity) -> None:
    provider = (
        "import sys; print('private-provider-diagnostic', file=sys.stderr); "
        "print('PROVIDER_READY', flush=True)"
    )
    with _witness(identity, program=provider, joined_client_fixture=True) as witness:
        assert "publishedNonce" in json.loads(witness.line())
        assert witness.line() == "PROVIDER_READY"
        receipt = json.loads(witness.line())
        assert receipt["outcome"]["reason"] == "watch_client_stopped"
        assert witness.process.wait(timeout=5) == 0
        assert witness.process.stderr is not None
        assert witness.process.stderr.read(4096) == b""


def test_interactive_default_preserves_curated_task_stderr() -> None:
    program = """
import os, sys
from pathlib import Path
from scripts.bounded_process import run_interactive
result = run_interactive(
    sys.executable,
    ('-S', '-c', "import sys; print('curated-cli-diagnostic', file=sys.stderr)"),
    cwd=Path.cwd(), env=os.environ, timeout_seconds=2,
)
raise SystemExit(0 if result.returncode == 0 else 2)
"""
    completed = subprocess.run(
        [_PYTHON, "-S", "-c", program],
        cwd=_SOURCE_ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == "curated-cli-diagnostic\n"


@pytest.mark.parametrize("environment_kind", ["missing", "broken", "mismatched"])
def test_stdlib_cancel_survives_unusable_environment_and_edited_lockfile(
    identity: InstanceIdentity,
    environment_kind: str,
) -> None:
    backend = identity.repo_root / "backend"
    backend.mkdir()
    (backend / "uv.lock").write_text("edited dependency graph, not installed\n")
    venv = backend / ".venv"
    if environment_kind != "missing":
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("unusable virtual environment interpreter\n")
        (venv / "pyvenv.cfg").write_text("home = /unavailable/runtime\n")
    if environment_kind == "mismatched":
        (venv / "old-lock.sha256").write_text("stale\n")
    environment = {**os.environ, "VIRTUAL_ENV": str(venv), "UV_PROJECT_ENVIRONMENT": str(venv)}

    def cancel() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                _PYTHON,
                "-S",
                "-m",
                "scripts.dev_environment.watch_session",
                "--repo-root",
                str(identity.repo_root),
                "--state-home",
                str(identity.state_home),
                "--timeout-seconds",
                "0",
            ],
            cwd=_SOURCE_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

    clean = cancel()
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout)["state"] == "quiescent"
    with owned_watch_session(identity) as current:
        stopped = cancel()
        assert stopped.returncode == 2, stopped.stderr
        assert json.loads(stopped.stdout)["reason"] == "cancellation_timeout"
        assert current.stop_requested()
