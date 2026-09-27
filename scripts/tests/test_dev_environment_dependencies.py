from __future__ import annotations

import json
import os
import select
import signal
import struct
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import cast

import pytest
from scripts.bounded_process import CommandResult, current_process_scope, spawn
from scripts.dev_environment import environment
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.environment import (
    DependencyScope,
    EnvironmentError,
    ManagedDependencyBorrow,
    admit_dependencies,
    borrow_managed_dependencies,
    dependency_lease,
    managed_dependency_context,
    prepare_dependencies,
)
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.diagram_process import DiagramCancellation


def dependency_identity_fixture(tmp_path: Path) -> InstanceIdentity:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "backend").mkdir()
    (root / "frontend").mkdir()
    (root / "mise.toml").write_text(
        '[tools]\npython = "'
        + ".".join(map(str, sys.version_info[:3]))
        + '"\nuv = "1.2.3"\nnode = "24.1.2"\npnpm = "11.2.3"\n'
    )
    for relative in (
        "backend/pyproject.toml",
        "backend/uv.lock",
        "backend/requirements-dev.lock",
        "frontend/package.json",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        ".npmrc",
    ):
        (root / relative).write_text("fixture\n")
    return derive_instance_identity(root, state_home=tmp_path / "state")


def create_legacy_venv(identity: InstanceIdentity) -> None:
    venv = identity.repo_root / "backend/.venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    executable = venv / "bin/python"
    if not executable.exists():
        executable.symlink_to(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    (venv / "pyvenv.cfg").write_text(
        "implementation = CPython\nversion_info = "
        + ".".join(map(str, sys.version_info[:3]))
        + "\n"
    )


class FrozenInstaller:
    def __init__(self, identity: InstanceIdentity) -> None:
        self.identity = identity
        self.calls: list[tuple[str, tuple[str, ...], dict[str, object]]] = []
        self.fail_sync = False
        self.change_lock = False

    def __call__(self, command: str, args: Sequence[str], **options: object) -> CommandResult:
        self.calls.append((command, tuple(args), options))
        assert options["cwd"] == self.identity.repo_root
        descriptors = cast(tuple[int, ...], options["inherited_fds"])
        assert len(descriptors) == 1
        os.fstat(descriptors[0])
        with pytest.raises(EnvironmentError), dependency_lease(self.identity):
            pytest.fail("installer did not retain the exclusive lease")
        if args == ("--version",):
            versions = {"uv": "uv 1.2.3", "node": "v24.1.2", "pnpm": "11.2.3"}
            return CommandResult(0, versions[command] + "\n", "")
        if self.fail_sync:
            return CommandResult(1, "", "private provider detail")
        if command == "uv":
            assert args[:6] == (
                "sync",
                "--project",
                "backend",
                "--frozen",
                "--all-groups",
                "--python",
            )
            assert args[-1] == "--no-python-downloads"
            create_legacy_venv(self.identity)
            if self.change_lock:
                (self.identity.repo_root / "backend/uv.lock").write_text("changed\n")
        else:
            assert command == "pnpm" and args[:2] == ("install", "--frozen-lockfile")
            for relative in ("node_modules", "frontend/node_modules"):
                (self.identity.repo_root / relative).mkdir(exist_ok=True)
        return CommandResult(0, "", "")


def prepared_quality_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstanceIdentity:
    identity = dependency_identity_fixture(tmp_path)
    monkeypatch.setattr(environment, "spawn", FrozenInstaller(identity))
    prepare_dependencies(identity, ("backend", "frontend"))
    source = Path(__file__).resolve().parents[2] / "proofkit"
    target = identity.repo_root / "proofkit"
    target.mkdir()
    (target / "witness-plan-input.json").write_bytes(
        (source / "witness-plan-input.json").read_bytes()
    )
    plan = json.loads((source / "quality-plan.v1.json").read_text())
    plan["localCommandIds"] = [
        "python.lock-check",
        "python.install-check",
        "frontend.install",
        "python.lint",
    ]
    plan["portableCommandIds"] = ["python.test"]
    plan["branchHeadAdditionalCommandIds"] = []
    (target / "quality-plan.v1.json").write_text(json.dumps(plan))
    return identity


@contextmanager
def quality_dependency_borrow(
    identity: InstanceIdentity,
    *,
    write_scopes: tuple[DependencyScope, ...] = ("backend", "frontend"),
) -> Iterator[ManagedDependencyBorrow]:
    with dependency_lease(identity, exclusive=bool(write_scopes)) as descriptor:
        child_descriptor = os.dup(descriptor)
        context = managed_dependency_context(identity, child_descriptor, "a" * 64)
        with borrow_managed_dependencies(
            identity.repo_root,
            context,
            selection_sha256="a" * 64,
            write_scopes=write_scopes,
            environment={"CI_COORDINATOR_DEV_STATE_HOME": str(identity.state_home)},
        ) as borrow:
            yield borrow


def pending_scopes(identity: InstanceIdentity) -> set[str]:
    return {
        scope
        for scope in ("backend", "frontend")
        if (
            identity.state_home / "dependency-preparation" / f"{identity.project_name}-{scope}.json"
        ).exists()
    }


def test_backend_preparation_has_no_frontend_tool_or_artifact_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    for relative in (
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "frontend/package.json",
    ):
        (identity.repo_root / relative).unlink()
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    prepare_dependencies(identity, ("backend",))
    admit_dependencies(identity, "backend")
    assert [command for command, _, _ in provider.calls] == ["uv", "uv"]
    assert not (identity.repo_root / "node_modules").exists()
    assert not (identity.repo_root / "frontend/node_modules").exists()


def test_full_install_then_backend_preparation_preserves_the_full_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    prepare_dependencies(identity, ("backend", "frontend"))
    retained = identity.repo_root / "backend/.venv/retained-development-tool"
    retained.write_text("keep")
    frontend_marker = identity.repo_root / "node_modules/.ci-coordinator-dependencies/identity.json"
    previous = frontend_marker.read_bytes()
    provider.calls.clear()
    prepare_dependencies(identity, ("backend",))
    admit_dependencies(identity, "frontend")
    assert retained.read_text() == "keep"
    assert frontend_marker.read_bytes() == previous
    assert all(command == "uv" for command, _, _ in provider.calls)


@pytest.mark.parametrize("field", ["rootDigest", "platform", "architecture"])
def test_foreign_marker_is_rejected_before_any_environment_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    prepare_dependencies(identity, ("backend", "frontend"))
    marker = identity.repo_root / "node_modules/.ci-coordinator-dependencies/identity.json"
    value = json.loads(marker.read_text())
    value[field] = "foreign"
    marker.write_text(json.dumps(value))
    before = marker.read_bytes()
    provider.calls.clear()
    with pytest.raises(EnvironmentError, match=Reason.FOREIGN_STATE):
        prepare_dependencies(identity, ("backend", "frontend"))
    assert provider.calls == []
    assert marker.read_bytes() == before


@pytest.mark.parametrize("relative", ["backend", "backend/.venv", "frontend/node_modules"])
def test_parent_and_destination_symlinks_never_admit_a_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    target = tmp_path / "foreign"
    path = identity.repo_root / relative
    if path.exists():
        path.rename(target)
    else:
        target.mkdir()
    retained = target / "preserve"
    retained.write_text("foreign data")
    path.symlink_to(target, target_is_directory=True)
    with pytest.raises(EnvironmentError, match=Reason.INVALID_STATE):
        prepare_dependencies(identity, ("backend", "frontend"))
    assert provider.calls == []
    assert retained.read_text() == "foreign data"


def test_legacy_python_must_resolve_to_the_current_native_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    create_legacy_venv(identity)
    executable = identity.repo_root / "backend/.venv/bin/python"
    executable.unlink()
    foreign = tmp_path / "foreign-python"
    foreign.write_text("foreign executable")
    foreign.chmod(0o700)
    executable.symlink_to(foreign)
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    with pytest.raises(EnvironmentError, match=Reason.FOREIGN_STATE):
        prepare_dependencies(identity, ("backend",))
    assert provider.calls == []
    assert foreign.read_text() == "foreign executable"


def test_legacy_editable_from_another_worktree_is_not_reassigned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    create_legacy_venv(identity)
    metadata = (
        identity.repo_root
        / "backend/.venv/lib/python3.14/site-packages"
        / "ci_coordinator_backend-0.1.0.dist-info/direct_url.json"
    )
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"url": (tmp_path / "other/backend").as_uri()}))
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    with pytest.raises(EnvironmentError, match=Reason.FOREIGN_STATE):
        prepare_dependencies(identity, ("backend",))
    assert provider.calls == []


@pytest.mark.parametrize("failure", ["sync_failure", "lock_changed"])
def test_interrupted_preparation_never_admits_success_and_explicit_retry_repairs_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    prepare_dependencies(identity, ("backend",))
    provider.fail_sync = failure == "sync_failure"
    provider.change_lock = failure == "lock_changed"
    with pytest.raises(EnvironmentError):
        prepare_dependencies(identity, ("backend",))
    with pytest.raises(EnvironmentError, match=Reason.ENVIRONMENT_STALE):
        admit_dependencies(identity, "backend")
    provider.fail_sync = provider.change_lock = False
    prepare_dependencies(identity, ("backend",))
    admit_dependencies(identity, "backend")


def test_unmarked_compatible_legacy_environment_requires_explicit_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    create_legacy_venv(identity)
    with pytest.raises(EnvironmentError, match=Reason.DEPENDENCIES_MISSING):
        admit_dependencies(identity, "backend")
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", FrozenInstaller(identity))
    prepare_dependencies(identity, ("backend",))
    admit_dependencies(identity, "backend")


def test_an_ambient_project_selector_cannot_redirect_the_backend_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    prepare_dependencies(
        identity,
        ("backend",),
        environment={
            "UV_PROJECT_ENVIRONMENT": "/foreign",
            "PYTHONPATH": "/foreign",
            "VIRTUAL_ENV": "/foreign",
        },
    )
    options = provider.calls[-1][2]
    observed = cast(dict[str, str], options["env"])
    assert observed["UV_PROJECT_ENVIRONMENT"] == str(identity.repo_root / "backend/.venv")
    assert "PYTHONPATH" not in observed and "VIRTUAL_ENV" not in observed


@pytest.mark.parametrize("scope", ["backend", "frontend"])
def test_reader_blocks_install_without_preventing_another_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: DependencyScope,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    provider = FrozenInstaller(identity)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(environment, "spawn", provider)
    with dependency_lease(identity):
        with pytest.raises(EnvironmentError, match=Reason.PREPARATION_IN_PROGRESS):
            prepare_dependencies(identity, (scope,))
        with dependency_lease(identity):
            pass
    assert provider.calls == []


def test_child_keeps_the_environment_lease_after_the_parent_descriptor_closes(
    tmp_path: Path,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    child: subprocess.Popen[bytes] | None = None
    try:
        with dependency_lease(identity) as descriptor:
            outer = current_process_scope()
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    "import os, select; select.select([0], [], [], 10); os.read(0, 1)",
                ],
                stdin=subprocess.PIPE,
                pass_fds=(descriptor, *(() if outer is None else outer.inherited_fds)),
            )
        with pytest.raises(EnvironmentError), dependency_lease(identity, exclusive=True):
            pytest.fail("the inheriting child lost its environment lease")
    finally:
        if child is not None:
            child.communicate(b"x", timeout=5)
    with dependency_lease(identity, exclusive=True):
        pass


def test_unrelated_worktrees_do_not_share_a_dependency_lease(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    first_root.mkdir()
    second_root = tmp_path / "second"
    second_root.mkdir()
    first = derive_instance_identity(first_root, state_home=tmp_path / "state")
    second = derive_instance_identity(second_root, state_home=tmp_path / "state")
    with dependency_lease(first, exclusive=True), dependency_lease(second):
        pass


def test_managed_phases_conserve_identity_and_never_reacquire_or_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    markers = [
        identity.repo_root / destination / ".ci-coordinator-dependencies/identity.json"
        for destination in ("backend/.venv", "node_modules")
    ]
    before = [path.read_bytes() for path in markers]
    with quality_dependency_borrow(identity) as borrow:
        assert pending_scopes(identity) == set()
        for scope in ("backend", "frontend"):
            with (
                pytest.raises(EnvironmentError, match=Reason.PREPARATION_IN_PROGRESS),
                dependency_lease(identity),
            ):
                pytest.fail("managed writer permitted a reader")
            borrow.begin(scope)
            assert pending_scopes(identity) == {scope}
            with pytest.raises(EnvironmentError, match=Reason.ENVIRONMENT_STALE):
                admit_dependencies(identity, scope)
            borrow.complete(scope)
            admit_dependencies(identity, scope)
            assert pending_scopes(identity) == set()
        with (
            pytest.raises(EnvironmentError, match=Reason.PREPARATION_IN_PROGRESS),
            dependency_lease(identity),
        ):
            pytest.fail("borrower unlocked the parent's open-file description")
    assert [path.read_bytes() for path in markers] == before
    with pytest.raises(EnvironmentError, match=Reason.INVALID_STATE):
        borrow.begin("backend")
    with dependency_lease(identity):
        pass


@pytest.mark.parametrize(
    "fault",
    [
        "version",
        "version_bool",
        "missing",
        "extra",
        "duplicate",
        "oversized",
        "json",
        "fd_bool",
        "fd_negative",
        "fd_float",
        "fd_overflow",
        "fd_stdio",
        "fd_closed",
        "fd_foreign",
        "root",
        "selection",
        "state_home",
    ],
)
def test_managed_borrow_rejects_each_invalid_operand_before_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    with quality_dependency_borrow(identity) as positive:
        positive.begin("backend")
        positive.complete("backend")
    with dependency_lease(identity, exclusive=True) as descriptor:
        child_descriptor = os.dup(descriptor)
        foreign_descriptor: int | None = None
        try:
            payload = json.loads(managed_dependency_context(identity, child_descriptor, "a" * 64))
            source = {"CI_COORDINATOR_DEV_STATE_HOME": str(identity.state_home)}
            if fault == "version":
                payload["version"] = 2
            elif fault == "version_bool":
                payload["version"] = True
            elif fault == "missing":
                del payload["fd"]
            elif fault == "extra":
                payload["extra"] = 1
            elif fault == "fd_bool":
                payload["fd"] = True
            elif fault == "fd_negative":
                payload["fd"] = -1
            elif fault == "fd_float":
                payload["fd"] = float(child_descriptor)
            elif fault == "fd_overflow":
                payload["fd"] = 2**128
            elif fault == "fd_stdio":
                payload["fd"] = 1
            elif fault == "fd_closed":
                os.close(child_descriptor)
            elif fault == "fd_foreign":
                foreign_descriptor = os.open(tmp_path / "unlocked", os.O_CREAT | os.O_RDWR, 0o600)
                payload["fd"] = foreign_descriptor
            elif fault == "root":
                payload["rootDigest"] = "b" * 64
            elif fault == "selection":
                payload["selectionSha256"] = "b" * 64
            elif fault == "state_home":
                source = {}
            context = json.dumps(payload)
            if fault == "duplicate":
                context = context[:-1] + ',"version":1}'
            elif fault == "oversized":
                context += " " * 513
            elif fault == "json":
                context = "not-json"
            with (
                pytest.raises(EnvironmentError, match=Reason.INVALID_STATE),
                borrow_managed_dependencies(
                    identity.repo_root,
                    context,
                    selection_sha256="a" * 64,
                    write_scopes=("backend", "frontend"),
                    environment=source,
                ),
            ):
                pytest.fail("invalid borrow was admitted")
            assert pending_scopes(identity) == set()
            admit_dependencies(identity, "backend")
            admit_dependencies(identity, "frontend")
        finally:
            with suppress(OSError):
                os.close(child_descriptor)
            if foreign_descriptor is not None:
                os.close(foreign_descriptor)


@pytest.mark.parametrize("before_begin", [True, False])
@pytest.mark.parametrize(
    "scope,relative",
    [
        ("backend", "backend/uv.lock"),
        ("backend", "backend/requirements-dev.lock"),
        ("frontend", "pnpm-lock.yaml"),
    ],
)
def test_managed_phase_conserves_each_input_and_the_checked_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before_begin: bool,
    scope: DependencyScope,
    relative: str,
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    with quality_dependency_borrow(identity) as borrow:
        if not before_begin:
            borrow.begin(scope)
        path = identity.repo_root / relative
        path.write_bytes(path.read_bytes() + b"changed\n")
        with pytest.raises(EnvironmentError, match=Reason.ENVIRONMENT_STALE):
            if before_begin:
                borrow.begin(scope)
            else:
                borrow.complete(scope)
        assert pending_scopes(identity) == (set() if before_begin else {scope})


@pytest.mark.parametrize("record", ["pending", "marker"])
@pytest.mark.parametrize(
    "field,reason", [("rootDigest", Reason.FOREIGN_STATE), ("inputs", Reason.ENVIRONMENT_STALE)]
)
def test_managed_completion_does_not_adopt_or_delete_changed_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: str,
    field: str,
    reason: Reason,
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    pending = (
        identity.state_home / "dependency-preparation" / f"{identity.project_name}-backend.json"
    )
    marker = identity.repo_root / "backend/.venv/.ci-coordinator-dependencies/identity.json"
    with quality_dependency_borrow(identity) as borrow:
        borrow.begin("backend")
        path = pending if record == "pending" else marker
        value = json.loads(path.read_text())
        value[field] = "b" * 64 if field == "rootDigest" else {}
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        with pytest.raises(EnvironmentError, match=reason):
            borrow.complete("backend")
        assert path.read_bytes() == before
        assert pending_scopes(identity) == {"backend"}


def test_managed_completion_republishes_a_removed_marker_but_not_a_foreign_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    marker = identity.repo_root / "backend/.venv/.ci-coordinator-dependencies/identity.json"
    before = marker.read_bytes()
    with quality_dependency_borrow(identity) as borrow:
        borrow.begin("backend")
        marker.unlink()
        borrow.complete("backend")
        assert marker.read_bytes() == before
        borrow.begin("frontend")
        destination = identity.repo_root / "frontend/node_modules"
        destination.rmdir()
        destination.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(EnvironmentError, match=Reason.INVALID_STATE):
            borrow.complete("frontend")
        assert pending_scopes(identity) == {"frontend"}


def test_portable_borrow_cannot_mint_an_install_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    with quality_dependency_borrow(identity, write_scopes=()) as borrow:
        with dependency_lease(identity):
            pass
        with pytest.raises(EnvironmentError, match=Reason.INVALID_STATE):
            borrow.begin("backend")
        assert pending_scopes(identity) == set()


@contextmanager
def managed_entry_context(identity: InstanceIdentity) -> Iterator[tuple[str, int, int]]:
    with dependency_lease(identity) as descriptor:
        inherited = os.dup(descriptor)
        read_stop, write_stop = os.pipe()
        os.set_blocking(read_stop, False)
        owned = {
            item: (os.fstat(item).st_dev, os.fstat(item).st_ino)
            for item in (inherited, read_stop, write_stop)
        }
        try:
            yield (
                json.dumps(
                    {
                        "version": 1,
                        "deadline": time.monotonic() + 30,
                        "stopFd": read_stop,
                        "stopGrace": 3,
                        "leases": [
                            {
                                "fd": inherited,
                                "root": str(identity.repo_root),
                                "lock": str(
                                    identity.state_home
                                    / "dependency-locks"
                                    / f"{identity.project_name}.lock"
                                ),
                                "selectionSha256": "a" * 64,
                            }
                        ],
                    }
                ),
                write_stop,
                inherited,
            )
        finally:
            for item, expected_inode in owned.items():
                with suppress(OSError):
                    if (os.fstat(item).st_dev, os.fstat(item).st_ino) == expected_inode:
                        os.close(item)


@pytest.mark.parametrize("status", [0, 7])
def test_managed_entry_preserves_return_and_restores_its_terminal_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    before = current_process_scope()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    with managed_entry_context(identity) as (context, _stop, inherited):
        arguments = ["owner", "public", environment.MANAGED_PROCESS_ARGUMENT, context]
        monkeypatch.setattr(sys, "argv", arguments)

        def main() -> int:
            assert sys.argv == ["owner", "public"]
            current = environment.current_managed_process()
            expected = (*(() if before is None else before.inherited_fds), inherited)
            assert current is not None and current.inherited_fds == expected
            return status

        assert environment.managed_process_entrypoint(main) == status
        assert sys.argv is arguments
        assert current_process_scope() is before
        assert {number: signal.getsignal(number) for number in handlers} == handlers
        with pytest.raises(OSError):
            os.fstat(inherited)


@pytest.mark.parametrize(
    "error",
    [
        ValueError("original"),
        RuntimeError("original"),
        OSError("original"),
        SystemExit(19),
        KeyboardInterrupt(),
    ],
)
def test_managed_entry_does_not_translate_a_primary_main_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    before = current_process_scope()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    with managed_entry_context(identity) as (context, _stop, inherited):
        arguments = ["owner", environment.MANAGED_PROCESS_ARGUMENT, context]
        monkeypatch.setattr(sys, "argv", arguments)

        def main() -> int:
            raise error

        with pytest.raises(type(error)) as caught:
            environment.managed_process_entrypoint(main)
        assert caught.value is error
        assert sys.argv is arguments
        assert current_process_scope() is before
        assert {number: signal.getsignal(number) for number in handlers} == handlers
        with pytest.raises(OSError):
            os.fstat(inherited)


@pytest.mark.parametrize(
    "stop,expected", [("pipe", 1), ("deadline", 1), ("int", 130), ("term", 143)]
)
def test_managed_entry_cannot_report_success_after_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: str, expected: int
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    with managed_entry_context(identity) as (context, stop_fd, _inherited):
        monkeypatch.setattr(sys, "argv", ["owner", environment.MANAGED_PROCESS_ARGUMENT, context])

        def main() -> int:
            current = environment.current_managed_process()
            assert current is not None
            if stop == "pipe":
                os.write(stop_fd, b"S")
            elif stop == "deadline":
                expired = json.loads(context)["deadline"] + 1
                monkeypatch.setattr(time, "monotonic", lambda: expired)
            else:
                number = signal.SIGINT if stop == "int" else signal.SIGTERM
                current._cancellation.receive(number, None)
                current._cancellation.receive(signal.SIGTERM, None)
            return 0

        assert environment.managed_process_entrypoint(main) == expected


@pytest.mark.parametrize("context", ["{}", "not-json", "x" * 8193])
def test_managed_entry_rejects_present_invalid_context_before_main(
    monkeypatch: pytest.MonkeyPatch, context: str
) -> None:
    before = current_process_scope()
    arguments = ["owner", environment.MANAGED_PROCESS_ARGUMENT, context]
    monkeypatch.setattr(sys, "argv", arguments)
    assert (
        environment.managed_process_entrypoint(lambda: pytest.fail("invalid context ran main")) == 2
    )
    assert sys.argv is arguments
    assert current_process_scope() is before


def test_unmanaged_entry_keeps_original_argv_return_and_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = ["owner", "public"]
    monkeypatch.setattr(sys, "argv", arguments)
    assert environment.managed_process_entrypoint(lambda: 7) == 7
    error = ValueError("unmanaged primary")

    def main() -> int:
        raise error

    with pytest.raises(ValueError) as caught:
        environment.managed_process_entrypoint(main)
    assert caught.value is error
    assert sys.argv is arguments


_MANAGED_ENTRY_NATIVE = """
import json, os, signal, sys
from pathlib import Path
from scripts.dev_environment.environment import current_managed_process, managed_process_entrypoint
def main():
    mode, marker = sys.argv[1:]
    scope = current_managed_process()
    assert scope is not None
    for descriptor in scope.inherited_fds:
        os.fstat(descriptor)
    Path(marker).write_text(json.dumps({'leases': len(scope.inherited_fds)}))
    if mode == 'raise':
        raise ValueError('owned primary failure')
    if mode in ('int', 'term', 'exit-zero-stop'):
        os.kill(os.getpid(), signal.SIGINT if mode == 'int' else signal.SIGTERM)
    if mode == 'exit-zero-stop':
        raise SystemExit(0)
    return 7 if mode == 'nonzero' else 0
raise SystemExit(managed_process_entrypoint(main))
"""


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("positive", 0),
        ("nonzero", 7),
        ("raise", 1),
        ("int", 130),
        ("term", 143),
        ("exit-zero-stop", 143),
        ("invalid", 2),
    ],
)
def test_native_managed_entry_preserves_exec_outcomes(
    tmp_path: Path, mode: str, expected: int
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    marker = tmp_path / "entry.json"
    with managed_entry_context(identity) as (context, _stop, inherited):
        stop_read = json.loads(context)["stopFd"]
        outer = current_process_scope()
        descriptors = tuple(
            dict.fromkeys((inherited, stop_read, *(() if outer is None else outer.inherited_fds)))
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _MANAGED_ENTRY_NATIVE,
                mode,
                str(marker),
                environment.MANAGED_PROCESS_ARGUMENT,
                "{}" if mode == "invalid" else context,
            ],
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=descriptors,
        )
        try:
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == expected, stderr.decode()
            assert stdout == b""
            if mode == "invalid":
                assert not marker.exists()
                assert stderr == b"managed process admission failed\n"
            else:
                assert json.loads(marker.read_text()) == {"leases": 1}
                if mode == "raise":
                    assert b"ValueError: owned primary failure" in stderr
                else:
                    assert stderr == b""
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=1)


def test_native_process_carrier_has_no_eight_lease_or_8192_byte_cut(
    tmp_path: Path,
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    marker = tmp_path / "entry.json"
    duplicates: list[int] = []
    ancestor = current_process_scope()
    ancestor_fds = () if ancestor is None else ancestor.inherited_fds
    with managed_entry_context(identity) as (context, _stop, inherited):
        document = json.loads(context)
        try:
            while len(document["leases"]) <= 8 or len(json.dumps(document).encode()) <= 8192:
                descriptor = os.dup(inherited)
                duplicates.append(descriptor)
                document["leases"].append({**document["leases"][0], "fd": descriptor})
            payload = json.dumps(document)
            assert len(payload.encode()) < os.sysconf("SC_ARG_MAX")
            with (
                environment.borrow_managed_process(payload),
                environment.managed_process_invocation(
                    ("-c", _MANAGED_ENTRY_NATIVE, "positive", str(marker)),
                    timeout_seconds=10,
                    graceful_seconds=3,
                ) as invocation,
            ):
                result = spawn(
                    sys.executable,
                    invocation.arguments,
                    cwd=Path(__file__).resolve().parents[2],
                    max_buffer=4096,
                    timeout_seconds=10,
                    graceful_seconds=3,
                    inherited_fds=invocation.inherited_fds,
                    cancellation_fd=invocation.cancellation_fd,
                )
            assert result.status == 0 and result.error is None, result.stderr
            assert json.loads(marker.read_text()) == {
                "leases": len(ancestor_fds) + len(document["leases"])
            }
        finally:
            for descriptor in duplicates:
                with suppress(OSError):
                    os.close(descriptor)


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("already_loaded", [False, True])
def test_private_pytest_loader_preserves_public_arguments_and_unmanaged_invocation(
    tmp_path: Path, managed: bool, already_loaded: bool
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    loader = ("-p", "scripts.python_coverage_diagnostics")
    public = ("-m", "pytest", *(loader if already_loaded else ()), "-q", "tests", "-m", "unit")
    with ExitStack() as lifetime:
        if managed:
            context, _stop, _descriptor = lifetime.enter_context(managed_entry_context(identity))
            lifetime.enter_context(environment.borrow_managed_process(context))
        elif current_process_scope() is not None:
            # This case owns an unmanaged API call inside a managed test session.
            lifetime.enter_context(pytest.MonkeyPatch.context()).setattr(
                environment, "current_managed_process", lambda: None
            )
        with environment.managed_process_invocation(
            public, timeout_seconds=10, graceful_seconds=3, pytest_participant=True
        ) as invocation:
            expected = (*public, *(loader if managed and not already_loaded else ()))
            assert invocation.arguments[: len(expected)] == expected
            if managed:
                assert invocation.arguments[-2] == "--managed-process-context"
                assert len(invocation.arguments) == len(expected) + 2
                assert invocation.inherited_fds and invocation.cancellation_fd is not None
            else:
                assert invocation.arguments == public
                assert invocation.inherited_fds == () and invocation.cancellation_fd is None


@pytest.mark.parametrize(
    "message", ["valid", "expired", "partial", "tag", "nan", "future", "eof", "duplicate"]
)
def test_stop_frame_never_creates_authority_from_incomplete_or_repeated_input(
    tmp_path: Path, message: str
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    with (
        managed_entry_context(identity) as (context, writer, _inherited),
        environment.borrow_managed_process(context) as borrow,
    ):
        assert not borrow.stop_requested()
        cut = time.monotonic() - 1 if message == "expired" else time.monotonic() + 2
        frame = struct.pack("!4sd", b"CF1:", cut)
        if message == "eof":
            os.close(writer)
        else:
            payload = {
                "partial": frame[:5],
                "tag": struct.pack("!4sd", b"BAD:", cut),
                "nan": struct.pack("!4sd", b"CF1:", float("nan")),
                "future": struct.pack("!4sd", b"CF1:", time.monotonic() + 60),
                "duplicate": frame + frame,
            }.get(message, frame)
            os.write(writer, payload)
        assert borrow.stop_requested()
        assert borrow.cancellation_deadline == (cut if message in {"valid", "expired"} else 0)
        if message == "valid":
            borrow._cancellation.receive(signal.SIGTERM, None)
            first_signal = borrow._cancellation.received_at
            borrow._cancellation.receive(signal.SIGINT, None)
            assert borrow._cancellation.received_at == first_signal
            assert borrow.cancellation_deadline == cut
            os.write(writer, struct.pack("!4sd", b"CF1:", cut + 100))
            assert borrow.cancellation_deadline == 0
            assert borrow._wire_first_force == cut


def test_nested_borrow_shares_memoized_stop_but_exec_uses_a_fresh_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    with (
        managed_entry_context(identity) as (context, writer, _inherited),
        environment.borrow_managed_process(context) as parent,
        quality_dependency_borrow(identity, write_scopes=()) as dependency,
        environment.managed_dependency_process_scope(
            dependency, "a" * 64, DiagramCancellation()
        ) as child,
        environment.managed_process_invocation(
            ("-m", "owned_fixture"), timeout_seconds=10, graceful_seconds=3
        ) as invocation,
    ):
        old_stop = json.loads(context)["stopFd"]
        new_stop = json.loads(invocation.arguments[-1])["stopFd"]
        assert old_stop != new_stop and old_stop not in invocation.inherited_fds
        cut = time.monotonic() + 2
        os.write(writer, struct.pack("!4sd", b"CF1:", cut))
        assert parent.cancellation_deadline == cut
        assert child.cancellation_deadline == cut
        assert parent.stop_requested() and child.stop_requested()
        assert child._parent is parent
        assert not select.select([new_stop], [], [], 0)[0]


def test_interruption_after_consuming_stop_bytes_cannot_reconstruct_a_later_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    with (
        managed_entry_context(identity) as (context, writer, _inherited),
        environment.borrow_managed_process(context) as borrow,
    ):
        descriptor = json.loads(context)["stopFd"]
        os.write(writer, struct.pack("!4sd", b"CF1:", time.monotonic() + 2))
        original_read = os.read
        failure = KeyboardInterrupt("after frame consumption")

        def interrupt_read(fd: int, count: int) -> bytes:
            result = original_read(fd, count)
            if fd == descriptor:
                raise failure
            return result

        with monkeypatch.context() as patcher:
            patcher.setattr(os, "read", interrupt_read)
            with pytest.raises(KeyboardInterrupt) as caught:
                borrow.stop_requested()
        assert caught.value is failure
        assert borrow.stop_requested()
        assert borrow.cancellation_deadline == 0
