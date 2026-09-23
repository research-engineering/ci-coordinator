from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from scripts import bounded_git
from scripts.bounded_git import (
    BoundedGitCommandError,
    BoundedGitError,
    capture_git_text,
    run_git,
)
from scripts.bounded_process import CommandResult


def test_git_execution_ignores_ambient_repository_redirection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _repository(tmp_path / "first", "first")
    second = _repository(tmp_path / "second", "second")
    expected = run_git(first, ("rev-parse", "HEAD")).stdout.strip()
    unexpected = run_git(second, ("rev-parse", "HEAD")).stdout.strip()
    assert expected != unexpected
    monkeypatch.setenv("GIT_DIR", str(second / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(second))

    assert capture_git_text(first, ("rev-parse", "HEAD"), strip=True) == expected


def test_git_capture_rejects_output_beyond_the_declared_bound(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", "x" * 4_096)

    with pytest.raises(BoundedGitError, match="output exceeded 128 bytes"):
        run_git(repository, ("show", "HEAD:tracked.txt"), max_buffer=128)


def test_git_nonzero_status_is_distinct_from_execution_bound_failure(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repository", "content")

    with pytest.raises(BoundedGitCommandError, match="unknown-ref"):
        run_git(repository, ("rev-parse", "--verify", "unknown-ref"))


def test_explicit_environment_without_path_does_not_reuse_ambient_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path / "repository", "content")
    executable = shutil.which("git")
    assert executable is not None
    observed_search_paths: list[str | None] = []

    def record_search_path(command: str, *, path: str | None = None) -> str | None:
        assert command == "git"
        observed_search_paths.append(path)
        return executable

    monkeypatch.setattr(shutil, "which", record_search_path)

    run_git(repository, ("status", "--short"), source_environment={})

    assert observed_search_paths == [os.defpath]


def test_git_forwards_the_owner_stop_predicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    observed_stop: object = None

    def stop_requested() -> bool:
        return False

    def fake_spawn(_command: str, _args: object, **kwargs: object) -> CommandResult:
        nonlocal observed_stop
        observed_stop = kwargs["stop_requested"]
        return CommandResult(0, "", "")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(bounded_git, "spawn", fake_spawn)

    run_git(repository, ("status", "--short"), stop_requested=stop_requested)

    assert observed_stop is stop_requested


def _repository(path: Path, content: str) -> Path:
    path.mkdir()
    run_git(path, ("init", "--quiet"))
    (path / "tracked.txt").write_text(content, encoding="utf-8")
    run_git(path, ("add", "tracked.txt"))
    run_git(
        path,
        (
            "-c",
            "user.name=CI Coordinator",
            "-c",
            "user.email=ci-coordinator@example.invalid",
            "commit",
            "--quiet",
            "--message=initial",
        ),
    )
    return path
