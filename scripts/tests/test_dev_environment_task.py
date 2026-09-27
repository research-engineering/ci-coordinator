from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Sequence
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import cast

import pytest
from scripts.bounded_process import CommandResult, InteractiveResult, current_process_scope, spawn
from scripts.dev_environment import bootstrap_witness, task
from scripts.dev_environment.diagnostics import Diagnostic, Reason
from scripts.dev_environment.environment import (
    EnvironmentError,
    admit_dependencies,
    borrow_managed_process,
    dependency_lease,
    managed_process_argument,
    managed_process_invocation,
)
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.lifecycle import OperationBusy, instance_operation_lock
from scripts.dev_environment.watch_session import CancelResult, owned_watch_session
from scripts.quality_plan import MANAGED_DEPENDENCY_ARGUMENT, select_quality_plan
from scripts.tests.test_dev_environment_dependencies import (
    dependency_identity_fixture,
    managed_entry_context,
    pending_scopes,
    prepared_quality_identity,
)

_SOURCE_ROOT = Path(__file__).resolve().parents[2]


def invoke(identity: InstanceIdentity, arguments: Sequence[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    status = task.run(
        arguments,
        repo_root=identity.repo_root,
        stdout=stdout,
        stderr=stderr,
        state_home=identity.state_home,
    )
    return status, stdout.getvalue(), stderr.getvalue()


@pytest.mark.parametrize(
    "arguments,mode",
    [
        (["check"], "local"),
        (["check", "local"], "local"),
        (["check", "portable"], "portable"),
        (["check:portable"], "portable"),
    ],
)
def test_quality_task_selects_its_real_effects_before_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], mode: str
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    stages: list[str] = []

    def check_mode() -> None:
        if mode == "local":
            with (
                pytest.raises(EnvironmentError, match=Reason.PREPARATION_IN_PROGRESS),
                dependency_lease(identity),
            ):
                pytest.fail("install-capable task used a shared lease")
        else:
            with dependency_lease(identity):
                pass
        with (
            pytest.raises(EnvironmentError, match=Reason.PREPARATION_IN_PROGRESS),
            dependency_lease(identity, exclusive=True),
        ):
            pytest.fail("task did not retain its lease")

    def admit(observed: InstanceIdentity, scope: str) -> None:
        assert observed == identity
        stages.append(scope)
        check_mode()

    def child(command: str, argv: Sequence[str], **options: object) -> InteractiveResult:
        stages.append("child")
        check_mode()
        assert command == str(identity.repo_root / "backend/.venv/bin/python")
        if managed_process_argument(argv) is not None:
            outer = current_process_scope()
            assert outer is not None
            assert set(outer.inherited_fds) <= set(cast(tuple[int, ...], options["inherited_fds"]))
            argv = argv[:-2]
        public_arguments = ["portable"] if mode == "portable" else arguments[1:]
        assert tuple(argv[:-2]) == ("-m", "scripts.quality_plan", *public_arguments)
        assert argv[-2] == MANAGED_DEPENDENCY_ARGUMENT
        assert len(argv[-1].encode()) <= 512
        assert json.loads(argv[-1]) == {
            "version": 1,
            "fd": cast(tuple[int, ...], options["inherited_fds"])[0],
            "rootDigest": identity.root_digest,
            "selectionSha256": select_quality_plan(public_arguments, identity.repo_root).sha256,
        }
        assert pending_scopes(identity) == set()
        return InteractiveResult(0, True)

    monkeypatch.setattr(task, "admit_dependencies", admit)
    monkeypatch.setattr(task, "run_interactive", child)
    monkeypatch.setattr(
        task,
        "prepare_dependencies",
        lambda *_args, **_kwargs: pytest.fail("duplicated installation"),
    )
    assert invoke(identity, arguments) == (0, "", "")
    assert stages == ["backend", "frontend", "child"]
    assert pending_scopes(identity) == set()


@pytest.mark.parametrize(
    "arguments",
    [
        ["check:portable", "local"],
        ["check:portable", "portable"],
        ["check", "unknown"],
        ["check", MANAGED_DEPENDENCY_ARGUMENT, "{}"],
        ["check:portable", MANAGED_DEPENDENCY_ARGUMENT, "{}"],
    ],
)
def test_quality_task_rejects_invalid_and_private_forwarding_without_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    monkeypatch.setattr(
        task,
        "dependency_lease",
        lambda *_args, **_kwargs: pytest.fail("lease acquired for invalid argv"),
    )
    monkeypatch.setattr(
        task,
        "run_interactive",
        lambda *_args, **_kwargs: pytest.fail("invalid argv was dispatched"),
    )
    status, stdout, stderr = invoke(identity, arguments)
    assert status == 2 and stdout == ""
    assert json.loads(stderr)["diagnostic"]["reason"] == Reason.INVALID_ARGUMENT
    assert not identity.state_home.exists()


_QUALITY_ENTRY_CHILD = """
import os, sys
from pathlib import Path
source, root, ready, release, failure, *arguments = sys.argv[1:]
sys.path.insert(0, source)
from scripts import quality_plan
from scripts.dev_environment.environment import managed_process_entrypoint
quality_plan.REPO_ROOT = Path(root)
def execute(command, *, source_environment):
    os.write(int(ready), (command.command_id + '\\n').encode())
    if os.read(int(release), 1) != b'x':
        raise RuntimeError('witness release channel closed')
    if command.command_id == failure:
        raise RuntimeError('selected command failed')
quality_plan._execute_command = execute
raise SystemExit(managed_process_entrypoint(lambda: quality_plan.main(sys.argv[6:])))
"""

_QUALITY_TASK_PARENT = """
import sys
from pathlib import Path
source, root, state, ready, release, failure, child_source, *arguments = sys.argv[1:]
sys.path.insert(0, source)
from scripts.dev_environment import task
from scripts.dev_environment.environment import managed_process_entrypoint
run_interactive = task.run_interactive
def child(command, argv, **options):
    options['inherited_fds'] = (*options['inherited_fds'], int(ready), int(release))
    return run_interactive(sys.executable,
        ('-c', child_source, source, root, ready, release, failure, *argv[2:]), **options)
task.run_interactive = child
raise SystemExit(managed_process_entrypoint(lambda: task.run(sys.argv[8:],
    repo_root=Path(root), state_home=Path(state), stdout=sys.stdout, stderr=sys.stderr)))
"""


@pytest.mark.parametrize(
    "mode,failure,expected_pending",
    [
        ("local", "", set()),
        ("local", "python.install-check", {"backend"}),
        ("local", "frontend.install", {"frontend"}),
        ("local", "python.lint", set()),
        ("portable", "", set()),
    ],
)
def test_native_task_to_quality_borrow_is_bound_to_actual_phase_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    failure: str,
    expected_pending: set[str],
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    lifetime = ExitStack()
    try:
        invocation = lifetime.enter_context(
            managed_process_invocation(
                (
                    "-c",
                    _QUALITY_TASK_PARENT,
                    str(_SOURCE_ROOT),
                    str(identity.repo_root),
                    str(identity.state_home),
                    str(ready_write),
                    str(release_read),
                    failure,
                    _QUALITY_ENTRY_CHILD,
                    "check",
                    mode,
                ),
                timeout_seconds=None,
                graceful_seconds=3,
            )
        )
        process = subprocess.Popen(
            [sys.executable, *invocation.arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(*invocation.inherited_fds, ready_write, release_read),
        )
        os.close(ready_write)
        ready_write = -1
        os.close(release_read)
        release_read = -1
        commands = (
            ["python.test"]
            if mode == "portable"
            else ["python.lock-check", "python.install-check", "frontend.install", "python.lint"]
        )
        for command_id in commands:
            assert select.select([ready_read], [], [], 10)[0], (
                "quality child did not reach its barrier"
            )
            assert os.read(ready_read, 4096).decode().strip() == command_id
            if mode == "local":
                with (
                    pytest.raises(EnvironmentError, match=Reason.PREPARATION_IN_PROGRESS),
                    dependency_lease(identity),
                ):
                    pytest.fail("native check admitted a concurrent reader")
            else:
                with dependency_lease(identity):
                    pass
            assert pending_scopes(identity) == {
                "python.install-check": {"backend"},
                "frontend.install": {"frontend"},
            }.get(command_id, set())
            os.write(release_write, b"x")
            if command_id == failure:
                break
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == (1 if failure else 0), stderr.decode()
        receipts = [json.loads(line) for line in stdout.splitlines()]
        assert receipts[-1]["succeeded"] is not bool(failure)
        assert pending_scopes(identity) == expected_pending
        for scope in ("backend", "frontend"):
            if scope in expected_pending:
                with pytest.raises(EnvironmentError, match=Reason.ENVIRONMENT_STALE):
                    admit_dependencies(identity, scope)
            else:
                admit_dependencies(identity, scope)
        with dependency_lease(identity, exclusive=True):
            pass
    finally:
        for descriptor in (ready_read, ready_write, release_read, release_write):
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
        lifetime.close()


_TRANSITIVE_QUALITY_ENTRY = """
import os, sys
from pathlib import Path
source, root, ready, release, failure, *arguments = sys.argv[1:]
sys.path.insert(0, source)
from scripts import quality_plan
from scripts.dev_environment.environment import managed_process_entrypoint
quality_plan.REPO_ROOT = Path(root)
def main():
    (Path(root) / 'quality.pid').write_text(str(os.getpid()))
    return quality_plan.main(sys.argv[6:])
raise SystemExit(managed_process_entrypoint(main))
"""

_TRANSITIVE_WITNESS_ENTRY = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.python_witness import PythonWitness
from scripts.dev_environment.environment import current_managed_process, managed_process_entrypoint
def main():
    source, root = sys.argv[1:]
    root = Path(root)
    scope = current_managed_process()
    assert scope is not None
    (root / 'witness.json').write_text(json.dumps({
        'pid': os.getpid(), 'leases': [
            [fd, os.fstat(fd).st_dev, os.fstat(fd).st_ino] for fd in scope.inherited_fds
        ],
    }))
    PythonWitness(backend_root=root / 'backend', environment=dict(os.environ), mode='lint',
        python_executable=sys.executable, repo_root=root).run()
    return 0
raise SystemExit(managed_process_entrypoint(main))
"""

_TRANSITIVE_LEAF = """
import json, os, signal, sys, time
from pathlib import Path
root = Path.cwd().parent
if sys.argv[1] == 'format':
    (root / 'second-command').write_text('format')
    raise SystemExit(0)
leases = json.loads((root / 'witness.json').read_text())['leases']
def retained():
    try:
        return all((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (device, inode)
            for fd, device, inode in leases)
    except OSError:
        return False
term_calls = 0
def terminate(number, frame):
    global term_calls
    term_calls += 1
    (root / 'leaf-term.json').write_text(
        json.dumps({'retained': retained(), 'signals': term_calls}))
signal.signal(signal.SIGTERM, terminate)
(root / 'leaf.json').write_text(json.dumps({'pid': os.getpid(), 'retained': retained()}))
deadline = time.monotonic() + 20
while not (root / 'release').exists():
    if time.monotonic() >= deadline:
        raise SystemExit(99)
    time.sleep(0.01)
"""


@pytest.mark.parametrize(
    "cut",
    [
        "positive",
        "task-term",
        "task-repeat",
        "task-exhausted",
        "quality-term",
        "witness-term",
        "witness-kill",
    ],
)
def test_native_task_quality_witness_leaf_retains_lease_and_drains_controlled_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut: str
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    root = identity.repo_root
    (root / "backend/ruff.py").write_text(_TRANSITIVE_LEAF)
    plan_path = root / "proofkit/quality-plan.v1.json"
    plan = json.loads(plan_path.read_text())
    plan["portableCommandIds"] = ["python.lint"]
    plan_path.write_text(json.dumps(plan))
    catalog_path = root / "proofkit/witness-plan-input.json"
    catalog = json.loads(catalog_path.read_text())
    for row in catalog["commands"]:
        if row["id"] == "python.lint":
            row["argv"] = [
                sys.executable,
                "-c",
                _TRANSITIVE_WITNESS_ENTRY,
                str(_SOURCE_ROOT),
                str(root),
            ]
    catalog_path.write_text(json.dumps(catalog))
    read_ready, write_ready = os.pipe()
    read_release, write_release = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    with ExitStack() as lifetime:
        outer_root = tmp_path / "outer-lifetime"
        outer_root.mkdir()
        outer_identity = dependency_identity_fixture(outer_root)
        context, _stop_writer, _lease = lifetime.enter_context(
            managed_entry_context(outer_identity)
        )
        value = json.loads(context)
        value["deadline"] = time.monotonic() + 60
        outer = lifetime.enter_context(borrow_managed_process(json.dumps(value)))
        assert current_process_scope() is outer
        # Task owns 30s; Q and W each retain their existing kill1 + return1 reserve.
        stop_grace = 5 if cut == "task-exhausted" else 30
        invocation = lifetime.enter_context(
            managed_process_invocation(
                (
                    "-c",
                    _QUALITY_TASK_PARENT,
                    str(_SOURCE_ROOT),
                    str(root),
                    str(identity.state_home),
                    str(write_ready),
                    str(read_release),
                    "",
                    _TRANSITIVE_QUALITY_ENTRY,
                    "check",
                    "portable",
                ),
                timeout_seconds=60,
                graceful_seconds=stop_grace,
            )
        )
        try:
            process = subprocess.Popen(
                [sys.executable, *invocation.arguments],
                cwd=_SOURCE_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                pass_fds=(*invocation.inherited_fds, write_ready, read_release),
            )
            leaf_path = root / "leaf.json"
            deadline = time.monotonic() + 10
            while not leaf_path.exists():
                assert process.poll() is None, process.communicate(timeout=1)
                assert time.monotonic() < deadline, "actual T/Q/W/leaf did not reach readiness"
                time.sleep(0.01)
            leaf = json.loads(leaf_path.read_text())
            witness = json.loads((root / "witness.json").read_text())
            quality_pid = int((root / "quality.pid").read_text())
            assert leaf["retained"] is True
            assert len({process.pid, quality_pid, witness["pid"], leaf["pid"]}) == 4
            with (
                pytest.raises(EnvironmentError, match=Reason.PREPARATION_IN_PROGRESS),
                dependency_lease(identity, exclusive=True),
            ):
                pytest.fail("live actual T/Q/W/leaf admitted a conflicting installer")
            if cut == "positive":
                (root / "release").touch()
            else:
                target = {
                    "task-term": process.pid,
                    "task-repeat": process.pid,
                    "task-exhausted": process.pid,
                    "quality-term": quality_pid,
                    "witness-term": witness["pid"],
                    "witness-kill": witness["pid"],
                }[cut]
                os.kill(target, signal.SIGKILL if cut == "witness-kill" else signal.SIGTERM)
                if cut == "task-repeat":
                    repeat_by = time.monotonic() + 5
                    while not (root / "leaf-term.json").exists():
                        assert time.monotonic() < repeat_by, "first stop did not reach the leaf"
                        time.sleep(0.01)
                    process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            if cut == "positive":
                assert process.returncode == 0, (stdout, stderr)
                assert (root / "second-command").read_text() == "format"
            else:
                assert process.returncode == (
                    143
                    if cut in {"task-term", "task-repeat", "task-exhausted", "quality-term"}
                    else 1
                ), (
                    stdout,
                    stderr,
                )
                assert not (root / "second-command").exists()
                if cut == "witness-kill":
                    with (
                        pytest.raises(EnvironmentError, match=Reason.PREPARATION_IN_PROGRESS),
                        dependency_lease(identity, exclusive=True),
                    ):
                        pytest.fail("W death released the actual live leaf's lease")
                    (root / "release").touch()
                elif cut != "task-exhausted":
                    assert (root / "leaf-term.json").exists(), (
                        "valid stop cut granted no TERM interval"
                    )
                    assert json.loads((root / "leaf-term.json").read_text()) == {
                        "retained": True,
                        "signals": 1,
                    }
                if cut not in {"witness-kill", "task-exhausted"}:
                    with pytest.raises(ProcessLookupError):
                        os.kill(leaf["pid"], 0)
            deadline = time.monotonic() + 5
            while True:
                try:
                    with dependency_lease(identity, exclusive=True):
                        assert not _native_leaf_executes(leaf["pid"]), (
                            "lease release did not establish physical leaf termination"
                        )
                        break
                except EnvironmentError as error:
                    assert error.reason == Reason.PREPARATION_IN_PROGRESS
                    assert time.monotonic() < deadline, "controlled leaf did not release its lease"
                    time.sleep(0.01)
            assert pending_scopes(identity) == set()
        finally:
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
            for path, field in ((root / "leaf.json", "pid"), (root / "witness.json", "pid")):
                if path.exists():
                    pid = json.loads(path.read_text())[field]
                    with suppress(ProcessLookupError):
                        os.killpg(pid, signal.SIGKILL)
            if (root / "quality.pid").exists():
                with suppress(ProcessLookupError):
                    os.killpg(int((root / "quality.pid").read_text()), signal.SIGKILL)
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=1)
            for descriptor in (read_ready, write_ready, read_release, write_release):
                os.close(descriptor)


def _native_leaf_executes(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if sys.platform == "linux":
        try:
            state = (Path("/proc") / str(pid) / "stat").read_bytes().rsplit(b") ", 1)[1]
        except FileNotFoundError:
            return False
        return state.split()[0] not in {b"Z", b"X", b"x"}
    return True


@pytest.mark.parametrize("operation", ["dev:down", "dev:reset"])
def test_cancel_stage_works_without_venv_and_reports_no_operation(
    tmp_path: Path,
    operation: str,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    status, stdout, stderr = invoke(identity, [operation, "--stop-watch"])
    assert status == 2
    payload = json.loads(stdout)
    assert payload["watchCancellation"] == {
        "state": "quiescent",
        "reason": "no_watch",
        "nonce": None,
        "recovery": None,
    }
    assert payload["operationPerformed"] is False
    assert set(payload) == {"watchCancellation", "operationPerformed"}
    assert json.loads(stderr)["diagnostic"]["reason"] == "dependencies_missing"


def test_cancel_stage_releases_all_its_locks_before_environment_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    calls: list[str] = []

    def cancel(observed: InstanceIdentity) -> CancelResult:
        assert observed == identity
        with dependency_lease(identity, exclusive=True), instance_operation_lock(identity):
            calls.append("cancel")
        return CancelResult("quiescent", "watch_client_stopped", "a" * 32)

    def admit(observed: InstanceIdentity, scope: str) -> None:
        assert observed == identity and scope == "backend"
        with instance_operation_lock(identity):
            calls.append("admit")

    def child(command: str, arguments: Sequence[str], **options: object) -> CommandResult:
        assert command == str(identity.repo_root / "backend/.venv/bin/python")
        assert "--stop-watch" not in arguments
        assert "down" in arguments
        assert options["timeout_seconds"] == 180
        assert options["inherited_fds"]
        with pytest.raises(EnvironmentError), dependency_lease(identity, exclusive=True):
            pytest.fail("the operation did not hold the environment lease")
        with instance_operation_lock(identity):
            calls.append("down")
        return CommandResult(0, '{"state":"down"}\n', "")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "cancel_watch", cancel)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "admit_dependencies", admit)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "spawn", child)
    status, stdout, stderr = invoke(
        identity, ["dev:down", "--format=human", "--stop-watch", "--format", "json"]
    )
    assert status == 0 and stderr == ""
    assert calls == ["cancel", "admit", "down"]
    payload = json.loads(stdout)
    assert payload["state"] == "down" and payload["operationPerformed"] is True
    assert payload["watchCancellation"]["nonce"] == "a" * 32


def test_an_installer_does_not_make_cancel_wait_for_the_environment_lease(tmp_path: Path) -> None:
    identity = dependency_identity_fixture(tmp_path)
    with dependency_lease(identity, exclusive=True):
        status, stdout, stderr = invoke(identity, ["dev:down", "--stop-watch"])
    assert status == 2
    assert json.loads(stdout)["watchCancellation"]["reason"] == "no_watch"
    assert json.loads(stdout)["operationPerformed"] is False
    assert json.loads(stderr)["diagnostic"]["reason"] == "preparation_in_progress"


def test_a_new_watcher_in_the_gap_is_not_cancelled_by_the_old_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    new_session = owned_watch_session(identity)
    calls: list[str] = []

    def cancel(_identity: InstanceIdentity) -> CancelResult:
        calls.append("cancel")
        new_session.__enter__()
        return CancelResult("quiescent", "watch_client_stopped", "a" * 32)

    def child(_command: str, arguments: Sequence[str], **_options: object) -> CommandResult:
        assert "--stop-watch" not in arguments
        with pytest.raises(OperationBusy), instance_operation_lock(identity):
            pytest.fail("the new watch did not exclude down")
        return CommandResult(
            2, "", json.dumps(Diagnostic(Reason.OPERATION_BUSY, "down").envelope())
        )

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "cancel_watch", cancel)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "admit_dependencies", lambda _identity, _scope: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "spawn", child)
    try:
        status, stdout, stderr = invoke(identity, ["dev:down", "--stop-watch"])
        assert status == 2 and calls == ["cancel"]
        assert json.loads(stderr)["diagnostic"]["reason"] == "operation_busy"
        assert json.loads(stdout)["watchCancellation"]["nonce"] == "a" * 32
        assert "operationPerformed" not in json.loads(stdout)
    finally:
        new_session.__exit__(None, None, None)


@pytest.mark.parametrize(
    "arguments",
    [
        ["dev:down", "--stop-watch", "--unknown"],
        ["dev:status", "--stop-watch"],
        ["dev:down", "--format", "secret-invalid", "--stop-watch"],
        ["dev:down", "--stop-watch", "--stop-watch"],
    ],
)
def test_invalid_cancel_arguments_have_no_cancellation_effect_or_raw_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        task, "cancel_watch", lambda _: pytest.fail("invalid arguments cancelled watch")
    )
    status, stdout, stderr = invoke(identity, arguments)
    assert status == 2 and stdout == ""
    assert json.loads(stderr)["diagnostic"]["reason"] == "invalid_argument"
    assert "secret-invalid" not in stderr


@pytest.mark.parametrize(
    "name,scopes",
    [
        ("dev:status", ["backend"]),
        ("dev:logs", ["backend"]),
        ("dev:debug-backend", ["backend"]),
        ("toolchain:check", ["backend"]),
        ("test:api", ["backend"]),
        ("test:api:deep", ["backend"]),
        ("dev:demo", ["frontend"]),
        ("browser:ui", ["frontend"]),
        ("check", ["backend", "frontend"]),
        ("check:portable", ["backend", "frontend"]),
    ],
)
def test_every_supported_environment_user_holds_its_lease_through_child_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    scopes: list[str],
) -> None:
    identity = (
        prepared_quality_identity(tmp_path, monkeypatch)
        if name.startswith("check")
        else dependency_identity_fixture(tmp_path)
    )
    admitted: list[str] = []

    def admit(_identity: InstanceIdentity, scope: str) -> None:
        admitted.append(scope)
        with pytest.raises(EnvironmentError), dependency_lease(identity, exclusive=True):
            pytest.fail("the venv was admitted before acquiring its lease")

    def child(command: str, arguments: Sequence[str], **options: object) -> InteractiveResult:
        with pytest.raises(EnvironmentError), dependency_lease(identity, exclusive=True):
            pytest.fail("child execution released its environment lease")
        descriptors = cast(tuple[int, ...], options["inherited_fds"])
        if managed_process_argument(arguments) is not None:
            outer = current_process_scope()
            assert outer is not None
            assert set(outer.inherited_fds) <= set(descriptors)
            arguments = arguments[:-2]
        else:
            assert len(descriptors) == 1
        os.fstat(descriptors[0])
        assert options["timeout_seconds"] is None
        assert options["graceful_seconds"] == (420 if name == "dev:debug-backend" else 30)
        if name.startswith("check"):
            assert command == str(identity.repo_root / "backend/.venv/bin/python")
            assert tuple(arguments[:-2]) == (
                "-m",
                "scripts.quality_plan",
                *(["portable"] if name.endswith("portable") else []),
            )
            assert arguments[-2] == MANAGED_DEPENDENCY_ARGUMENT
            context = json.loads(arguments[-1])
            assert context["fd"] == descriptors[0]
            assert context["rootDigest"] == identity.root_digest
        if name == "dev:demo":
            assert command == "pnpm" and tuple(arguments) == (
                "--dir",
                "frontend",
                "run",
                "dev:demo",
            )
        if name == "toolchain:check":
            assert tuple(arguments) == (
                "-m",
                "scripts.dev_environment.toolchain",
                "--format",
                "json",
            )
        if name.startswith("test:api"):
            assert tuple(arguments) == (
                "-m",
                "scripts.api_contract_campaign",
                "deep" if name.endswith(":deep") else "fast",
            )
        return InteractiveResult(0, True)

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "admit_dependencies", admit)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "run_interactive", child)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        task,
        "prepare_dependencies",
        lambda *args, **kwargs: pytest.fail("environment user installed dependencies"),
    )
    status, stdout, stderr = invoke(identity, [name])
    assert status == 0 and stdout == stderr == ""
    assert admitted == scopes


def test_doctor_uses_a_dependency_free_process_during_an_exclusive_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    (identity.repo_root / "backend/.venv").mkdir()
    observed: list[tuple[str, tuple[str, ...]]] = []

    def child(command: str, arguments: Sequence[str], **options: object) -> InteractiveResult:
        observed.append((command, tuple(arguments)))
        assert "inherited_fds" not in options
        assert options["timeout_seconds"] == 60
        return InteractiveResult(2, True)

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "run_interactive", child)
    with dependency_lease(identity, exclusive=True):
        status, _, _ = invoke(identity, ["dev:doctor"])
    assert status == 2
    assert observed == [
        (sys.executable, ("-S", "-m", "scripts.dev_environment", "doctor", "--format", "json"))
    ]


def test_doctor_without_a_venv_does_not_create_dependency_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        task, "run_interactive", lambda *_args, **_kwargs: InteractiveResult(2, True)
    )
    status, _, _ = invoke(identity, ["dev:doctor"])
    assert status == 2
    assert not identity.state_home.exists()


@pytest.mark.parametrize(
    "result",
    [
        InteractiveResult(0, True, "residual-descendant"),
        InteractiveResult(0, True, "timeout"),
        InteractiveResult(0, True, "signal"),
        InteractiveResult(0, True, escalated=True),
        InteractiveResult(0, True, started=False),
        InteractiveResult(0, False),
    ],
)
def test_zero_exit_cannot_hide_a_failed_or_forced_process_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: InteractiveResult,
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "admit_dependencies", lambda _identity, _scope: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(task, "run_interactive", lambda *_args, **_kwargs: result)
    status, stdout, stderr = invoke(identity, ["check"])
    assert status == 2 and stdout == ""
    reason = "provider_timeout" if result.failure_kind == "timeout" else "provider_unavailable"
    assert json.loads(stderr)["diagnostic"]["reason"] == reason


def test_missing_venv_cancel_imports_no_installed_adapter_in_a_fresh_interpreter(
    tmp_path: Path,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    program = """
import io, json, sys
from pathlib import Path
from scripts.dev_environment.task import run
out, err = io.StringIO(), io.StringIO()
status = run(['dev:down', '--stop-watch'], repo_root=Path(sys.argv[1]),
             state_home=Path(sys.argv[2]), stdout=out, stderr=err)
imports = [name for name in sys.modules
           if name.startswith(('ci_coordinator', 'cryptography'))]
print(json.dumps({'status':status, 'result':json.loads(out.getvalue()), 'imports':imports}))
"""
    result = subprocess.run(
        [
            getattr(sys, "_base_executable", sys.executable),
            "-S",
            "-c",
            program,
            str(identity.repo_root),
            str(identity.state_home),
        ],
        cwd=_SOURCE_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0 and result.stderr == ""
    value = json.loads(result.stdout)
    assert value["status"] == 2 and value["imports"] == []
    assert value["result"]["operationPerformed"] is False


def test_mise_bootstrap_selects_backend_tools_without_an_implicit_installing_front_door() -> None:
    config = tomllib.loads((_SOURCE_ROOT / "mise.toml").read_text())
    assert config["settings"]["auto_install"] is False
    tasks = config["tasks"]
    assert tasks["install:backend"]["run"] == [
        "mise install --locked python uv",
        "mise exec -- python -S -m scripts.dev_environment.task install:backend",
    ]
    for name in ("dev:prepare", "dev:up", "dev:watch"):
        assert tasks[name]["depends"] == ["install:backend"]
    for name in (
        "dev:status",
        "dev:doctor",
        "dev:logs",
        "dev:open",
        "dev:down",
        "dev:reset",
        "dev:debug-backend",
        "toolchain:check",
        "test:api",
        "test:api:deep",
        "check",
        "check:portable",
    ):
        assert "depends" not in tasks[name]
        assert tasks[name]["run"] == f"python -S -m scripts.dev_environment.task {name}"
    assert tasks["dev:reset"]["confirm"]
    assert tasks["dev:debug-backend"]["raw"] is True


@pytest.mark.parametrize("mode", ["matching", "wrong", "missing", "empty", "crashed"])
def test_full_install_requires_an_executable_exact_scanner_before_dependency_preparation(
    tmp_path: Path, mode: str
) -> None:
    config = tomllib.loads((_SOURCE_ROOT / "mise.toml").read_text())
    steps = config["tasks"]["install"]["run"]
    assert shlex.split(steps[0]) == [
        "mise",
        "install",
        "--locked",
        "python",
        "uv",
        "node",
        "pnpm",
        "gitleaks",
    ]
    admission = shlex.split(steps[1])
    assert admission[:5] == ["mise", "exec", "--", "sh", "-c"]
    assert steps[2] == "mise exec -- python -S -m scripts.dev_environment.task install"
    if mode != "missing":
        tool = tmp_path / "gitleaks"
        output = (
            "0.0.0" if mode == "wrong" else "" if mode == "empty" else config["tools"]["gitleaks"]
        )
        status = 7 if mode == "crashed" else 0
        tool.write_text(
            f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(output)}\nexit {status}\n", encoding="utf-8"
        )
        tool.chmod(0o700)

    result = spawn(
        "/bin/sh",
        admission[4:],
        cwd=tmp_path,
        env={"PATH": str(tmp_path)},
        max_buffer=4096,
        timeout_seconds=5,
    )

    assert result.error is None
    assert (result.status == 0) is (mode == "matching")


def test_bootstrap_copies_exact_task_import_dependencies(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = bootstrap_witness._copy_inputs(_SOURCE_ROOT, project)

    for relative in (
        "scripts/diagram_process.py",
        "scripts/quality_plan.py",
        "scripts/proofkit_common.py",
    ):
        assert relative in manifest
        expected = (_SOURCE_ROOT / relative).read_bytes()
        assert (project / relative).read_bytes() == expected
        assert manifest[relative] == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("provider_failure", [True, False])
def test_bootstrap_preserves_sanitized_primary_failure_when_cleanup_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider_failure: bool,
) -> None:
    configuration = tomllib.loads((_SOURCE_ROOT / "mise.toml").read_text())
    temporary = tempfile.TemporaryDirectory(dir=tmp_path)
    cleanup = temporary.cleanup

    def failing_cleanup() -> None:
        cleanup()
        raise PermissionError(errno.EACCES, "private-cleanup-detail")

    def child(_command: str, arguments: Sequence[str], **_options: object) -> CommandResult:
        if tuple(arguments) == ("--version",):
            return CommandResult(0, configuration["min_version"] + " linux-x64\n", "")
        assert tuple(arguments) == ("run", "install:backend")
        if provider_failure:
            return CommandResult(19, "private-stdout", "private-stderr", error="private-error")
        raise PermissionError(errno.EACCES, "private-filesystem-detail")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(bootstrap_witness, "_copy_inputs", lambda _source, _destination: {})
    monkeypatch.setattr(temporary, "cleanup", failing_cleanup)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda **_: temporary)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(bootstrap_witness, "spawn", child)
    assert bootstrap_witness.main(["--mise", sys.executable]) == 2
    captured = capsys.readouterr()
    expected: dict[str, object] = {
        "schemaVersion": 1,
        "state": "failed",
        "phase": "backend_task",
        "cleanupFailed": True,
    }
    if provider_failure:
        expected.update(
            reason="backend_task_failed", exitCode=19, failureKind=None, providerError=True
        )
    else:
        expected.update(reason="bootstrap_unavailable", errorKind="filesystem", errno=errno.EACCES)
    assert json.loads(captured.out) == expected
    assert "private-" not in captured.out and captured.err == ""


@pytest.mark.parametrize("change", ["frontend_version", "missing_guard"])
def test_bootstrap_frontend_guards_survive_empty_directory_pruning_and_still_fail_closed(
    tmp_path: Path,
    change: str,
) -> None:
    guarded = bootstrap_witness._guard_frontend_tools(tmp_path)
    try:
        for directory in guarded:
            # mise's runtime-symlink rebuild prunes empty tool directories.
            if not any(directory.iterdir()):
                directory.rmdir()
        bootstrap_witness._assert_frontend_absent(tmp_path, tmp_path / "project", guarded)
        first = guarded[0]
        first.chmod(0o700)
        if change == "frontend_version":
            (first / "unexpected-version").mkdir()
            reason = "frontend_tool_used_or_installed"
        else:
            (first / bootstrap_witness._FRONTEND_GUARD).unlink()
            first.rmdir()
            reason = "frontend_install_guard_changed"
        with pytest.raises(bootstrap_witness.BootstrapWitnessError, match=reason):
            bootstrap_witness._assert_frontend_absent(tmp_path, tmp_path / "project", guarded)
    finally:
        for directory in guarded:
            if directory.is_dir():
                directory.chmod(0o700)
