from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath


def real_repository_directory(root: Path, relative: Path, label: str) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        observed = current.lstat()
        if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
            raise ValueError(f"{label} must contain only real directories: {relative.as_posix()}")
    return current


def repository_path_matches(pattern: str, path: str) -> bool:
    return PurePosixPath(path).full_match(pattern, case_sensitive=True)


def read_repository_regular_file(
    root: Path,
    relative: Path,
    label: str,
    *,
    maximum_bytes: int,
) -> bytes:
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} path must be repository-relative")
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError(f"{label} byte bound must be a positive integer")

    lexical_root = Path(os.path.abspath(root))
    real_repository_directory(
        lexical_root,
        Path(*relative.parts[:-1]),
        f"{label} parent",
    )
    return _read_bounded_regular_file(
        lexical_root / relative,
        label,
        displayed_path=relative.as_posix(),
        maximum_bytes=maximum_bytes,
    )


def read_bounded_regular_file(
    path: Path,
    label: str,
    *,
    maximum_bytes: int,
) -> bytes:
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError(f"{label} byte bound must be a positive integer")
    lexical_path = Path(os.path.abspath(path))
    return _read_bounded_regular_file(
        lexical_path,
        label,
        displayed_path=str(lexical_path),
        maximum_bytes=maximum_bytes,
    )


def _read_bounded_regular_file(
    path: Path,
    label: str,
    *,
    displayed_path: str,
    maximum_bytes: int,
) -> bytes:
    observed = path.lstat()
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_size > maximum_bytes
    ):
        raise ValueError(f"{label} must be a bounded regular file: {displayed_path}")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if _file_identity(observed) != _file_identity(before):
            raise ValueError(f"{label} changed before it was read: {displayed_path}")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        payload = b"".join(chunks)
        if len(payload) > maximum_bytes:
            raise ValueError(f"{label} exceeds its byte bound: {displayed_path}")
        final_path = path.lstat()
        if (
            _file_identity(before) != _file_identity(after)
            or _file_identity(after) != _file_identity(final_path)
            or len(payload) != before.st_size
        ):
            raise ValueError(f"{label} changed while it was read: {displayed_path}")
        return payload
    finally:
        os.close(descriptor)


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
    )
