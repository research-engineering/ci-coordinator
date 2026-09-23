from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Collection, Sequence
from pathlib import Path, PurePosixPath

from scripts import file_descriptor
from scripts.bounded_process import StopPredicate, spawn
from scripts.documentation_graph_contract import (
    DocumentationGraphError,
    DocumentationLimits,
    contains_control,
    safe_relative_path,
)

_file_identity = file_descriptor.file_identity
_read_bounded = file_descriptor.read_bounded

_INVENTORY_TIMEOUT_SECONDS = 30
MAX_PROFILE_BYTES = 65_536


def discover_repository_paths(
    repo_root: Path,
    limits: DocumentationLimits,
    *,
    timeout_seconds: float = _INVENTORY_TIMEOUT_SECONDS,
    stop_requested: StopPredicate | None = None,
) -> tuple[str, ...]:
    git = shutil.which("git")
    if git is None:
        raise OSError("git executable was not found on PATH")
    result = spawn(
        git,
        (
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ),
        cwd=repo_root,
        env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
        max_buffer=limits.max_inventory_bytes,
        timeout_seconds=timeout_seconds,
        stop_requested=stop_requested,
    )
    if result.error is not None:
        raise OSError(result.error)
    if result.status != 0:
        detail = result.stderr.strip() or f"status {result.status}"
        raise OSError(f"git ls-files failed: {detail}")
    if result.stderr:
        raise OSError(f"git ls-files wrote to stderr: {result.stderr.strip()}")
    if "\ufffd" in result.stdout:
        raise UnicodeError("git path inventory is not strict UTF-8")
    if result.stdout and not result.stdout.endswith("\0"):
        raise ValueError("git path inventory is not NUL-terminated")
    return tuple(path for path in result.stdout.split("\0") if path)


def read_policy_source(repo_root: Path, profile_path: Path) -> str:
    relative_path = safe_relative_path(profile_path.as_posix(), "documentation graph profile path")
    try:
        root_fd = _open_repository_root(repo_root)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"documentation graph profile root is unavailable: {_stable_error(error)}"
        ) from error
    try:
        try:
            source_fd = _open_relative_entry(root_fd, relative_path)
        except (OSError, ValueError) as error:
            raise ValueError(
                f"documentation graph profile is unavailable: {_stable_error(error)}"
            ) from error
        try:
            before = os.fstat(source_fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("documentation graph profile must be a regular file")
            if before.st_size > MAX_PROFILE_BYTES:
                raise ValueError(f"documentation graph profile exceeds {MAX_PROFILE_BYTES} bytes")
            payload = _read_bounded(source_fd, MAX_PROFILE_BYTES + 1)
            after = os.fstat(source_fd)
            if _file_identity(before) != _file_identity(after) or len(payload) != before.st_size:
                raise ValueError("documentation graph profile changed during admission")
        finally:
            os.close(source_fd)
    finally:
        os.close(root_fd)
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError("documentation graph profile is not strict UTF-8") from error


def admit_inventory(
    paths: Collection[str],
    limits: DocumentationLimits,
) -> frozenset[str]:
    values = tuple(paths)
    issues: list[str] = []
    if len(values) > limits.max_repository_path_count:
        issues.append(
            f"documentation graph: repository path count exceeds {limits.max_repository_path_count}"
        )
    if len(values) != len(set(values)):
        issues.append("documentation graph: repository path inventory contains duplicates")
    total_bytes = 0
    for path in values:
        if not isinstance(path, str) or not path:
            issues.append("documentation graph: repository path must be a non-empty string")
            continue
        total_bytes += len(path.encode("utf-8")) + 1
        if contains_control(path):
            issues.append(f"documentation graph: repository path contains a control byte: {path!r}")
            continue
        try:
            safe_relative_path(path, "repository path")
        except ValueError as error:
            issues.append(f"documentation graph: {error}: {path}")
    if total_bytes > limits.max_inventory_bytes:
        issues.append(
            f"documentation graph: repository path bytes exceed {limits.max_inventory_bytes}"
        )
    if issues:
        raise DocumentationGraphError(issues)
    return frozenset(values)


def read_document_sources(
    repo_root: Path,
    document_paths: Sequence[str],
    limits: DocumentationLimits,
) -> tuple[dict[str, bytes], list[str]]:
    payloads: dict[str, bytes] = {}
    issues: list[str] = []
    total_bytes = 0
    try:
        root_fd = _open_repository_root(repo_root)
    except (OSError, ValueError) as error:
        return {}, [f"documentation graph: repository root is unavailable: {_stable_error(error)}"]
    try:
        for relative_path in document_paths:
            try:
                source_fd = _open_relative_entry(root_fd, relative_path)
            except (OSError, ValueError) as error:
                issues.append(f"{relative_path}: {_stable_error(error)}")
                continue
            try:
                before = os.fstat(source_fd)
                if not stat.S_ISREG(before.st_mode):
                    issues.append(f"{relative_path}: Markdown source must be a regular file")
                    continue
                projected_total = total_bytes + before.st_size
                if before.st_size > limits.max_document_bytes:
                    issues.append(
                        f"{relative_path}: document bytes exceed {limits.max_document_bytes}"
                    )
                if projected_total > limits.max_total_bytes:
                    issues.append(
                        f"documentation graph: aggregate bytes exceed {limits.max_total_bytes}"
                    )
                    break
                if before.st_size > limits.max_document_bytes:
                    total_bytes = projected_total
                    continue
                remaining_bytes = limits.max_total_bytes - total_bytes
                total_bytes = projected_total
                payload = _read_bounded(
                    source_fd,
                    min(
                        limits.max_document_bytes + 1,
                        remaining_bytes + 1,
                    ),
                )
                after = os.fstat(source_fd)
                if (
                    _file_identity(before) != _file_identity(after)
                    or len(payload) != before.st_size
                ):
                    issues.append(f"{relative_path}: document changed during admission")
                    continue
                payloads[relative_path] = payload
            except OSError as error:
                issues.append(f"{relative_path}: read failed: {_stable_error(error)}")
            finally:
                os.close(source_fd)
    finally:
        os.close(root_fd)
    return payloads, issues


def repository_directories(paths: Collection[str]) -> frozenset[str]:
    directories: set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return frozenset(directories)


def path_component_issue(repo_root: Path, relative_path: str) -> str | None:
    try:
        root_fd = _open_repository_root(repo_root)
    except (OSError, ValueError) as error:
        return f"repository root is unavailable: {_stable_error(error)}"
    try:
        try:
            target_fd = _open_relative_entry(root_fd, relative_path)
        except (OSError, ValueError) as error:
            return _stable_error(error)
        os.close(target_fd)
        return None
    finally:
        os.close(root_fd)


def _open_repository_root(repo_root: Path) -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("descriptor-relative no-follow traversal is unavailable")
    return os.open(
        repo_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )


def _open_relative_entry(root_fd: int, relative_path: str) -> int:
    parts = PurePosixPath(relative_path).parts
    current_fd = os.dup(root_fd)
    traversed: list[str] = []
    try:
        for index, part in enumerate(parts):
            traversed.append(part)
            component = "/".join(traversed)
            try:
                expected = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except OSError as error:
                raise ValueError(
                    f"target metadata is unavailable at {component}: {_stable_error(error)}"
                ) from error
            if stat.S_ISLNK(expected.st_mode):
                raise ValueError(f"path traverses symlink component: {component}")
            is_last = index == len(parts) - 1
            if not is_last and not stat.S_ISDIR(expected.st_mode):
                raise ValueError(f"path traverses non-directory component: {component}")
            flags = (
                os.O_RDONLY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            if not is_last:
                flags |= os.O_DIRECTORY
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except OSError as error:
                raise ValueError(
                    f"path changed during admission at {component}: {_stable_error(error)}"
                ) from error
            actual = os.fstat(next_fd)
            if _file_identity(expected) != _file_identity(actual):
                os.close(next_fd)
                raise ValueError(f"path changed during admission at {component}")
            os.close(current_fd)
            current_fd = next_fd
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _stable_error(error: OSError | ValueError) -> str:
    if isinstance(error, OSError):
        return f"OS error {error.errno if error.errno is not None else 'unknown'}"
    return str(error)
