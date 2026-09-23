from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

import pytest
import scripts.proofkit_branch_head_quality as branch_quality
from scripts.mutation.detached_worktree_lifecycle import CleanupResult
from scripts.proofkit_branch_head_quality import (
    assert_tracked_tree_clean,
    run_branch_head_quality,
)
from scripts.proofkit_branch_head_range import (
    BranchHeadReceipt,
    admit_exact_branch_head_range,
    assert_same_branch_head_receipt,
    read_exact_branch_head_range,
)


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
