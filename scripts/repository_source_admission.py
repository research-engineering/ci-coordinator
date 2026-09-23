from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

from scripts import file_descriptor

_file_identity = file_descriptor.file_identity
_read_bounded = file_descriptor.read_bounded


def read_bounded_repository_bytes(
    repo_root: Path,
    relative_path: PurePosixPath,
    *,
    maximum_bytes: int,
) -> bytes:
    if maximum_bytes < 0:
        raise ValueError("repository source byte limit must be non-negative")
    root_fd = _open_repository_root(repo_root)
    try:
        source_fd = _open_relative_entry(root_fd, relative_path)
        try:
            before = os.fstat(source_fd)
            _require_regular_file(before, relative_path)
            if before.st_size > maximum_bytes:
                raise ValueError(
                    f"repository source exceeds {maximum_bytes} bytes: {relative_path}"
                )
            payload = _read_bounded(source_fd, maximum_bytes + 1)
            after = os.fstat(source_fd)
            if _file_identity(before) != _file_identity(after) or len(payload) != before.st_size:
                raise ValueError(f"repository source changed during admission: {relative_path}")
            if len(payload) > maximum_bytes:
                raise ValueError(
                    f"repository source exceeds {maximum_bytes} bytes: {relative_path}"
                )
            return payload
        finally:
            os.close(source_fd)
    finally:
        os.close(root_fd)


def read_bounded_repository_text(
    repo_root: Path,
    relative_path: PurePosixPath,
    *,
    maximum_bytes: int,
) -> str:
    payload = read_bounded_repository_bytes(
        repo_root,
        relative_path,
        maximum_bytes=maximum_bytes,
    )
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError(f"repository source is not strict UTF-8: {relative_path}") from error


def admit_repository_regular_file(
    repo_root: Path,
    relative_path: PurePosixPath,
) -> None:
    root_fd = _open_repository_root(repo_root)
    try:
        source_fd = _open_relative_entry(root_fd, relative_path)
        try:
            _require_regular_file(os.fstat(source_fd), relative_path)
        finally:
            os.close(source_fd)
    finally:
        os.close(root_fd)


def _open_repository_root(repo_root: Path) -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("descriptor-relative no-follow traversal is unavailable")
    try:
        return os.open(
            repo_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise ValueError("repository source root is unavailable") from error


def _open_relative_entry(root_fd: int, relative_path: PurePosixPath) -> int:
    parts = _safe_parts(relative_path)
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
                    f"repository source metadata is unavailable at {component}"
                ) from error
            if stat.S_ISLNK(expected.st_mode):
                raise ValueError(f"repository source traverses symlink component: {component}")
            is_last = index == len(parts) - 1
            if not is_last and not stat.S_ISDIR(expected.st_mode):
                raise ValueError(
                    f"repository source traverses non-directory component: {component}"
                )
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
                    f"repository source changed during admission at {component}"
                ) from error
            actual = os.fstat(next_fd)
            if _file_identity(expected) != _file_identity(actual):
                os.close(next_fd)
                raise ValueError(f"repository source changed during admission at {component}")
            os.close(current_fd)
            current_fd = next_fd
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _safe_parts(relative_path: PurePosixPath) -> tuple[str, ...]:
    parts = relative_path.parts
    if relative_path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid repository source path: {relative_path}")
    return parts


def _require_regular_file(
    metadata: os.stat_result,
    relative_path: PurePosixPath,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"repository source must be a regular file: {relative_path}")
