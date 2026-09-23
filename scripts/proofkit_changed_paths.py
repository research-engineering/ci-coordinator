from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

GitPaths = Callable[[Sequence[str]], list[str]]
CommittedPathsSince = Callable[[str, str], list[str]]
NormalizePath = Callable[[object], str]

_FULL_LOWERCASE_SHA = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True, slots=True)
class ChangedPathContext:
    base_ref: str
    head_ref: str
    paths: tuple[str, ...]


def changed_paths_from_git_context(
    *,
    args: Sequence[str],
    env: Mapping[str, str],
    normalize_path: NormalizePath,
    git_paths: GitPaths,
    committed_paths_since: CommittedPathsSince,
) -> list[str]:
    return list(
        changed_path_context_from_git_context(
            args=args,
            env=env,
            normalize_path=normalize_path,
            git_paths=git_paths,
            committed_paths_since=committed_paths_since,
        ).paths
    )


def changed_path_context_from_git_context(
    *,
    args: Sequence[str],
    env: Mapping[str, str],
    normalize_path: NormalizePath,
    git_paths: GitPaths,
    committed_paths_since: CommittedPathsSince,
) -> ChangedPathContext:
    explicit_paths: list[str] = []
    base_ref = _trimmed(env.get("PROOFKIT_BASE_REF"))
    head_ref = _trimmed(env.get("PROOFKIT_HEAD_REF"))
    index = 0
    while index < len(args):
        arg = args[index]
        value = args[index + 1] if index + 1 < len(args) else None
        if arg == "--changed-path":
            if value is None or value == "":
                raise ValueError("missing value for --changed-path")
            explicit_paths.append(normalize_path(value))
            index += 2
            continue
        if arg == "--base-ref":
            if value is None or value == "":
                raise ValueError("missing value for --base-ref")
            if base_ref is not None and base_ref != value:
                raise ValueError("multiple proofkit base refs are not allowed")
            base_ref = value
            index += 2
            continue
        if arg == "--head-ref":
            if value is None or value == "":
                raise ValueError("missing value for --head-ref")
            if head_ref is not None and head_ref != value:
                raise ValueError("multiple proofkit head refs are not allowed")
            head_ref = value
            index += 2
            continue
        raise ValueError(f"unsupported proofkit plan argument: {arg}")

    if _is_ci_environment(env):
        for label, value in (("base", base_ref), ("head", head_ref)):
            if value is not None and _FULL_LOWERCASE_SHA.fullmatch(value) is None:
                raise ValueError(f"CI proofkit {label} ref must be a full lowercase commit SHA")
        if base_ref is not None and head_ref is None:
            raise ValueError("CI proofkit range requires an explicit head commit SHA")
    if head_ref is not None and base_ref is None:
        raise ValueError("proofkit range head requires a base ref")

    worktree_paths = [
        *git_paths(("diff", "--name-only", "--no-renames", "-z")),
        *git_paths(("diff", "--cached", "--name-only", "--no-renames", "-z")),
        *git_paths(("ls-files", "--others", "--exclude-standard", "-z")),
    ]
    if base_ref is not None:
        selected_head_ref = head_ref if head_ref is not None else "HEAD"
        return ChangedPathContext(
            base_ref=base_ref,
            head_ref=selected_head_ref,
            paths=tuple(
                sorted(
                    {
                        *committed_paths_since(base_ref, selected_head_ref),
                        *worktree_paths,
                        *explicit_paths,
                    }
                )
            ),
        )
    if _is_ci_environment(env) and not explicit_paths:
        raise ValueError("PROOFKIT_BASE_REF or explicit --changed-path is required in CI")
    selected_paths = explicit_paths or worktree_paths
    return ChangedPathContext(
        base_ref="HEAD",
        head_ref="WORKTREE",
        paths=tuple(sorted(set(selected_paths))),
    )


def assert_admitted_commit_range(
    *,
    base_commit: str,
    head_commit: str,
    checkout_commit: str,
    ancestry_status: int,
) -> None:
    assert_admitted_head(head_commit=head_commit, checkout_commit=checkout_commit)
    if base_commit == head_commit:
        raise ValueError("proofkit base commit must differ from HEAD")
    if ancestry_status == 1:
        raise ValueError("proofkit base commit must be an ancestor of HEAD")
    if ancestry_status != 0:
        raise ValueError(f"proofkit ancestry check failed with status {ancestry_status}")


def assert_admitted_head(*, head_commit: str, checkout_commit: str) -> None:
    if head_commit != checkout_commit:
        raise ValueError("proofkit head commit must equal the checked out HEAD")


def require_non_empty_path_set(paths: list[str], mode: str) -> list[str]:
    if not paths:
        raise ValueError(f"proofkit {mode} must contain at least one path")
    return paths


def _trimmed(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _is_ci_environment(env: Mapping[str, str]) -> bool:
    return env.get("CI") == "true" or env.get("GITHUB_ACTIONS") == "true"
