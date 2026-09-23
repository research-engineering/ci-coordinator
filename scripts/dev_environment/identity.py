"""Stable collision-resistant identity for one repository worktree."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scripts.bounded_process import spawn

_PROJECT_PREFIX: Final = "ci-coordinator-"
_PROJECT_PATTERN: Final = re.compile(r"ci-coordinator-[0-9a-f]{12}")
_MAX_GIT_OUTPUT_BYTES: Final = 1_048_576


@dataclass(frozen=True, slots=True)
class InstanceIdentity:
    repo_root: Path
    state_home: Path
    root_digest: str
    project_name: str
    state_directory: Path
    secrets_directory: Path
    environment_path: Path
    metadata_path: Path

    def __post_init__(self) -> None:
        if not self.repo_root.is_absolute() or not self.repo_root.is_dir():
            raise ValueError("repository root must be an existing absolute directory")
        if len(self.root_digest) != 64 or any(
            c not in "0123456789abcdef" for c in self.root_digest
        ):
            raise ValueError("repository root digest must be canonical SHA-256")
        if _PROJECT_PATTERN.fullmatch(self.project_name) is None:
            raise ValueError("development project name is invalid")
        if self.project_name != _PROJECT_PREFIX + self.root_digest[:12]:
            raise ValueError("development project name does not match repository identity")
        if not self.state_home.is_absolute():
            raise ValueError("development state home must be absolute")
        if self.state_directory.parent != self.state_home / "instances":
            raise ValueError("instance state escaped the development state home")
        if self.state_directory.name != self.project_name:
            raise ValueError("instance state does not match the project identity")
        if self.secrets_directory.parent != self.state_directory:
            raise ValueError("instance secrets escaped their state directory")
        if self.environment_path.parent != self.state_directory:
            raise ValueError("instance environment path escaped its state directory")
        if self.metadata_path.parent != self.state_directory:
            raise ValueError("instance metadata path escaped its state directory")


def derive_instance_identity(
    repo_root: Path,
    *,
    state_home: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> InstanceIdentity:
    canonical_root = repo_root.expanduser().resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("repository root must be a directory")
    canonical_state_home = _state_home(state_home, environment, canonical_root)
    digest = hashlib.sha256(str(canonical_root).encode("utf-8")).hexdigest()
    project_name = _PROJECT_PREFIX + digest[:12]
    state_directory = canonical_state_home / "instances" / project_name
    return InstanceIdentity(
        repo_root=canonical_root,
        state_home=canonical_state_home,
        root_digest=digest,
        project_name=project_name,
        state_directory=state_directory,
        secrets_directory=state_directory / "secrets",
        environment_path=state_directory / "runtime.env",
        metadata_path=state_directory / "metadata.json",
    )


def _state_home(
    explicit: Path | None,
    environment: Mapping[str, str] | None,
    repo_root: Path,
) -> Path:
    if explicit is not None:
        candidate = explicit
    else:
        source = os.environ if environment is None else environment
        configured = source.get("CI_COORDINATOR_DEV_STATE_HOME")
        xdg_state = source.get("XDG_STATE_HOME")
        if configured:
            candidate = Path(configured)
        elif xdg_state:
            candidate = Path(xdg_state) / "ci-coordinator"
        else:
            candidate = Path.home() / ".local" / "state" / "ci-coordinator"
    expanded = candidate.expanduser()
    if not expanded.is_absolute():
        raise ValueError("development state home must be absolute")
    canonical = expanded.resolve(strict=False)
    if any(
        canonical == root or canonical.is_relative_to(root) for root in _worktree_roots(repo_root)
    ):
        raise ValueError("development state home must be outside every registered worktree")
    return canonical


def _worktree_roots(repo_root: Path) -> tuple[Path, ...]:
    roots = {repo_root}
    if not (repo_root / ".git").exists():
        return tuple(roots)
    result = spawn(
        "git",
        ("worktree", "list", "--porcelain", "-z"),
        cwd=repo_root,
        max_buffer=_MAX_GIT_OUTPUT_BYTES,
        timeout_seconds=10,
    )
    if result.error is not None or result.status != 0:
        raise ValueError("registered worktrees could not be admitted")
    for field in result.stdout.split("\0"):
        if not field.startswith("worktree "):
            continue
        path = Path(field.removeprefix("worktree "))
        if not path.is_absolute():
            raise ValueError("registered worktree path must be absolute")
        try:
            # A container mount or a pruned checkout can hide a registered root.
            # Preserve its exclusion path without requiring it to be mounted.
            roots.add(path.resolve(strict=False))
        except OSError as error:
            raise ValueError("registered worktree path is unavailable") from error
    return tuple(sorted(roots))
