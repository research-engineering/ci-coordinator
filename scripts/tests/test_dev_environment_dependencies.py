from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from scripts.bounded_process import CommandResult
from scripts.dev_environment import environment
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.environment import (
    DependencyScope,
    EnvironmentError,
    admit_dependencies,
    dependency_lease,
    prepare_dependencies,
)
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity


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
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    "import os, select; select.select([0], [], [], 10); os.read(0, 1)",
                ],
                stdin=subprocess.PIPE,
                pass_fds=(descriptor,),
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
