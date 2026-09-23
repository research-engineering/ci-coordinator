from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from scripts import module_ownership_candidates as candidate_module
from scripts.bounded_process import CommandResult
from scripts.module_ownership_candidates import (
    RepositoryPathSnapshot,
    candidate_inventory,
    repository_path_snapshot,
)
from scripts.module_ownership_profile import load_profile


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _initialize_git(root: Path) -> str:
    executable = shutil.which("git")
    assert executable is not None
    subprocess.run((executable, "init", "--quiet"), cwd=root, check=True)
    return executable


def test_authoritative_inventory_self_discovers_the_git_worktree(
    tmp_path: Path,
) -> None:
    executable = _initialize_git(tmp_path)
    relative = "backend/src/ci_coordinator/large.py"
    _write(tmp_path, relative, "# line\n" * 401)
    subprocess.run((executable, "add", relative), cwd=tmp_path, check=True)

    inventory = candidate_inventory(load_profile(), tmp_path)

    assert inventory["inventoryScope"] == "git-worktree"
    assert inventory["candidateQueueComplete"] is True
    assert inventory["observedPathCount"] == 1
    assert inventory["dispositionCount"] == 1


def test_git_path_snapshot_includes_only_observed_project_paths(tmp_path: Path) -> None:
    executable = _initialize_git(tmp_path)
    _write(tmp_path, ".gitignore", "*.ignored\n")
    _write(tmp_path, ".git/info/exclude", "local-hidden.py\n")
    _write(tmp_path, "deleted.py", "deleted = True\n")
    _write(tmp_path, "kept.py", "kept = True\n")
    subprocess.run(
        (executable, "add", ".gitignore", "deleted.py", "kept.py"),
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "deleted.py").unlink()
    _write(tmp_path, "local-hidden.py", "repository-owned\n")
    _write(tmp_path, "untracked.py", "untracked = True\n")
    _write(tmp_path, "cache.ignored", "ignored\n")

    snapshot = repository_path_snapshot(tmp_path)

    assert snapshot == RepositoryPathSnapshot(
        deleted_tracked_count=1,
        paths=(".gitignore", "kept.py", "untracked.py"),
        tracked_count=2,
        untracked_count=1,
    )


def test_git_path_snapshot_preserves_a_valid_replacement_character(
    tmp_path: Path,
) -> None:
    executable = _initialize_git(tmp_path)
    relative = "valid-\ufffd.py"
    _write(tmp_path, relative, "value = 1\n")
    subprocess.run((executable, "add", "--", relative), cwd=tmp_path, check=True)

    snapshot = repository_path_snapshot(tmp_path)

    assert snapshot.paths == (relative,)


def test_git_path_snapshot_ignores_ambient_repository_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_root = tmp_path / "expected"
    redirected_root = tmp_path / "redirected"
    expected_root.mkdir()
    redirected_root.mkdir()
    for root, relative in (
        (expected_root, "expected.py"),
        (redirected_root, "redirected.py"),
    ):
        executable = _initialize_git(root)
        _write(root, relative, "value = 1\n")
        subprocess.run((executable, "add", relative), cwd=root, check=True)
    monkeypatch.setenv("GIT_DIR", str(redirected_root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(redirected_root))
    monkeypatch.setenv("GIT_INDEX_FILE", str(redirected_root / ".git" / "index"))
    global_excludes = tmp_path / "global-excludes"
    global_excludes.write_text("expected.py\n", encoding="utf-8")
    global_config = tmp_path / "global-config"
    global_config.write_text(
        f"[core]\n\texcludesFile = {global_excludes}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))

    snapshot = repository_path_snapshot(expected_root)

    assert snapshot.paths == ("expected.py",)


def test_git_path_snapshot_requires_the_exact_repository_toplevel(
    tmp_path: Path,
) -> None:
    _initialize_git(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()

    with pytest.raises(ValueError, match="differs from the Git toplevel"):
        repository_path_snapshot(nested)


def test_git_path_snapshot_rejects_invalid_utf8_path_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_spawn(
        _command: str,
        args: tuple[str, ...],
        **_kwargs: object,
    ) -> CommandResult:
        if args[0] == "rev-parse":
            return CommandResult(
                status=0,
                stdout=f"{tmp_path.resolve()}\n",
                stderr="",
            )
        return CommandResult(
            status=0,
            stdout="invalid-\udcff.py\0",
            stderr="",
        )

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(candidate_module, "spawn", fake_spawn)

    with pytest.raises(ValueError, match="git path is not valid UTF-8"):
        repository_path_snapshot(tmp_path)


def test_git_path_snapshot_has_a_hard_output_bound(tmp_path: Path) -> None:
    executable = _initialize_git(tmp_path)
    _write(tmp_path, "tracked.py", "value = 1\n")
    subprocess.run((executable, "add", "tracked.py"), cwd=tmp_path, check=True)

    with pytest.raises(OSError, match="output exceeded 1 bytes"):
        repository_path_snapshot(tmp_path, maximum_output_bytes=1)
