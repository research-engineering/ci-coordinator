from __future__ import annotations

# The repository's test assertion exception is scoped to backend/tests.
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Final, cast

import pytest
import scripts.mutation.detached_worktree_lifecycle as lifecycle_module
from scripts.bounded_git import BoundedGitResult
from scripts.bounded_process import SUPPORTED_PLATFORMS, CommandResult, current_process_scope, spawn
from scripts.dev_environment.environment import borrow_managed_process, managed_process_invocation
from scripts.mutation.detached_worktree_lifecycle import (
    DEFAULT_MAX_BUFFER_BYTES,
    DEFAULT_TIMEOUT_MS,
    DetachedWorktreeLifecycle,
    UnsupportedPlatformError,
)
from scripts.tests.test_dev_environment_dependencies import (
    dependency_identity_fixture,
    managed_entry_context,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"{name} is required for mutation lifecycle tests")
    return executable


GIT: Final = _required_executable("git")


@pytest.mark.parametrize("listed_status,expected", [(0, False), (1, True)])
def test_unscoped_failed_remove_and_list_preserve_residual_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listed_status: int,
    expected: bool,
) -> None:
    owner = DetachedWorktreeLifecycle(repo_root=tmp_path, temp_prefix="legacy-cleanup-")
    worktree = owner.worktree
    worktree.mkdir()
    owner.worktree_registration_attempted = True
    calls: list[tuple[str, ...]] = []

    def cleanup_git(arguments: tuple[str, ...]) -> CommandResult:
        calls.append(arguments)
        if arguments[:2] == ("worktree", "remove"):
            return CommandResult(1, "", "remove failed")
        if arguments == ("worktree", "prune"):
            return CommandResult(0, "", "")
        assert arguments == ("worktree", "list", "--porcelain")
        return CommandResult(listed_status, "", "")

    monkeypatch.setattr(owner, "_cleanup_git", cleanup_git)
    result = owner._perform_cleanup()
    assert calls == [
        ("worktree", "remove", "--force", str(worktree)),
        ("worktree", "prune"),
        ("worktree", "list", "--porcelain"),
    ]
    assert result.to_report() == {
        "state": "failed",
        "worktreeRemoval": "failed",
        "removalExitCode": 1,
        "pruneExitCode": 0,
        "residualRegistration": expected,
        "output": "remove failed",
    }


@pytest.mark.parametrize("mode", ["positive", "expired", "nested", "residual"])
def test_native_owned_cleanup_uses_one_remaining_parent_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    _initialize_repository(identity.repo_root)
    owners = [
        DetachedWorktreeLifecycle(repo_root=identity.repo_root, temp_prefix="owned-stop-cut-")
        for _ in range(2 if mode == "nested" else 1)
    ]
    outer = current_process_scope()
    primary = RuntimeError("original caller failure")
    results = []
    try:
        revision = owners[0].git(("rev-parse", "HEAD")).stdout.strip()
        for owner in owners:
            owner.add_detached_worktree(revision)
        if mode == "residual":
            (owners[0].temp_root / "keep.txt").write_text("owned residual")
        original = owners[0]._perform_cleanup

        def guarded(
            scope: lifecycle_module._GitCleanupScope | None = None,
        ) -> lifecycle_module.CleanupResult:
            assert scope is not None and current_process_scope() is scope
            if mode != "expired":
                valid_arguments = ("worktree", "remove", "--force", str(owners[0].worktree))
                for command, arguments, cwd in (
                    (sys.executable, valid_arguments, identity.repo_root),
                    (GIT, ("status",), identity.repo_root),
                    (GIT, valid_arguments, tmp_path),
                ):
                    with pytest.raises(ValueError, match="outside owned Git cleanup"):
                        spawn(command, arguments, cwd=cwd, max_buffer=4096, timeout_seconds=30)
            return original(scope)

        monkeypatch.setattr(owners[0], "_perform_cleanup", guarded)
        with managed_entry_context(identity) as (context, writer, descriptor):
            with borrow_managed_process(context) as parent:
                cut = time.monotonic() - 1 if mode == "expired" else time.monotonic() + 3
                os.write(writer, struct.pack("!4sd", b"CF1:", cut))
                assert parent.cancellation_deadline == cut and parent.stop_requested()
                for index, owner in enumerate(owners):
                    if index:
                        while time.monotonic() < cut:
                            time.sleep(0.01)
                    with pytest.raises(RuntimeError) as caught:
                        try:
                            raise primary
                        finally:
                            results.append(owner.cleanup())
                    assert caught.value is primary
                    assert current_process_scope() is parent
                    assert parent.cancellation_deadline == cut
                    os.fstat(descriptor)
                    assert owner.cleanup() is results[-1]
            assert current_process_scope() is outer
        observed = _run(
            [GIT, "-C", str(identity.repo_root), "worktree", "list", "--porcelain"]
        ).stdout
        for index, (owner, result) in enumerate(zip(owners, results, strict=True)):
            expired = mode == "expired" or index > 0
            assert result.state == ("failed" if expired or mode == "residual" else "passed"), result
            if expired:
                assert result.residual_registration is None
                assert f"worktree {owner.worktree}" in observed.splitlines()
                assert owner.worktree.exists()
            else:
                assert result.worktree_removal == "removed"
                assert f"worktree {owner.worktree}" not in observed.splitlines()
                if mode == "residual":
                    assert result.residual_registration is False
                    assert (owner.temp_root / "keep.txt").read_text() == "owned residual"
                else:
                    assert not owner.temp_root.exists()
    finally:
        for owner in owners:
            temporary = owner._temp_root
            if temporary is not None:
                worktree = temporary / "worktree"
                if worktree.exists():
                    _run(
                        [
                            GIT,
                            "-C",
                            str(identity.repo_root),
                            "worktree",
                            "remove",
                            "--force",
                            str(worktree),
                        ]
                    )
                shutil.rmtree(temporary, ignore_errors=True)


PROBE_COMMAND_SOURCE: Final = """
import json
import os
from pathlib import Path
import subprocess
import sys
import time

descriptors = tuple(map(int, sys.argv[1:]))
try:
    for descriptor in descriptors:
        os.fstat(descriptor)
except OSError:
    Path(os.environ["PROBE_MARKER"]).with_suffix(".lease").write_text(
        json.dumps({"pid": None, "retained": False, "count": len(descriptors)})
    )
    Path(os.environ["PROBE_MARKER"]).write_text(json.dumps({
        "commandPid": os.getpid(), "grandchildPid": None,
        "tempRoot": os.environ["PROBE_TEMP_ROOT"], "worktree": os.environ["PROBE_WORKTREE"],
    }))
    raise SystemExit(1)
grandchild_code = (
    "import json, os, sys, time\\nfrom pathlib import Path\\n"
    "fds = tuple(map(int, sys.argv[1:])); retained = True\\n"
    "try:\\n    for descriptor in fds: os.fstat(descriptor)\\n"
    "except OSError: retained = False\\n"
    "Path(os.environ['PROBE_MARKER']).with_suffix('.lease').write_text("
    "json.dumps({'pid': os.getpid(), 'retained': retained, 'count': len(fds)}))\\n"
    "time.sleep(60)\\n"
)
grandchild = subprocess.Popen(
    [sys.executable, "-c", grandchild_code, *sys.argv[1:]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    pass_fds=descriptors,
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
from scripts.bounded_process import current_process_scope
from scripts.dev_environment.environment import managed_process_entrypoint

def main():
    repo_root = Path(sys.argv[1])
    marker_path = Path(sys.argv[2])
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=repo_root, temp_prefix="mutation-lifecycle-probe-worker-",
    )
    lifecycle.install_signal_handlers()
    cleanup = None
    scope = current_process_scope()
    descriptors = () if scope is None else scope.inherited_fds
    try:
        revision = lifecycle.git(("rev-parse", "HEAD")).stdout.strip()
        lifecycle.add_detached_worktree(revision)
        lifecycle.run(
            sys.executable, ("-c", {PROBE_COMMAND_SOURCE!r}, *map(str, descriptors)),
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
    return 0
raise SystemExit(managed_process_entrypoint(main))
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
@pytest.mark.parametrize("managed", [False, True], ids=["ambient", "explicit-managed"])
def test_signal_propagates_after_cleanup(
    tmp_path: Path, received_signal: signal.Signals, managed: bool
) -> None:
    repo_root = tmp_path / "repo"
    marker_path = tmp_path / "ready.json"
    _initialize_repository(repo_root)
    lifetime = ExitStack()
    if managed:
        lease_root = tmp_path / "lease"
        lease_root.mkdir()
        identity = dependency_identity_fixture(lease_root)
        context, _writer, _descriptor = lifetime.enter_context(managed_entry_context(identity))
        lifetime.enter_context(borrow_managed_process(context))
    invocation = lifetime.enter_context(
        managed_process_invocation(
            ("-c", WORKER_SOURCE, str(repo_root), str(marker_path)),
            timeout_seconds=None,
            graceful_seconds=10,
        )
    )
    worker = subprocess.Popen(
        [sys.executable, *invocation.arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=invocation.inherited_fds,
    )
    marker: dict[str, object] = {}
    try:
        ready_by = time.monotonic() + 10
        marker = _read_json_when_ready(marker_path, 10)
        retained = _read_json_when_ready(
            marker_path.with_suffix(".lease"), max(0, ready_by - time.monotonic())
        )
        assert retained == {
            "pid": marker["grandchildPid"],
            "retained": True,
            "count": len(invocation.inherited_fds)
            - (1 if invocation.cancellation_fd is not None else 0),
        }
        stop_by = time.monotonic() + 10
        if invocation.cancellation_fd is not None:
            os.write(invocation.cancellation_fd, struct.pack("!4sd", b"CF1:", stop_by))
        worker.send_signal(received_signal)
        _stdout, stderr = worker.communicate(timeout=max(0, stop_by - time.monotonic()))
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
        owned_group = marker.get("commandPid")
        if isinstance(owned_group, int):
            with suppress(ProcessLookupError):
                os.killpg(owned_group, signal.SIGKILL)
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        for key in ("commandPid", "grandchildPid"):
            value = marker.get(key)
            if isinstance(value, int):
                _kill_if_running(value)
        lifetime.close()


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
        scope = current_process_scope()
        descriptors = () if scope is None else scope.inherited_fds
        execution = lifecycle.run(
            sys.executable,
            ("-c", STUBBORN_PROBE_COMMAND_SOURCE, *map(str, descriptors)),
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
    pass_fds=tuple(map(int, sys.argv[2:])),
)
Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
"""
    descendant_pid = 0
    try:
        scope = current_process_scope()
        descriptors = () if scope is None else scope.inherited_fds
        execution = lifecycle.run(
            sys.executable,
            ("-c", command, str(marker_path), *map(str, descriptors)),
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
    if current_process_scope() is None:
        assert cleanup.prune_exit_code == 0
        assert cleanup.residual_registration is False
        assert not lifecycle.temp_root.exists()
    else:
        assert cleanup.prune_exit_code is None
        assert cleanup.residual_registration is None
        assert lifecycle.temp_root.exists()
        legacy = lifecycle._perform_cleanup()
        assert legacy.state == "failed" and legacy.prune_exit_code == 0
        assert legacy.residual_registration is False
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
    monkeypatch.setattr(
        lifecycle_module._GitCleanupScope,
        "remove",
        lambda _self, path: fail_removal(path),
    )

    try:
        cleanup = lifecycle.cleanup()
        assert cleanup.state == "failed"
        assert cleanup.worktree_removal == "not-needed"
        assert cleanup.output == "OSError: injected cleanup failure"
        assert lifecycle.cleanup() == cleanup
    finally:
        remove_path(temp_root)
