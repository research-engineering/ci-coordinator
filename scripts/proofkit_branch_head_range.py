from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.proofkit_git_range import ProofkitGitRange

_FULL_LOWERCASE_SHA = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True, slots=True)
class BranchHeadReceipt:
    base_commit: str
    changed_path_count: int
    head_commit: str


def read_exact_branch_head_range(environment: Mapping[str, str]) -> tuple[str, str]:
    return (
        _exact_sha(environment, "PROOFKIT_BASE_REF"),
        _exact_sha(environment, "PROOFKIT_HEAD_REF"),
    )


def admit_exact_branch_head_range(
    repository_root: Path,
    environment: Mapping[str, str],
) -> BranchHeadReceipt:
    base_ref, head_ref = read_exact_branch_head_range(environment)
    paths = ProofkitGitRange(repository_root).committed_paths_since(base_ref, head_ref)
    return BranchHeadReceipt(
        base_commit=base_ref,
        changed_path_count=len(paths),
        head_commit=head_ref,
    )


def assert_same_branch_head_receipt(
    before: BranchHeadReceipt,
    after: BranchHeadReceipt,
) -> None:
    if before != after:
        raise ValueError("branch-head quality range receipt changed during execution")


def _exact_sha(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or _FULL_LOWERCASE_SHA.fullmatch(value) is None:
        raise ValueError(f"{name} must be an exact full lowercase commit SHA")
    return value
