from __future__ import annotations

import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from scripts.bounded_process import StopPredicate, spawn

DEFAULT_GIT_OUTPUT_BYTES: Final = 16 * 1024 * 1024
DEFAULT_GIT_TIMEOUT_SECONDS: Final = 30.0
GitDecodeErrors = Literal["strict", "surrogateescape"]


class BoundedGitError(RuntimeError):
    pass


class BoundedGitCommandError(BoundedGitError):
    pass


@dataclass(frozen=True, slots=True)
class BoundedGitResult:
    status: int
    stdout: str
    stderr: str


def run_git(
    repository_root: Path,
    arguments: Sequence[str],
    *,
    check: bool = True,
    decode_errors: GitDecodeErrors = "strict",
    max_buffer: int = DEFAULT_GIT_OUTPUT_BYTES,
    source_environment: Mapping[str, str] | None = None,
    stop_requested: StopPredicate | None = None,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
) -> BoundedGitResult:
    environment = os.environ if source_environment is None else source_environment
    configured_path = environment.get("PATH")
    search_path = os.defpath if configured_path is None else configured_path
    executable = shutil.which("git", path=search_path)
    if executable is None:
        raise OSError("git executable was not found on PATH")
    projected_environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }
    projected_environment["PATH"] = search_path
    result = spawn(
        executable,
        tuple(arguments),
        cwd=repository_root,
        decode_errors=decode_errors,
        env=projected_environment,
        max_buffer=max_buffer,
        stop_requested=stop_requested,
        timeout_seconds=timeout_seconds,
    )
    if result.error is not None:
        raise BoundedGitError(result.error)
    if result.status is None:
        raise BoundedGitError("git execution ended without a status")
    if check and result.status != 0:
        diagnostic = result.stderr.strip() or f"exit status {result.status}"
        raise BoundedGitCommandError(f"git {' '.join(arguments)} failed: {diagnostic}")
    return BoundedGitResult(result.status, result.stdout, result.stderr)


def capture_git_text(
    repository_root: Path,
    arguments: Sequence[str],
    *,
    strip: bool = False,
) -> str:
    output = run_git(repository_root, arguments).stdout
    return output.strip() if strip else output


def git_stdout_bytes(result: BoundedGitResult) -> bytes:
    return result.stdout.encode("utf-8", errors="surrogateescape")
