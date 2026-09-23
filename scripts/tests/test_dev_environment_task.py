from __future__ import annotations

import errno
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from scripts.bounded_process import CommandResult, InteractiveResult, spawn
from scripts.dev_environment import bootstrap_witness, task
from scripts.dev_environment.diagnostics import Diagnostic, Reason
from scripts.dev_environment.environment import EnvironmentError, dependency_lease
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.lifecycle import OperationBusy, instance_operation_lock
from scripts.dev_environment.watch_session import CancelResult, owned_watch_session
from scripts.tests.test_dev_environment_dependencies import dependency_identity_fixture

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
    identity = dependency_identity_fixture(tmp_path)
    admitted: list[str] = []

    def admit(_identity: InstanceIdentity, scope: str) -> None:
        admitted.append(scope)
        with pytest.raises(EnvironmentError), dependency_lease(identity, exclusive=True):
            pytest.fail("the venv was admitted before acquiring its lease")

    def child(command: str, arguments: Sequence[str], **options: object) -> InteractiveResult:
        with pytest.raises(EnvironmentError), dependency_lease(identity, exclusive=True):
            pytest.fail("child execution released its environment lease")
        descriptors = cast(tuple[int, ...], options["inherited_fds"])
        assert len(descriptors) == 1
        os.fstat(descriptors[0])
        assert options["timeout_seconds"] is None
        assert options["graceful_seconds"] == (420 if name == "dev:debug-backend" else 30)
        if name.startswith("check"):
            assert command == str(identity.repo_root / "backend/.venv/bin/python")
            assert tuple(arguments) == (
                "-m",
                "scripts.quality_plan",
                *(["portable"] if name.endswith("portable") else []),
            )
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
    identity = dependency_identity_fixture(tmp_path)
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
