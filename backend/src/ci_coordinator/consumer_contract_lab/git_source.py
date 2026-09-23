"""Bounded local Git and regular-file identity for contract evidence."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from contextlib import suppress
from pathlib import Path
from typing import Final

from ci_coordinator.consumer_contract_lab.model import GitSourceSnapshot
from ci_coordinator.consumer_contract_lab.process import run_bounded
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context.freshness import is_safe_relative_path

_MAX_FILE_BYTES: Final = 4_194_304
_MAX_GIT_OUTPUT_BYTES: Final = 2_097_152
_GIT_TIMEOUT_SECONDS: Final = 10
_MAX_PACKAGE_ENTRIES: Final = 1_024
_MAX_PACKAGE_BYTES: Final = 16_777_216


class GitSourceError(ValueError):
    """A local source path cannot be represented as exact bounded evidence."""


def git_snapshot(
    root: Path,
    *,
    relevant_files: tuple[tuple[str, bytes], ...],
) -> GitSourceSnapshot:
    head = _commit_id(_git(root, ("rev-parse", "--verify", "HEAD^{commit}")))
    paths = tuple(path for path, _ in relevant_files)
    if (
        not relevant_files
        or paths != tuple(sorted(set(paths), key=utf16_sort_key))
        or any(not is_safe_relative_path(path) for path in paths)
        or any(type(content) is not bytes for _, content in relevant_files)
    ):
        raise GitSourceError("relevant Git files are not canonical")
    head_blobs = _head_blob_ids(root, head, paths)
    dirty = {
        path for path, content in relevant_files if head_blobs.get(path) != _git_blob_id(content)
    }
    if _commit_id(_git(root, ("rev-parse", "--verify", "HEAD^{commit}"))) != head:
        raise GitSourceError("repository head changed during source inspection")
    dirty_paths = tuple(sorted(dirty, key=utf16_sort_key))
    return GitSourceSnapshot(
        head=head,
        dirty_paths=dirty_paths,
        source_kind="worktree" if dirty_paths else "commit",
    )


def coordinator_package_snapshot(
    root: Path,
    package_relative: str,
    commit: str,
    loaded_package: Path,
) -> GitSourceSnapshot:
    if not is_safe_relative_path(package_relative):
        raise GitSourceError("coordinator package path is unsafe")
    exact_commit = _canonical_commit(commit)
    commit_blobs = _tree_blob_ids(root, package_relative, exact_commit)
    image_paths = _package_image_paths(loaded_package, package_relative)
    if tuple(commit_blobs) != image_paths:
        raise GitSourceError("loaded coordinator package inventory differs from its commit")
    total_bytes = 0
    package_prefix = f"{package_relative}/"
    for path in image_paths:
        content = _read_exact_regular(
            loaded_package,
            path.removeprefix(package_prefix),
            require_nonempty=False,
            reject_nul=False,
        )
        total_bytes += len(content)
        if total_bytes > _MAX_PACKAGE_BYTES:
            raise GitSourceError("coordinator package exceeds its aggregate byte bound")
        if _git_blob_id(content) != commit_blobs[path]:
            raise GitSourceError("loaded coordinator package bytes differ from its commit")
    return GitSourceSnapshot(head=exact_commit, dirty_paths=(), source_kind="commit")


def repository_root(value: Path) -> Path:
    root = value.expanduser().resolve()
    if not root.is_dir():
        raise GitSourceError("repository root is unavailable")
    discovered = _git(root, ("rev-parse", "--path-format=absolute", "--show-toplevel"))
    try:
        git_root = Path(discovered.decode("utf-8").strip()).resolve()
    except UnicodeDecodeError as error:
        raise GitSourceError("repository root is not valid UTF-8") from error
    if git_root != root:
        raise GitSourceError("path is not the exact Git repository root")
    return root


def require_loaded_source(expected_package: Path, loaded_package: Path) -> None:
    try:
        matching = loaded_package.samefile(expected_package)
    except OSError as error:
        raise GitSourceError("loaded coordinator source cannot be identified") from error
    if not matching:
        raise GitSourceError("loaded Python package is outside the exact source image")


def relative_path(root: Path, value: Path) -> str:
    path = value if value.is_absolute() else root / value
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as error:
        raise GitSourceError("profile path is outside target repository") from error
    if not is_safe_relative_path(relative):
        raise GitSourceError("profile path is unsafe")
    return relative


def require_directory(root: Path, relative: str) -> None:
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise GitSourceError("target artifacts directory is unavailable") from error
    if not resolved.is_dir() or resolved.parent == resolved:
        raise GitSourceError("target artifacts directory is invalid")


def read_regular(root: Path, relative: str) -> bytes:
    return _read_exact_regular(root, relative, require_nonempty=True, reject_nul=True)


def read_opaque_regular(root: Path, relative: str) -> bytes:
    return _read_exact_regular(root, relative, require_nonempty=False, reject_nul=False)


def _read_exact_regular(
    root: Path,
    relative: str,
    *,
    require_nonempty: bool,
    reject_nul: bool,
) -> bytes:
    if not is_safe_relative_path(relative):
        raise GitSourceError("consumer contract path is unsafe")
    descriptors: list[int] = []
    try:
        directory = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        descriptors.append(directory)
        components = relative.split("/")
        for component in components[:-1]:
            directory = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            descriptors.append(directory)
        file_descriptor = os.open(
            components[-1],
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory,
        )
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise GitSourceError("consumer contract path is not a regular file")
        minimum_size = 1 if require_nonempty else 0
        if not minimum_size <= before.st_size <= _MAX_FILE_BYTES:
            raise GitSourceError("consumer contract file is outside its byte bound")
        content = bytearray()
        while len(content) <= _MAX_FILE_BYTES:
            chunk = os.read(
                file_descriptor,
                min(65_536, _MAX_FILE_BYTES + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(file_descriptor)
    except OSError as error:
        raise GitSourceError(
            f"consumer contract file is unavailable or traverses a symbolic link: {relative}"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise GitSourceError("consumer contract file changed while being read")
    if len(content) != before.st_size or (reject_nul and b"\x00" in content):
        raise GitSourceError("consumer contract file bytes are invalid")
    return bytes(content)


def _git(root: Path, arguments: tuple[str, ...]) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise GitSourceError("Git executable is unavailable")
    try:
        completed = run_bounded(
            executable,
            ("-C", str(root), *arguments),
            cwd=root,
            max_output_bytes=_MAX_GIT_OUTPUT_BYTES,
            timeout_seconds=_GIT_TIMEOUT_SECONDS,
            env={
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": str(root),
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except (RuntimeError, TypeError, ValueError) as error:
        raise GitSourceError("bounded Git inspection failed") from error
    if completed.status != 0 or completed.error is not None:
        raise GitSourceError("Git inspection rejected the repository")
    return completed.stdout


def _package_image_paths(package: Path, package_relative: str) -> tuple[str, ...]:
    try:
        resolved = package.resolve(strict=True)
        if resolved != package or not resolved.is_dir():
            raise GitSourceError("coordinator package directory is invalid")
        paths: list[str] = []
        entry_count = 0
        for directory, raw_directories, raw_files in os.walk(
            package,
            topdown=True,
            followlinks=False,
            onerror=_raise_walk_error,
        ):
            raw_directories.sort()
            raw_files.sort()
            admitted_directories: list[str] = []
            for name in raw_directories:
                entry_count += 1
                candidate = Path(directory) / name
                mode = candidate.lstat().st_mode
                if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                    raise GitSourceError("coordinator package contains a special directory")
                if name == "__pycache__":
                    raise GitSourceError("loaded coordinator package contains Python cache files")
                admitted_directories.append(name)
            raw_directories[:] = admitted_directories
            for name in raw_files:
                entry_count += 1
                candidate = Path(directory) / name
                mode = candidate.lstat().st_mode
                if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                    raise GitSourceError("coordinator package contains a special file")
                relative = f"{package_relative}/{candidate.relative_to(package).as_posix()}"
                if not is_safe_relative_path(relative):
                    raise GitSourceError("coordinator package path is unsafe")
                if name.endswith((".pyc", ".pyo")):
                    raise GitSourceError("loaded coordinator package contains Python cache files")
                paths.append(relative)
            if entry_count > _MAX_PACKAGE_ENTRIES:
                raise GitSourceError("coordinator package exceeds its entry bound")
    except OSError as error:
        raise GitSourceError("coordinator package inventory is unavailable") from error
    result = tuple(sorted(paths, key=utf16_sort_key))
    if not result or len(result) > _MAX_PACKAGE_ENTRIES:
        raise GitSourceError("coordinator package file inventory is invalid")
    return result


def _raise_walk_error(error: OSError) -> None:
    raise error


def _tree_blob_ids(root: Path, package_relative: str, commit: str) -> dict[str, str]:
    content = _git(
        root,
        (
            "--literal-pathspecs",
            "ls-tree",
            "-rz",
            "--full-tree",
            commit,
            "--",
            package_relative,
        ),
    )
    result = _parse_tree_blob_ids(content, require_regular=True)
    if not result or len(result) > _MAX_PACKAGE_ENTRIES:
        raise GitSourceError("coordinator commit package inventory is invalid")
    return result


def _head_blob_ids(root: Path, commit: str, paths: tuple[str, ...]) -> dict[str, str]:
    content = _git(
        root,
        ("--literal-pathspecs", "ls-tree", "-rz", "--full-tree", commit, "--", *paths),
    )
    return _parse_tree_blob_ids(content, require_regular=False)


def _parse_tree_blob_ids(content: bytes, *, require_regular: bool) -> dict[str, str]:
    if not content:
        return {}
    if not content.endswith(b"\x00"):
        raise GitSourceError("Git tree output is not NUL terminated")
    result: dict[str, str] = {}
    for item in content[:-1].split(b"\x00"):
        metadata, separator, raw_path = item.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3:
            raise GitSourceError("Git tree entry is malformed")
        mode, object_type, raw_object_id = fields
        try:
            path = raw_path.decode("utf-8")
            object_id = raw_object_id.decode("ascii")
        except UnicodeDecodeError as error:
            raise GitSourceError("Git tree entry encoding is invalid") from error
        if (
            path in result
            or not is_safe_relative_path(path)
            or len(object_id) != 40
            or any(character not in "0123456789abcdef" for character in object_id)
        ):
            raise GitSourceError("Git tree entry identity is invalid")
        if object_type == b"blob" and mode in {b"100644", b"100755"}:
            result[path] = object_id
        elif require_regular:
            raise GitSourceError("coordinator HEAD package contains a special object")
    return {path: result[path] for path in sorted(result, key=utf16_sort_key)}


def _git_blob_id(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _commit_id(content: bytes) -> str:
    try:
        value = content.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise GitSourceError("repository commit is not ASCII") from error
    return _canonical_commit(value)


def _canonical_commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise GitSourceError("repository commit is not a canonical SHA-1 object id")
    return value
