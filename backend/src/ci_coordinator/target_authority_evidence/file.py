"""Bounded no-follow input and atomic content-addressed publication."""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from .codec import decode_target_authority_evidence, encode_target_authority_evidence
from .model import (
    MAX_EVIDENCE_BUNDLE_BYTES,
    AdmittedTargetAuthorityEvidence,
    TargetAuthorityEvidenceError,
)

_READ_CHUNK_BYTES: Final = 1_048_576
_MAX_PATH_BYTES: Final = 4_096
_TEMP_ATTEMPTS: Final = 16


@dataclass(frozen=True, slots=True)
class PublishedTargetAuthorityEvidence:
    path: Path
    bundle_digest: str


def read_target_authority_evidence(path: Path) -> AdmittedTargetAuthorityEvidence:
    absolute = _absolute_path(path, "evidence input")
    try:
        parent_fd, filename = _open_parent(absolute)
    except TargetAuthorityEvidenceError:
        raise
    except (NotImplementedError, OSError) as error:
        raise _file_error(
            "input_open_failed",
            "evidence input path could not be opened without following links",
        ) from error
    descriptor = -1
    try:
        descriptor = os.open(
            filename,
            _read_flags(),
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _file_error("input_not_regular", "evidence input is not a regular file")
        if before.st_size < 1 or before.st_size > MAX_EVIDENCE_BUNDLE_BYTES:
            raise _file_error("input_size_rejected", "evidence input size is outside its bound")
        content = _read_exact(descriptor, before.st_size)
        after = os.fstat(descriptor)
        if _stable_identity(before) != _stable_identity(after):
            raise _file_error("input_changed", "evidence input changed while it was read")
    except (NotImplementedError, OSError) as error:
        raise _file_error("input_open_failed", "evidence input could not be read safely") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
    return decode_target_authority_evidence(content)


def publish_target_authority_evidence(
    evidence: AdmittedTargetAuthorityEvidence,
    destination: Path,
) -> PublishedTargetAuthorityEvidence:
    if type(evidence) is not AdmittedTargetAuthorityEvidence:
        raise TypeError("publication requires exact admitted evidence")
    content = encode_target_authority_evidence(evidence)
    directory = _absolute_path(destination, "evidence destination")
    try:
        directory_fd = _open_directory(directory)
    except TargetAuthorityEvidenceError:
        raise
    except (NotImplementedError, OSError) as error:
        raise _file_error(
            "output_open_failed",
            "evidence destination could not be opened without following links",
        ) from error
    temporary_name: str | None = None
    temporary_fd = -1
    try:
        _require_private_owner_directory(directory_fd)
        final_name = f"{evidence.bundle.bundle_digest}.json"
        temporary_name, temporary_fd = _create_private_temporary(
            directory_fd,
            evidence.bundle.bundle_digest,
        )
        _write_exact(temporary_fd, content)
        os.fsync(temporary_fd)
        staged = os.fstat(temporary_fd)
        if not stat.S_ISREG(staged.st_mode) or staged.st_size != len(content):
            raise _file_error(
                "staged_file_mismatch",
                "staged evidence does not match the verified bundle size",
            )
        try:
            os.link(
                temporary_name,
                final_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise _file_error(
                "output_collision",
                "content-addressed evidence already exists",
            ) from error
        except (NotImplementedError, OSError) as error:
            raise _file_error(
                "atomic_link_failed",
                "evidence filesystem does not admit atomic no-overwrite publication",
            ) from error
        os.fsync(directory_fd)
        os.unlink(temporary_name, dir_fd=directory_fd)
        temporary_name = None
        os.fsync(directory_fd)
        return PublishedTargetAuthorityEvidence(
            directory / final_name,
            evidence.bundle.bundle_digest,
        )
    except TargetAuthorityEvidenceError:
        raise
    except (NotImplementedError, OSError) as error:
        raise _file_error(
            "publication_failed",
            "evidence publication did not complete durably",
        ) from error
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except (NotImplementedError, OSError):
                pass
        os.close(directory_fd)


def _absolute_path(path: Path, label: str) -> Path:
    if not isinstance(path, Path):
        raise TypeError(f"{label} path must be a pathlib.Path")
    if not path.is_absolute() or ".." in path.parts:
        raise _file_error("path_rejected", f"{label} path must be absolute and traversal-free")
    if len(os.fsencode(path)) > _MAX_PATH_BYTES:
        raise _file_error("path_rejected", f"{label} path exceeds its byte bound")
    return path


def _open_parent(path: Path) -> tuple[int, str]:
    name = path.name
    if not name or name in {".", ".."}:
        raise _file_error("path_rejected", "evidence input filename is not admitted")
    return _open_directory(path.parent), name


def _open_directory(path: Path) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    if nofollow is None or directory_flag is None or cloexec is None:
        raise _file_error(
            "filesystem_semantics_unsupported",
            "platform lacks required no-follow directory semantics",
        )
    descriptor = os.open("/", os.O_RDONLY | directory_flag | cloexec)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise _file_error("path_rejected", "path component is not admitted")
            next_descriptor = os.open(
                component,
                os.O_RDONLY | directory_flag | cloexec | nofollow,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or cloexec is None or nonblock is None:
        raise _file_error(
            "filesystem_semantics_unsupported",
            "platform lacks required no-follow file semantics",
        )
    return os.O_RDONLY | cast(int, cloexec) | cast(int, nofollow) | cast(int, nonblock)


def _read_exact(descriptor: int, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = expected_size
    while remaining:
        chunk = os.read(descriptor, min(remaining, _READ_CHUNK_BYTES))
        if not chunk:
            raise _file_error("input_changed", "evidence input ended before its stated size")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise _file_error("input_changed", "evidence input grew while it was read")
    return b"".join(chunks)


def _stable_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _require_private_owner_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise _file_error("output_not_directory", "evidence destination is not a directory")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        raise _file_error(
            "output_ownership_rejected",
            "evidence destination must be owner-controlled and not group/world writable",
        )


def _create_private_temporary(directory_fd: int, digest: str) -> tuple[str, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    if nofollow is None or cloexec is None:
        raise _file_error(
            "filesystem_semantics_unsupported",
            "platform lacks required private-file semantics",
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | cloexec | nofollow
    for _ in range(_TEMP_ATTEMPTS):
        name = f".{digest}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            continue
        try:
            os.fchmod(descriptor, 0o600)
        except BaseException as error:
            cleanup_error = _discard_failed_temporary(directory_fd, name, descriptor)
            if cleanup_error is not None and isinstance(error, Exception):
                raise _file_error(
                    "temporary_cleanup_failed",
                    "private evidence staging cleanup did not complete",
                ) from cleanup_error
            raise
        return name, descriptor
    raise _file_error(
        "temporary_name_exhausted",
        "private evidence staging could not allocate a unique name",
    )


def _discard_failed_temporary(
    directory_fd: int,
    name: str,
    descriptor: int,
) -> BaseException | None:
    first_error: BaseException | None = None
    try:
        os.close(descriptor)
    except BaseException as error:
        first_error = error
    try:
        os.unlink(name, dir_fd=directory_fd)
    except BaseException as error:
        if first_error is None:
            first_error = error
    return first_error


def _write_exact(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written < 1:
            raise _file_error("short_write", "evidence staging made no write progress")
        offset += written


def _file_error(code: str, message: str) -> TargetAuthorityEvidenceError:
    return TargetAuthorityEvidenceError(code, message)
