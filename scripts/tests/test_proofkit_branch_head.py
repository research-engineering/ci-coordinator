from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest
import scripts.proofkit_branch_head_quality as branch_quality
from scripts.mutation.detached_worktree_lifecycle import CleanupResult
from scripts.proofkit_branch_head_quality import (
    UnsupportedBranchHeadHostError,
    assert_tracked_tree_clean,
    run_branch_head_quality,
)
from scripts.proofkit_branch_head_range import (
    BranchHeadReceipt,
    admit_exact_branch_head_range,
    assert_same_branch_head_receipt,
    read_exact_branch_head_range,
)
from scripts.proofkit_git_range import ProofkitGitRange
from scripts.quality_plan import WitnessCommand, load_quality_plan

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return result.stdout.strip()


def _repository(root: Path) -> tuple[str, str]:
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Branch Head Probe")
    _git(root, "config", "user.email", "branch-head@example.invalid")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "--quiet", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "tracked.txt").write_text("head\n", encoding="utf-8")
    _git(root, "commit", "--all", "--quiet", "-m", "head")
    return base, _git(root, "rev-parse", "HEAD")


def _one_file_per_commit_repository(root: Path, changed: tuple[str, ...]) -> tuple[str, str]:
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Branch Head Probe")
    _git(root, "config", "user.email", "branch-head@example.invalid")
    for name in (*changed, "unchanged.txt"):
        (root / name).write_text("base\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    for name in changed:
        (root / name).write_text("changed\n", encoding="utf-8")
        _git(root, "commit", "--all", "--quiet", "-m", f"change {name}")
    return base, _git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"PROOFKIT_BASE_REF": "a" * 40},
        {"PROOFKIT_BASE_REF": "A" * 40, "PROOFKIT_HEAD_REF": "b" * 40},
        {"PROOFKIT_BASE_REF": "HEAD", "PROOFKIT_HEAD_REF": "b" * 40},
    ],
)
def test_branch_head_range_requires_two_exact_lowercase_shas(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="exact full lowercase commit SHA"):
        read_exact_branch_head_range(environment)


def test_branch_head_range_admits_only_checked_out_nonempty_descendant(
    tmp_path: Path,
) -> None:
    base, head = _repository(tmp_path)
    receipt = admit_exact_branch_head_range(
        tmp_path,
        {"PROOFKIT_BASE_REF": base, "PROOFKIT_HEAD_REF": head},
    )
    assert receipt == BranchHeadReceipt(base, 1, head)

    with pytest.raises(ValueError, match="must differ"):
        admit_exact_branch_head_range(
            tmp_path,
            {"PROOFKIT_BASE_REF": head, "PROOFKIT_HEAD_REF": head},
        )


def test_branch_head_range_diffs_from_the_caller_base_not_the_head_parent(
    tmp_path: Path,
) -> None:
    changed = ("first.txt", "second.txt", "third.txt")
    base, head = _one_file_per_commit_repository(tmp_path, changed)
    assert _git(tmp_path, "rev-parse", "HEAD~3") == base

    resolved = ProofkitGitRange(tmp_path).committed_range(base, head)
    receipt = admit_exact_branch_head_range(
        tmp_path,
        {"PROOFKIT_BASE_REF": base, "PROOFKIT_HEAD_REF": head},
    )

    assert (resolved.base_commit, resolved.paths, resolved.head_commit) == (base, changed, head)
    assert receipt == BranchHeadReceipt(base, len(changed), head)


def test_branch_head_receipt_comparison_is_exact() -> None:
    receipt = BranchHeadReceipt("a" * 40, 1, "b" * 40)
    assert_same_branch_head_receipt(receipt, receipt)
    with pytest.raises(ValueError, match="receipt changed"):
        assert_same_branch_head_receipt(
            receipt,
            BranchHeadReceipt(receipt.base_commit, 2, receipt.head_commit),
        )


def test_cleanliness_rejects_tracked_and_untracked_changes(tmp_path: Path) -> None:
    _repository(tmp_path)
    assert_tracked_tree_clean(tmp_path, "probe")
    (tmp_path / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    with pytest.raises(ValueError, match="worktree must equal HEAD"):
        assert_tracked_tree_clean(tmp_path, "probe")


def test_branch_head_installs_signal_authority_before_preflight_and_always_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeLifecycle:
        def __init__(self, **_kwargs: object) -> None:
            self.received_signal: signal.Signals | None = None
            events.append("constructed")

        def install_signal_handlers(self) -> None:
            events.append("installed")

        def assert_running(self) -> None:
            events.append("asserted")
            if self.received_signal is not None:
                raise RuntimeError("interrupted preflight")

        def cleanup(self) -> CleanupResult:
            events.append("cleaned")
            return CleanupResult("passed", "not-needed")

        def dispose_signal_handlers(self) -> None:
            events.append("disposed")

        def rethrow_signal_if_needed(self) -> None:
            events.append("rethrown")
            raise SystemExit(128 + signal.SIGTERM.value)

    lifecycle: FakeLifecycle | None = None

    def construct_lifecycle(**kwargs: object) -> FakeLifecycle:
        nonlocal lifecycle
        lifecycle = FakeLifecycle(**kwargs)
        return lifecycle

    def interrupting_git(_root: Path, _arguments: object) -> str:
        events.append("preflight")
        assert lifecycle is not None
        lifecycle.received_signal = signal.SIGTERM
        return "a" * 40

    def forbidden_receipt(_root: Path, _environment: object) -> BranchHeadReceipt:
        pytest.fail("preflight must stop before reading the receipt")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(branch_quality, "DetachedWorktreeLifecycle", construct_lifecycle)
    monkeypatch.setattr(branch_quality, "_git_capture", interrupting_git)

    with pytest.raises(SystemExit) as failure:
        run_branch_head_quality(
            tmp_path,
            environment={},
            execute_command=lambda _command: None,
            read_receipt=forbidden_receipt,
            host_platform="linux",
        )

    assert failure.value.code == 128 + signal.SIGTERM.value
    assert events == [
        "constructed",
        "installed",
        "preflight",
        "asserted",
        "cleaned",
        "disposed",
        "rethrown",
    ]


class _PassiveLifecycle:
    def __init__(self, **_kwargs: object) -> None:
        self.received_signal: signal.Signals | None = None

    def install_signal_handlers(self) -> None:
        pass

    def assert_running(self) -> None:
        pass

    def cleanup(self) -> CleanupResult:
        return CleanupResult("passed", "not-needed")

    def dispose_signal_handlers(self) -> None:
        pass


@pytest.mark.parametrize("host_platform", ["darwin", "win32"])
def test_branch_head_refuses_a_non_linux_host_before_any_preflight_or_witness(
    monkeypatch: pytest.MonkeyPatch,
    host_platform: str,
) -> None:
    def forbidden(*_arguments: object, **_keywords: object) -> NoReturn:
        pytest.fail("an unsupported host must be refused before branch-head work starts")

    monkeypatch.setattr(branch_quality, "DetachedWorktreeLifecycle", forbidden)
    monkeypatch.setattr(branch_quality, "_git_capture", forbidden)
    monkeypatch.setattr(branch_quality, "load_quality_plan", forbidden)

    with pytest.raises(UnsupportedBranchHeadHostError) as refusal:
        run_branch_head_quality(
            REPO_ROOT,
            environment={},
            execute_command=forbidden,
            read_receipt=forbidden,
            write_receipt=forbidden,
            host_platform=host_platform,
        )

    assert str(refusal.value) == (
        "branch-head quality is supported only on Linux hosts; refusing on "
        f"{host_platform} before any witness starts because the sequence includes "
        "Linux-only native witnesses such as python.native-timeout (GNU timeout). "
        "Run it on a Linux host or GitHub Linux runner."
    )


def test_branch_head_cli_refuses_a_non_linux_host_before_git_discovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_arguments: object, **_keywords: object) -> NoReturn:
        pytest.fail("the CLI must refuse an unsupported host before Git discovery")

    monkeypatch.setattr(branch_quality, "_git_capture", forbidden)
    monkeypatch.setattr(branch_quality, "run_branch_head_quality", forbidden)

    status = branch_quality.main([], host_platform="darwin")

    assert status == 1
    assert capsys.readouterr().err == (
        "branch-head quality is supported only on Linux hosts; refusing on "
        "darwin before any witness starts because the sequence includes "
        "Linux-only native witnesses such as python.native-timeout (GNU timeout). "
        "Run it on a Linux host or GitHub Linux runner.\n"
    )


def test_branch_head_runs_the_complete_plan_on_a_linux_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = BranchHeadReceipt("a" * 40, 1, "b" * 40)
    executed: list[str] = []
    written: list[str] = []

    def git(_root: Path, arguments: object) -> str:
        return "b" * 40 if arguments == ("rev-parse", "HEAD") else ""

    def execute(command: WitnessCommand) -> None:
        executed.append(command.command_id)

    monkeypatch.setattr(branch_quality, "DetachedWorktreeLifecycle", _PassiveLifecycle)
    monkeypatch.setattr(branch_quality, "_git_capture", git)

    result = run_branch_head_quality(
        REPO_ROOT,
        environment={},
        execute_command=execute,
        read_receipt=lambda _root, _environment: receipt,
        write_receipt=written.append,
        host_platform="linux",
    )

    expected = [
        command.command_id for command in load_quality_plan(REPO_ROOT).branch_head_commands()
    ]
    assert "python.native-timeout" in expected
    assert executed == expected
    assert result == receipt
    assert written == [f"branch-head quality passed for {'a' * 40}...{'b' * 40}"]
