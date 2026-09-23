from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_git import BoundedGitError, git_stdout_bytes, run_git
from scripts.proofkit_changed_paths import (
    assert_admitted_commit_range,
    assert_admitted_head,
    require_non_empty_path_set,
)

_FULL_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True, slots=True)
class ResolvedGitRange:
    base_commit: str
    head_commit: str
    paths: tuple[str, ...]


class ProofkitGitRange:
    def __init__(self, repository_root: Path) -> None:
        self._repository_root = repository_root

    def committed_paths_since(self, base_ref: str, head_ref: str) -> list[str]:
        return list(self.committed_range(base_ref, head_ref).paths)

    def committed_range(self, base_ref: str, head_ref: str) -> ResolvedGitRange:
        base_commit = self.resolve_commit(base_ref, "base")
        checkout_commit, head_commit = self._resolve_head_commit(head_ref)
        try:
            ancestry = run_git(
                self._repository_root,
                (
                    "merge-base",
                    "--is-ancestor",
                    base_commit,
                    head_commit,
                ),
                check=False,
            )
        except (OSError, BoundedGitError) as error:
            raise OSError(f"proofkit ancestry git execution failed: {error}") from error
        assert_admitted_commit_range(
            ancestry_status=ancestry.status,
            base_commit=base_commit,
            checkout_commit=checkout_commit,
            head_commit=head_commit,
        )
        paths = require_non_empty_path_set(
            self.git_paths(
                (
                    "diff",
                    "--name-only",
                    "--no-renames",
                    "-z",
                    base_commit,
                    head_commit,
                    "--",
                )
            ),
            "committed range",
        )
        return ResolvedGitRange(base_commit, head_commit, tuple(paths))

    def git_paths(self, args: Sequence[str]) -> list[str]:
        result = run_git(
            self._repository_root,
            args,
            decode_errors="surrogateescape",
        )
        return decode_git_path_output(git_stdout_bytes(result))

    def _resolve_head_commit(self, head_ref: str) -> tuple[str, str]:
        head_commit = self.resolve_commit(head_ref, "head")
        checkout_commit = self.resolve_commit("HEAD", "checkout head")
        assert_admitted_head(head_commit=head_commit, checkout_commit=checkout_commit)
        return checkout_commit, head_commit

    def resolve_commit(self, ref: str, label: str) -> str:
        if ref.startswith("-") or "\0" in ref:
            raise ValueError(f"invalid proofkit {label} ref")
        try:
            result = run_git(
                self._repository_root,
                ("rev-parse", "--verify", f"{ref}^{{commit}}"),
                check=False,
            )
        except (OSError, BoundedGitError) as error:
            raise OSError(f"proofkit {label} git execution failed: {error}") from error
        if result.status != 0:
            diagnostic = result.stderr.strip() or f"exit status {result.status}"
            raise ValueError(f"proofkit {label} git rev-parse rejected {ref}: {diagnostic}")
        commit = result.stdout.strip()
        if _FULL_COMMIT.fullmatch(commit) is None:
            raise ValueError(f"proofkit {label} ref resolved to an invalid commit: {ref}")
        return commit


def decode_git_path_output(output: bytes) -> list[str]:
    if output and not output.endswith(b"\0"):
        raise ValueError("proofkit git path output is not NUL terminated")
    paths: list[str] = []
    for segment in output.split(b"\0"):
        if not segment:
            continue
        try:
            paths.append(segment.decode("utf-8", errors="strict"))
        except UnicodeDecodeError as error:
            raise ValueError("proofkit git path is not valid UTF-8") from error
    return paths
