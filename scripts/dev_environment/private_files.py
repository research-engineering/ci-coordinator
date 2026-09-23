"""Owner-only filesystem primitives for local development state."""

from __future__ import annotations

import fcntl
import math
import os
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

_DIRECTORY_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_MAX_STATE_FILE_BYTES: Final = 131_072


class PrivateFileError(ValueError):
    """A local state path is unsafe or outside the admitted shape."""


class PrivateLockBusy(PrivateFileError):
    pass


def ensure_private_directory(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            status = path.lstat()
            if (
                not stat.S_ISDIR(status.st_mode)
                or stat.S_ISLNK(status.st_mode)
                or status.st_uid != os.getuid()
            ):
                raise PrivateFileError("private state directory is unsafe")
        else:
            path.mkdir(parents=True, mode=_DIRECTORY_MODE)
        os.chmod(path, _DIRECTORY_MODE)
    except PrivateFileError:
        raise
    except OSError as error:
        raise PrivateFileError("private state directory is unavailable") from error
    require_private_directory(path)


def require_private_directory(path: Path) -> None:
    try:
        status = path.lstat()
    except OSError as error:
        raise PrivateFileError("private state directory is unavailable") from error
    if (
        not stat.S_ISDIR(status.st_mode)
        or stat.S_ISLNK(status.st_mode)
        or status.st_uid != os.getuid()
        or status.st_mode & 0o777 != _DIRECTORY_MODE
    ):
        raise PrivateFileError("private state directory is unsafe")


def require_private_file(path: Path) -> None:
    try:
        status = path.lstat()
    except OSError as error:
        raise PrivateFileError("private state file is unavailable") from error
    if (
        not stat.S_ISREG(status.st_mode)
        or stat.S_ISLNK(status.st_mode)
        or status.st_uid != os.getuid()
        or status.st_nlink != 1
        or status.st_mode & 0o777 != _FILE_MODE
        or status.st_size > _MAX_STATE_FILE_BYTES
    ):
        raise PrivateFileError("private state file is unsafe")


def read_private_text(path: Path) -> str:
    require_private_file(path)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PrivateFileError("private state file is unreadable") from error


def atomic_write_private_text(
    path: Path,
    content: str,
    *,
    temporary_directory: Path | None = None,
) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise PrivateFileError("private state content exceeded the admitted bound")
    staging_directory = path.parent if temporary_directory is None else temporary_directory
    require_private_directory(staging_directory)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=staging_directory,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, _FILE_MODE)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
        if staging_directory != path.parent:
            _fsync_directory(staging_directory)
        require_private_file(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def exclusive_private_lock(path: Path) -> Iterator[None]:
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, _FILE_MODE)
    except OSError as error:
        raise PrivateFileError("private state lock is unavailable") from error
    try:
        os.fchmod(descriptor, _FILE_MODE)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        require_private_file(path)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def bounded_private_lock(path: Path, *, timeout_seconds: float = 0) -> Iterator[int]:
    """Close only this descriptor; an inherited flock must survive parent exit."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds < 0
    ):
        raise ValueError("lock timeout must be a nonnegative finite number")
    deadline = time.monotonic() + timeout_seconds
    require_private_directory(path.parent)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, _FILE_MODE)
    except OSError as error:
        raise PrivateFileError("private state lock is unavailable") from error
    try:
        _require_lock_descriptor(path, descriptor)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PrivateLockBusy("private state lock is busy") from error
                time.sleep(min(remaining, 0.02))
        _require_lock_descriptor(path, descriptor)
        yield descriptor
    finally:
        os.close(descriptor)


def _require_lock_descriptor(path: Path, descriptor: int) -> None:
    require_private_file(path)
    opened = os.fstat(descriptor)
    current = path.lstat()
    if (
        opened.st_dev != current.st_dev
        or opened.st_ino != current.st_ino
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.getuid()
        or opened.st_nlink != 1
        or opened.st_mode & 0o777 != _FILE_MODE
    ):
        raise PrivateFileError("private state lock identity changed")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
