from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import diagram_push
from scripts.bounded_git import run_git
from scripts.bounded_process import InteractiveResult, spawn
from scripts.diagram_inventory import resolve_commit
from scripts.diagram_push import admit_existing_hooks, selected_commits

ZERO = "0" * 40


def commit(root: Path, path: str, body: str) -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    run_git(root, ["add", "--", path])
    run_git(
        root,
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "fixture",
        ],
    )
    return run_git(root, ["rev-parse", "HEAD"]).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    run_git(tmp_path, ["init", "--quiet"])
    return tmp_path


def record(local: str, remote: str) -> str:
    return f"refs/heads/topic {local} refs/heads/topic {remote}\n"


def test_new_ref_and_missing_remote_select_the_exact_tip(repository: Path) -> None:
    head = commit(repository, "README.md", "old\n")
    assert selected_commits(repository, record(head, ZERO)) == (head,)
    assert selected_commits(repository, record(head, "f" * 40)) == (head,)


def test_dirty_worktree_cannot_replace_pushed_document_bytes(repository: Path) -> None:
    base = commit(repository, "README.md", "old\n")
    head = commit(repository, "README.md", "new\n")
    (repository / "README.md").write_text("old\n")
    assert selected_commits(repository, record(head, base)) == (head,)


def test_unrelated_source_change_does_not_run_diagrams(repository: Path) -> None:
    base = commit(repository, "README.md", "old\n")
    head = commit(repository, "backend/example.py", "x = 1\n")
    assert selected_commits(repository, record(head, base)) == ()


def test_deleted_document_and_changed_checker_trigger_full_tip(
    repository: Path,
) -> None:
    base = commit(repository, "docs/old.md", "old\n")
    run_git(repository, ["rm", "docs/old.md"])
    head = commit(repository, "unrelated.txt", "new\n")
    assert selected_commits(repository, record(head, base)) == (head,)
    next_head = commit(repository, "scripts/diagram_check.py", "changed\n")
    assert selected_commits(repository, record(next_head, head)) == (next_head,)


def test_all_ref_records_are_processed_and_deduplicated(repository: Path) -> None:
    first = commit(repository, "README.md", "one\n")
    second = commit(repository, "README.md", "two\n")
    assert selected_commits(
        repository, record(first, ZERO) + record(second, ZERO) + record(first, ZERO)
    ) == tuple(sorted([first, second]))
    assert selected_commits(repository, record(ZERO, second)) == ()


def test_annotated_tag_resolves_to_its_commit(repository: Path) -> None:
    head = commit(repository, "README.md", "one\n")
    run_git(
        repository,
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "tag",
            "-a",
            "tagged",
            "-m",
            "fixture",
        ],
    )
    tag = run_git(repository, ["rev-parse", "tagged"]).stdout.strip()
    assert selected_commits(repository, record(tag, ZERO)) == (head,)


def test_replacement_commit_cannot_hide_a_document_change(repository: Path) -> None:
    base = commit(repository, "README.md", "old\n")
    head = commit(repository, "README.md", "new\n")
    run_git(repository, ["replace", head, base])
    assert run_git(repository, ["diff", "--name-only", base, head]).stdout == ""
    assert selected_commits(repository, record(head, base)) == (head,)


def test_replacement_blob_cannot_substitute_pushed_document(repository: Path) -> None:
    from scripts.diagram_contract import PROFILE_PATH
    from scripts.diagram_inventory import build_manifest
    from scripts.documentation_graph_policy import DEFAULT_PROFILE_PATH

    source_root = Path(__file__).resolve().parents[2]
    for path in (PROFILE_PATH, DEFAULT_PROFILE_PATH):
        target = repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / path).read_bytes())
    run_git(repository, ["add", "."])
    head = commit(repository, "README.md", "```mermaid\nunsupportedDiagram\n```\n")
    original = run_git(repository, ["rev-parse", f"{head}:README.md"]).stdout.strip()
    valid = "```mermaid\nflowchart TD\nA --> B\n```\n"
    (repository / "README.md").write_text(valid)
    replacement = run_git(repository, ["hash-object", "-w", "README.md"]).stdout.strip()
    run_git(repository, ["replace", original, replacement])
    assert run_git(repository, ["show", f"{head}:README.md"]).stdout == valid
    with pytest.raises(ValueError, match="unsupported"):
        build_manifest(repository, head)


@pytest.mark.parametrize("selector", ["both", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"])
def test_redirected_repository_cannot_bypass_a_foreign_hook(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str
) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    run_git(foreign, ["init", "--quiet"])
    custom_hook = foreign / ".git/hooks/pre-push"
    custom_hook.write_text("#!/bin/sh\nexit 23\n")
    original = custom_hook.read_bytes()
    (repository / ".githooks").mkdir()
    installed_hook = repository / ".githooks/pre-push"
    installed_hook.write_text("#!/bin/sh\nexit 0\n")
    installed_hook.chmod(0o700)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    if selector == "both":
        monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    else:
        monkeypatch.setenv(
            selector, str(foreign if selector == "GIT_WORK_TREE" else foreign / ".git")
        )
    transport = Mock(side_effect=AssertionError("push started before repository admission"))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_push, "run_interactive", transport)
    with pytest.raises(ValueError, match="repository identity"):
        diagram_push.push(repository, ["origin", "HEAD"])
    transport.assert_not_called()
    assert custom_hook.read_bytes() == original


def test_linked_worktree_uses_common_hooks_and_keeps_the_admitted_environment(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(repository, "README.md", "fixture\n")
    linked = tmp_path / "linked"
    run_git(repository, ["worktree", "add", "--detach", str(linked)])
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv(
        "GIT_DIR", run_git(linked, ["rev-parse", "--absolute-git-dir"]).stdout.strip()
    )
    monkeypatch.setenv("GIT_WORK_TREE", str(linked))
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    monkeypatch.setenv("GIT_ASKPASS", "/usr/bin/false")
    admit_existing_hooks(linked)
    common_hook = repository / ".git/hooks/pre-push"
    common_hook.write_text("#!/bin/sh\nexit 23\n")
    with pytest.raises(ValueError, match="existing pre-push"):
        admit_existing_hooks(linked)
    common_hook.unlink()
    (linked / ".githooks").mkdir()
    hook = linked / ".githooks/pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o700)
    expected_gitdir = str(run_git(linked, ["rev-parse", "--absolute-git-dir"]).stdout.strip())
    admitted_environments: list[dict[str, str]] = []

    def admission(
        root: Path,
        *,
        environment: dict[str, str],
        stop_requested: object = None,
    ) -> None:
        assert stop_requested is None
        admit_existing_hooks(root, environment=environment)
        admitted_environments.append(dict(environment))
        monkeypatch.setenv("GIT_DIR", str(repository / ".git"))
        monkeypatch.setenv("GIT_SSH_COMMAND", "must-not-replace-the-admitted-transport")

    monkeypatch.setattr(diagram_push, "admit_existing_hooks", admission)
    transport = Mock(return_value=InteractiveResult(0, True))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_push, "run_interactive", transport)
    assert diagram_push.push(linked, ["origin", "HEAD"]) == 0
    environment = transport.call_args.kwargs["env"]
    assert environment == admitted_environments[0]
    assert environment["GIT_DIR"] == expected_gitdir
    assert environment["GIT_WORK_TREE"] == str(linked)
    assert environment["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert environment["GIT_ASKPASS"] == "/usr/bin/false"


@pytest.mark.parametrize(
    "payload", ["broken\n", "a bad b oid\n", f"a {'a' * 40} b {ZERO}", "x" * 65_537]
)
def test_malformed_or_truncated_hook_input_fails(repository: Path, payload: str) -> None:
    with pytest.raises(ValueError):
        selected_commits(repository, payload)


def test_existing_hooks_are_not_silently_superseded(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    admit_existing_hooks(repository)
    hook = repository / ".git/hooks/pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(ValueError, match="existing pre-push"):
        admit_existing_hooks(repository)
    hook.unlink()
    run_git(repository, ["config", "core.hooksPath", "custom"])
    with pytest.raises(ValueError, match=r"core\.hooksPath"):
        admit_existing_hooks(repository)


def test_hook_admission_and_ref_selection_forward_cancellation(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    base = commit(repository, "README.md", "old\n")
    head = commit(repository, "README.md", "new\n")
    stop = Mock(return_value=False)
    git_calls = Mock(wraps=run_git)
    configuration = Mock(wraps=spawn)
    resolution = Mock(wraps=resolve_commit)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_push, "run_git", git_calls)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_push, "spawn", configuration)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_push, "resolve_commit", resolution)
    admit_existing_hooks(repository, stop_requested=stop)
    assert selected_commits(repository, record(head, base), stop_requested=stop) == (head,)
    assert configuration.call_count == 3
    assert resolution.call_count == 1
    assert all(call.kwargs["stop_requested"] is stop for call in configuration.call_args_list)
    assert resolution.call_args.kwargs["stop_requested"] is stop
    assert {tuple(call.args[1][1:3]) for call in git_calls.call_args_list} == {
        ("rev-parse", "--path-format=absolute"),
        ("rev-parse", "--verify"),
        ("diff", "--name-only"),
    }
    assert all(call.kwargs["stop_requested"] is stop for call in git_calls.call_args_list)
