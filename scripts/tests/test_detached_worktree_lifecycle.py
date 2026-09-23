from __future__ import annotations

# The repository's test assertion exception is scoped to backend/tests.
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Final, cast

import pytest
import scripts.mutation.detached_worktree_lifecycle as lifecycle_module
from scripts.bounded_git import BoundedGitResult
from scripts.bounded_process import SUPPORTED_PLATFORMS, CommandResult
from scripts.mutation.detached_worktree_lifecycle import (
    DEFAULT_MAX_BUFFER_BYTES,
    DEFAULT_TIMEOUT_MS,
    DetachedWorktreeLifecycle,
    UnsupportedPlatformError,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"{name} is required for mutation lifecycle tests")
    return executable


GIT: Final = _required_executable("git")

PROBE_COMMAND_SOURCE: Final = """
import json
import os
from pathlib import Path
import subprocess
import sys
import time

grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
Path(os.environ["PROBE_MARKER"]).write_text(
    json.dumps(
        {
            "commandPid": os.getpid(),
            "grandchildPid": grandchild.pid,
            "tempRoot": os.environ["PROBE_TEMP_ROOT"],
            "worktree": os.environ["PROBE_WORKTREE"],
        }
    ),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""

STUBBORN_PROBE_COMMAND_SOURCE: Final = f"""
import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
{PROBE_COMMAND_SOURCE}
"""

WORKER_SOURCE: Final = f"""
import sys
from pathlib import Path

sys.path.insert(0, {str(REPO_ROOT)!r})
from scripts.mutation.detached_worktree_lifecycle import DetachedWorktreeLifecycle

repo_root = Path(sys.argv[1])
marker_path = Path(sys.argv[2])
lifecycle = DetachedWorktreeLifecycle(
    repo_root=repo_root,
    temp_prefix="mutation-lifecycle-probe-worker-",
)
lifecycle.install_signal_handlers()
cleanup = None
try:
    revision = lifecycle.git(("rev-parse", "HEAD")).stdout.strip()
    lifecycle.add_detached_worktree(revision)
    lifecycle.run(
        sys.executable,
        ("-c", {PROBE_COMMAND_SOURCE!r}),
        env={{
            **dict(__import__("os").environ),
            "PROBE_MARKER": str(marker_path),
            "PROBE_TEMP_ROOT": str(lifecycle.temp_root),
            "PROBE_WORKTREE": str(lifecycle.worktree),
        }},
    )
    lifecycle.assert_running()
    raise RuntimeError("probe command exited before receiving a signal")
except RuntimeError:
    if lifecycle.received_signal is None:
        raise
finally:
    cleanup = lifecycle.cleanup()

if lifecycle.cleanup() != cleanup:
    raise RuntimeError("cleanup result changed across repeated calls")
lifecycle.rethrow_signal_if_needed()
"""


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _initialize_repository(repo_root: Path) -> None:
    _run([GIT, "init", "--quiet", str(repo_root)])
    _run([GIT, "-C", str(repo_root), "config", "user.name", "Mutation Probe"])
    _run(
        [
            GIT,
            "-C",
            str(repo_root),
            "config",
            "user.email",
            "mutation-probe@example.invalid",
        ]
    )
    (repo_root / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    _run([GIT, "-C", str(repo_root), "add", "fixture.txt"])
    _run([GIT, "-C", str(repo_root), "commit", "--quiet", "-m", "probe fixture"])


def _read_json_when_ready(path: Path, timeout_seconds: float) -> dict[str, object]:
    parsed: object = None

    def ready() -> bool:
        nonlocal parsed
        if not path.exists():
            return False
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return isinstance(parsed, dict)

    _wait_until(ready, timeout_seconds)
    return cast(dict[str, object], parsed)


def _wait_until(predicate: Callable[[], bool], timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for probe condition")
        time.sleep(0.025)


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    result = subprocess.run(
        ["/bin/ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _marker_int(marker: dict[str, object], key: str) -> int:
    value = marker[key]
    assert isinstance(value, int)
    return value


def _marker_path(marker: dict[str, object], key: str) -> Path:
    value = marker[key]
    assert isinstance(value, str)
    return Path(value)


def _kill_if_running(pid: int) -> None:
    if _is_process_running(pid):
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("received_signal", [signal.SIGINT, signal.SIGTERM])
def test_signal_propagates_after_cleanup(tmp_path: Path, received_signal: signal.Signals) -> None:
    repo_root = tmp_path / "repo"
    marker_path = tmp_path / "ready.json"
    _initialize_repository(repo_root)
    worker = subprocess.Popen(
        [sys.executable, "-c", WORKER_SOURCE, str(repo_root), str(marker_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    marker: dict[str, object] = {}
    try:
        marker = _read_json_when_ready(marker_path, 10)
        worker.send_signal(received_signal)
        _stdout, stderr = worker.communicate(timeout=10)
        assert worker.returncode == -received_signal.value, stderr

        command_pid = _marker_int(marker, "commandPid")
        grandchild_pid = _marker_int(marker, "grandchildPid")
        _wait_until(lambda: not _is_process_running(command_pid), 5)
        _wait_until(lambda: not _is_process_running(grandchild_pid), 5)

        temp_root = _marker_path(marker, "tempRoot")
        worktree = _marker_path(marker, "worktree")
        assert not temp_root.exists()
        registered = _run([GIT, "-C", str(repo_root), "worktree", "list", "--porcelain"])
        assert f"worktree {worktree}" not in registered.stdout.split("\n")
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        for key in ("commandPid", "grandchildPid"):
            value = marker.get(key)
            if isinstance(value, int):
                _kill_if_running(value)


def test_timeout_force_kills_stubborn_process_group(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    marker_path = tmp_path / "ready.json"
    _initialize_repository(repo_root)
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=repo_root,
        temp_prefix="mutation-lifecycle-timeout-worker-",
    )
    marker: dict[str, object] = {}
    cleanup = None
    try:
        revision = lifecycle.git(("rev-parse", "HEAD")).stdout.strip()
        lifecycle.add_detached_worktree(revision)
        execution = lifecycle.run(
            sys.executable,
            ("-c", STUBBORN_PROBE_COMMAND_SOURCE),
            env={
                **os.environ,
                "PROBE_MARKER": str(marker_path),
                "PROBE_TEMP_ROOT": str(lifecycle.temp_root),
                "PROBE_WORKTREE": str(lifecycle.worktree),
            },
            timeout_ms=500,
        )
        marker = _read_json_when_ready(marker_path, 1)
        assert execution.timed_out
        assert execution.signal == "SIGKILL"
        _wait_until(lambda: not _is_process_running(_marker_int(marker, "commandPid")), 5)
        _wait_until(lambda: not _is_process_running(_marker_int(marker, "grandchildPid")), 5)

        cleanup = lifecycle.cleanup()
        assert cleanup.state == "passed"
        assert cleanup.worktree_removal == "removed"
        assert lifecycle.cleanup() == cleanup
        assert not _marker_path(marker, "tempRoot").exists()
    finally:
        if cleanup is None:
            lifecycle.cleanup()
        for key in ("commandPid", "grandchildPid"):
            value = marker.get(key)
            if isinstance(value, int):
                _kill_if_running(value)


def test_residual_process_group_is_terminated(tmp_path: Path) -> None:
    marker_path = tmp_path / "descendant.pid"
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-residual-worker-",
    )
    command = """
import subprocess
import sys
from pathlib import Path

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
"""
    descendant_pid = 0
    try:
        execution = lifecycle.run(
            sys.executable,
            ("-c", command, str(marker_path)),
            timeout_ms=5_000,
        )
        assert execution.status is None
        assert execution.failure_kind == "residual-descendant"
        descendant_pid = int(marker_path.read_text(encoding="utf-8"))
        _wait_until(lambda: not _is_process_running(descendant_pid), 5)
    finally:
        lifecycle.cleanup()
        if descendant_pid:
            _kill_if_running(descendant_pid)


def test_output_capture_has_one_shared_ten_mibibyte_cap(tmp_path: Path) -> None:
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-output-cap-",
    )
    try:
        execution = lifecycle.run(
            sys.executable,
            (
                "-c",
                (
                    "import os, sys, time; "
                    f"os.write(sys.stdout.fileno(), b'x' * ({DEFAULT_MAX_BUFFER_BYTES} + 1)); "
                    "time.sleep(60)"
                ),
            ),
        )
        assert len(execution.stdout.encode("utf-8")) == DEFAULT_MAX_BUFFER_BYTES
        assert execution.stderr == ""
        assert execution.error is not None
        assert execution.failure_kind == "output-limit"
        assert str(execution.error) == (
            f"{sys.executable}: output exceeded {DEFAULT_MAX_BUFFER_BYTES} bytes"
        )
        assert execution.signal in {"SIGTERM", "SIGKILL"}
    finally:
        lifecycle.cleanup()


def test_cleanup_prunes_after_worktree_remove_failure(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _initialize_repository(repo_root)
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=repo_root,
        temp_prefix="mutation-lifecycle-prune-",
    )
    revision = lifecycle.git(("rev-parse", "HEAD")).stdout.strip()
    lifecycle.add_detached_worktree(revision)
    _run(
        [
            GIT,
            "-C",
            str(repo_root),
            "worktree",
            "remove",
            "--force",
            str(lifecycle.worktree),
        ]
    )

    cleanup = lifecycle.cleanup()

    assert cleanup.state == "failed"
    assert cleanup.worktree_removal == "failed"
    assert cleanup.removal_exit_code == 128
    assert cleanup.prune_exit_code == 0
    assert cleanup.residual_registration is False
    assert not lifecycle.temp_root.exists()
    assert lifecycle.cleanup() == cleanup


def test_spawn_failure_is_reported_without_leaking_temp_root(tmp_path: Path) -> None:
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-spawn-error-",
    )
    execution = lifecycle.run("definitely-not-a-real-mutation-command", ())
    assert execution.status is None
    assert execution.error is not None
    assert execution.failure_kind == "spawn"
    assert execution.timed_out is False
    assert lifecycle.cleanup().worktree_removal == "not-needed"


def test_unsupported_platform_fails_before_allocating_temp_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert frozenset({"darwin", "linux"}) == SUPPORTED_PLATFORMS
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(UnsupportedPlatformError, match="proved only for Linux and macOS"):
        DetachedWorktreeLifecycle(repo_root=REPO_ROOT, temp_prefix="must-not-exist-")


@pytest.mark.parametrize("timeout_ms", [0, -1, True])
def test_run_rejects_a_nonpositive_or_noninteger_timeout(
    tmp_path: Path,
    timeout_ms: int,
) -> None:
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-invalid-timeout-",
    )
    try:
        with pytest.raises(ValueError, match="timeout_ms must be a positive integer"):
            lifecycle.run(sys.executable, ("-c", "pass"), timeout_ms=timeout_ms)
    finally:
        lifecycle.cleanup()


def test_run_delegates_the_default_finite_deadline_and_stop_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-delegation-",
    )
    observed_timeout: float | None = None
    observed_stop: Callable[[], bool] | None = None

    def fake_spawn(
        _command: str,
        _args: object,
        **kwargs: object,
    ) -> CommandResult:
        nonlocal observed_stop, observed_timeout
        timeout = kwargs["timeout_seconds"]
        stop = kwargs["stop_requested"]
        assert isinstance(timeout, float)
        assert callable(stop)
        observed_timeout = timeout
        observed_stop = cast(Callable[[], bool], stop)
        return CommandResult(0, "", "")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(lifecycle_module, "spawn", fake_spawn)
    try:
        result = lifecycle.run(sys.executable, ("-c", "pass"))
        assert result.status == 0
        assert observed_timeout == DEFAULT_TIMEOUT_MS / 1_000
        assert observed_stop is not None and observed_stop() is False
    finally:
        lifecycle.cleanup()


def test_run_suppresses_a_late_success_after_the_adapter_observes_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-late-stop-",
    )

    def fake_spawn(_command: str, _args: object, **_kwargs: object) -> CommandResult:
        lifecycle.request_stop(signal.SIGTERM)
        return CommandResult(0, "completed", "")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(lifecycle_module, "spawn", fake_spawn)
    try:
        result = lifecycle.run(sys.executable, ("-c", "pass"))
        assert result.status is None
        assert result.failure_kind == "cancelled"
        assert result.stdout == "completed"
    finally:
        lifecycle.cleanup()


def test_git_suppresses_a_late_success_after_the_adapter_observes_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-late-git-stop-",
    )

    def fake_run_git(*_args: object, **_kwargs: object) -> BoundedGitResult:
        lifecycle.request_stop(signal.SIGINT)
        return BoundedGitResult(0, "completed", "")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(lifecycle_module, "run_git", fake_run_git)
    try:
        with pytest.raises(RuntimeError, match="interrupted by SIGINT"):
            lifecycle.git(("status", "--short"))
    finally:
        lifecycle.cleanup()


def test_construction_and_noop_cleanup_do_not_allocate_temporary_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_allocation(*_args: object, **_kwargs: object) -> str:
        pytest.fail("temporary state must be allocated only when first requested")

    monkeypatch.setattr(tempfile, "mkdtemp", forbidden_allocation)
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-lazy-allocation-",
    )

    assert lifecycle.cleanup().worktree_removal == "not-needed"


def test_cleanup_converts_an_ordinary_cleanup_exception_to_a_stable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=tmp_path,
        temp_prefix="mutation-lifecycle-cleanup-failure-",
    )
    temp_root = lifecycle.temp_root
    remove_path = lifecycle_module._remove_path

    def fail_removal(_path: Path) -> None:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(lifecycle_module, "_remove_path", fail_removal)

    try:
        cleanup = lifecycle.cleanup()
        assert cleanup.state == "failed"
        assert cleanup.worktree_removal == "not-needed"
        assert cleanup.output == "OSError: injected cleanup failure"
        assert lifecycle.cleanup() == cleanup
    finally:
        remove_path(temp_root)
