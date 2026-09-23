from __future__ import annotations

from pathlib import Path

import pytest
from scripts.dev_environment.identity import derive_instance_identity


def test_instance_identity_is_stable_and_root_specific(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()

    state_home = tmp_path / "state"
    first = derive_instance_identity(first_root, state_home=state_home)
    repeated = derive_instance_identity(first_root, state_home=state_home)
    second = derive_instance_identity(second_root, state_home=state_home)

    assert first == repeated
    assert first.project_name.startswith("ci-coordinator-")
    assert len(first.project_name) == len("ci-coordinator-") + 12
    assert first.project_name != second.project_name
    assert first.root_digest != second.root_digest
    assert first.state_directory.is_relative_to(state_home)
    assert not first.state_directory.is_relative_to(first.repo_root)


def test_instance_identity_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        derive_instance_identity(tmp_path / "missing", state_home=tmp_path / "state")


def test_instance_identity_rejects_relative_state_home(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="state home"):
        derive_instance_identity(tmp_path, state_home=Path("relative"))


def test_instance_identity_rejects_state_home_inside_worktree(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside every registered worktree"):
        derive_instance_identity(tmp_path, state_home=tmp_path / ".private-state")


def test_instance_identity_rejects_symlink_alias_into_worktree(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-state-alias"
    outside.symlink_to(tmp_path, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="outside every registered worktree"):
            derive_instance_identity(tmp_path, state_home=outside / "state")
    finally:
        outside.unlink()


def test_unmounted_registered_root_is_still_excluded_from_state_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.bounded_process import CommandResult

    root = tmp_path / "mounted-repository"
    root.mkdir()
    (root / ".git").mkdir()
    hidden = tmp_path / "unmounted-worktree"
    output = f"worktree {root}\0HEAD {'a' * 40}\0\0worktree {hidden}\0HEAD {'b' * 40}\0"
    monkeypatch.setattr(
        "scripts.dev_environment.identity.spawn",
        lambda *_args, **_kwargs: CommandResult(0, output, ""),
    )
    identity = derive_instance_identity(root, state_home=tmp_path / "private-state")
    assert identity.repo_root == root
    assert not hidden.exists()
    with pytest.raises(ValueError, match="outside every registered worktree"):
        derive_instance_identity(root, state_home=hidden / "state")
    assert not hidden.exists()


def test_relative_registered_root_is_invalid_provider_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.bounded_process import CommandResult

    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    monkeypatch.setattr(
        "scripts.dev_environment.identity.spawn",
        lambda *_args, **_kwargs: CommandResult(0, "worktree relative-root\0", ""),
    )
    with pytest.raises(ValueError, match="must be absolute"):
        derive_instance_identity(root, state_home=tmp_path / "state")
