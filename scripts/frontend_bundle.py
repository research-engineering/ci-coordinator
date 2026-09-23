"""Produce and verify the canonical operator-UI asset manifest."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Final, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIRECTORY = REPO_ROOT / "frontend" / "dist"
ASSET_MANIFEST_NAME: Final = "asset-manifest.v1.json"
ASSET_MANIFEST_SCHEMA: Final = "ci-coordinator-operator-ui-assets/v1"
_CONTENT_TYPES: Final[Mapping[str, str]] = {
    ".avif": "image/avif",
    ".css": "text/css; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
_TEXT_SUFFIXES: Final = frozenset({".css", ".html", ".js", ".json", ".svg"})
_FORBIDDEN_MARKERS: Final = (
    "BEGIN PRIVATE KEY",
    "CI_COORDINATOR_GITHUB_PRIVATE_KEY",
    "CI_COORDINATOR_MIGRATION_DATABASE_DSN",
    "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN",
    "proxyRequestIsAdmitted",
    "workbenchFixture",
)
_MAX_ASSET_COUNT: Final = 1_000
_MAX_ASSET_BYTES: Final = 8 * 1024 * 1024
_MAX_INDEX_BYTES: Final = 1 * 1024 * 1024
_MAX_BUNDLE_BYTES: Final = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES: Final = 1 * 1024 * 1024
_MAX_ASSET_PATH_BYTES: Final = 1_024
_MAX_ASSET_PATH_PARTS: Final = 16
_MAX_FILESYSTEM_ENTRIES: Final = _MAX_ASSET_COUNT * _MAX_ASSET_PATH_PARTS + 1
_RESERVED_ASSET_NAMESPACE: Final = "_bundle"
_SAFE_ASSET_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,255}")


class BundleAdmissionError(ValueError):
    """The browser bundle escaped its admitted production surface."""


def write_bundle_manifest(root: Path = DIST_DIRECTORY) -> None:
    """Write the only mutable build output, then verify the frozen projection."""
    root_fd = _open_bundle_root(root)
    try:
        files = _collect_payload_files(root_fd)
        manifest = {
            "files": [_manifest_entry(path, content) for path, content in files],
            "schemaVersion": ASSET_MANIFEST_SCHEMA,
        }
        encoded = _canonical_json(manifest)
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise BundleAdmissionError("production asset manifest exceeded its byte bound")
        _write_manifest(root_fd, encoded)
        _verify_bundle(root_fd)
    finally:
        os.close(root_fd)


def verify_bundle(root: Path = DIST_DIRECTORY) -> None:
    """Verify the complete bundle against one canonical exact-byte manifest."""
    root_fd = _open_bundle_root(root)
    try:
        _verify_bundle(root_fd)
    finally:
        os.close(root_fd)


def _verify_bundle(root_fd: int) -> None:
    manifest_bytes = _read_regular_file(
        root_fd,
        PurePosixPath(ASSET_MANIFEST_NAME),
        _MAX_MANIFEST_BYTES,
    )
    manifest = _decode_manifest(manifest_bytes)
    if _canonical_json(manifest) != manifest_bytes:
        raise BundleAdmissionError("production asset manifest is not canonical")
    payload = dict(_collect_payload_files(root_fd))
    entries = cast("list[dict[str, object]]", manifest["files"])
    expected_paths = tuple(entry["path"] for entry in entries)
    if tuple(payload) != expected_paths:
        raise BundleAdmissionError("production asset manifest does not cover the exact bundle")
    for entry in entries:
        path = cast("str", entry["path"])
        content = payload[path]
        if entry != _manifest_entry(path, content):
            raise BundleAdmissionError("production asset identity does not match the manifest")


def _collect_payload_files(root_fd: int) -> tuple[tuple[str, bytes], ...]:
    inventory = _payload_inventory(root_fd)
    paths_by_name = {relative for relative, _size in inventory}
    total_bytes = 0
    for relative, size in inventory:
        maximum_bytes = _MAX_INDEX_BYTES if relative == "index.html" else _MAX_ASSET_BYTES
        if not 0 <= size <= maximum_bytes:
            raise BundleAdmissionError("production asset exceeded the admitted byte bound")
        total_bytes += size
    if (
        not inventory
        or len(inventory) > _MAX_ASSET_COUNT
        or total_bytes > _MAX_BUNDLE_BYTES
        or "index.html" not in paths_by_name
        or not any(path.endswith(".js") and path.startswith("assets/") for path in paths_by_name)
    ):
        raise BundleAdmissionError("production bundle shape exceeded the admitted bound")
    files: list[tuple[str, bytes]] = []
    for relative, expected_size in inventory:
        content = _read_regular_file(root_fd, PurePosixPath(relative), expected_size)
        if len(content) != expected_size:
            raise BundleAdmissionError("production bundle changed before it was read")
        _admit_payload_content(relative, content)
        files.append((relative, content))
    if _payload_inventory(root_fd) != inventory:
        raise BundleAdmissionError("production bundle changed while it was read")
    return tuple(files)


def _payload_inventory(root_fd: int) -> tuple[tuple[str, int], ...]:
    inventory: list[tuple[str, int]] = []
    _walk_payload_inventory(root_fd, (), inventory, 0)
    return tuple(sorted(inventory))


def _walk_payload_inventory(
    current_fd: int,
    prefix: tuple[str, ...],
    inventory: list[tuple[str, int]],
    observed_entries: int,
) -> int:
    try:
        entries = os.scandir(current_fd)
    except OSError as error:
        raise BundleAdmissionError("production bundle is unavailable") from error
    with entries:
        for entry in entries:
            observed_entries += 1
            if observed_entries > _MAX_FILESYSTEM_ENTRIES:
                raise BundleAdmissionError("production bundle shape exceeded the admitted bound")
            try:
                status = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise BundleAdmissionError(
                    "production bundle contains an unreadable path"
                ) from error
            if stat.S_ISLNK(status.st_mode):
                raise BundleAdmissionError("production bundle contains a symbolic link")
            relative_parts = (*prefix, entry.name)
            if len(relative_parts) > _MAX_ASSET_PATH_PARTS:
                raise BundleAdmissionError("production bundle shape exceeded the admitted bound")
            if stat.S_ISDIR(status.st_mode):
                child_fd = _open_directory_entry(current_fd, entry.name, status)
                try:
                    observed_entries = _walk_payload_inventory(
                        child_fd,
                        relative_parts,
                        inventory,
                        observed_entries,
                    )
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(status.st_mode):
                raise BundleAdmissionError("production bundle contains a non-regular file")
            relative = PurePosixPath(*relative_parts).as_posix()
            if relative == ASSET_MANIFEST_NAME:
                continue
            _admit_payload_path(relative)
            inventory.append((relative, status.st_size))
    return observed_entries


def _admit_payload_path(value: str) -> None:
    path = PurePosixPath(value)
    if value == "index.html":
        return
    if (
        path.is_absolute()
        or len(path.parts) < 2
        or len(path.parts) > _MAX_ASSET_PATH_PARTS
        or len(value.encode("utf-8")) > _MAX_ASSET_PATH_BYTES
        or path.parts[0] != "assets"
        or path.parts[1] == _RESERVED_ASSET_NAMESPACE
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(_SAFE_ASSET_SEGMENT.fullmatch(part) is None for part in path.parts)
        or path.suffix == ".map"
    ):
        message = (
            "production bundle contains a source map"
            if path.suffix == ".map"
            else "production bundle contains an unsupported path"
        )
        raise BundleAdmissionError(message)
    _content_type_for(value)


def _admit_payload_content(path: str, content: bytes) -> None:
    if path == "index.html" and not content:
        raise BundleAdmissionError("production bundle index is empty")
    suffix = PurePosixPath(path).suffix
    if suffix not in _TEXT_SUFFIXES:
        return
    try:
        source = content.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise BundleAdmissionError("production asset is not valid UTF-8 text") from error
    if any(marker in source for marker in _FORBIDDEN_MARKERS):
        raise BundleAdmissionError("production asset contains a forbidden marker")


def _decode_manifest(content: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleAdmissionError("production asset manifest is invalid JSON") from error
    if type(decoded) is not dict or set(decoded) != {"files", "schemaVersion"}:
        raise BundleAdmissionError("production asset manifest has an invalid shape")
    if decoded["schemaVersion"] != ASSET_MANIFEST_SCHEMA or type(decoded["files"]) is not list:
        raise BundleAdmissionError("production asset manifest has an invalid contract")
    entries = cast("list[object]", decoded["files"])
    if not 1 <= len(entries) <= _MAX_ASSET_COUNT:
        raise BundleAdmissionError("production asset manifest has an invalid cardinality")
    previous_path = ""
    total_bytes = 0
    for entry in entries:
        if type(entry) is not dict or set(entry) != {
            "contentType",
            "path",
            "sha256",
            "sizeBytes",
        }:
            raise BundleAdmissionError("production asset manifest entry has an invalid shape")
        path = entry["path"]
        digest = entry["sha256"]
        size = entry["sizeBytes"]
        content_type = entry["contentType"]
        if (
            type(path) is not str
            or path <= previous_path
            or type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or type(size) is not int
            or not 0 <= size <= (_MAX_INDEX_BYTES if path == "index.html" else _MAX_ASSET_BYTES)
            or type(content_type) is not str
        ):
            raise BundleAdmissionError("production asset manifest entry is invalid")
        _admit_payload_path(path)
        if content_type != _content_type_for(path):
            raise BundleAdmissionError("production asset media type is invalid")
        if path == "index.html" and size == 0:
            raise BundleAdmissionError("production asset manifest entry is invalid")
        total_bytes += size
        previous_path = path
    if total_bytes > _MAX_BUNDLE_BYTES:
        raise BundleAdmissionError("production bundle shape exceeded the admitted bound")
    return decoded


def _manifest_entry(path: str, content: bytes) -> dict[str, object]:
    return {
        "contentType": _content_type_for(path),
        "path": path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "sizeBytes": len(content),
    }


def _content_type_for(path: str) -> str:
    if path == "index.html":
        return "text/html; charset=utf-8"
    suffix = PurePosixPath(path).suffix
    content_type = _CONTENT_TYPES.get(suffix)
    if content_type is None:
        raise BundleAdmissionError("production asset media type is unsupported")
    return content_type


def _read_regular_file(
    root_fd: int,
    relative_path: PurePosixPath,
    maximum_bytes: int,
) -> bytes:
    descriptor = _open_relative_entry(root_fd, relative_path)
    try:
        before = os.fstat(descriptor)
        if not 0 <= before.st_size <= maximum_bytes:
            raise BundleAdmissionError("production asset exceeded the admitted byte bound")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
    except BundleAdmissionError:
        raise
    except OSError as error:
        raise BundleAdmissionError("production bundle contains an unreadable file") from error
    finally:
        os.close(descriptor)
    final_descriptor = _open_relative_entry(root_fd, relative_path)
    try:
        try:
            final_path = os.fstat(final_descriptor)
        except OSError as error:
            raise BundleAdmissionError("production bundle contains an unreadable file") from error
    finally:
        os.close(final_descriptor)
    if len(content) > maximum_bytes:
        raise BundleAdmissionError("production asset exceeded the admitted byte bound")
    if (
        _file_identity(before) != _file_identity(after)
        or _file_identity(after) != _file_identity(final_path)
        or len(content) != before.st_size
    ):
        raise BundleAdmissionError("production bundle changed while it was read")
    return content


def _write_manifest(root_fd: int, content: bytes) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise BundleAdmissionError("production bundle no-follow admission is unavailable")
    descriptor = -1
    temporary_name: str | None = None
    try:
        for _attempt in range(16):
            temporary_name = f".{ASSET_MANIFEST_NAME}.{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | getattr(os, "O_CLOEXEC", 0),
                    0o644,
                    dir_fd=root_fd,
                )
            except FileExistsError:
                continue
            break
        if descriptor < 0 or temporary_name is None:
            raise BundleAdmissionError("production asset manifest staging is unavailable")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise BundleAdmissionError("production asset manifest write did not progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            ASSET_MANIFEST_NAME,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        temporary_name = None
        os.fsync(root_fd)
    except BundleAdmissionError:
        raise
    except OSError as error:
        raise BundleAdmissionError("production asset manifest is not writable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=root_fd)


def _open_bundle_root(root: Path) -> int:
    if not isinstance(root, Path):
        raise TypeError("production bundle root must be a Path")
    try:
        observed = root.lstat()
    except OSError as error:
        raise BundleAdmissionError("production bundle is unavailable") from error
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise BundleAdmissionError("production bundle root has an invalid filesystem type")
    descriptor = -1
    try:
        descriptor = os.open(root, _open_flags(directory=True))
        actual = os.fstat(descriptor)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise BundleAdmissionError("production bundle is unavailable") from error
    if _file_identity(observed) != _file_identity(actual):
        os.close(descriptor)
        raise BundleAdmissionError("production bundle root changed during admission")
    return descriptor


def _open_directory_entry(
    parent_fd: int,
    name: str,
    observed: os.stat_result,
) -> int:
    return _open_admitted_entry(parent_fd, name, observed, directory=True)


def _open_relative_entry(root_fd: int, relative_path: PurePosixPath) -> int:
    parts = relative_path.parts
    if relative_path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise BundleAdmissionError("production bundle file path is invalid")
    current_fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            is_last = index == len(parts) - 1
            try:
                observed = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except OSError as error:
                raise BundleAdmissionError(
                    "production bundle contains an unreadable file"
                ) from error
            if stat.S_ISLNK(observed.st_mode):
                raise BundleAdmissionError("production bundle contains a symbolic link")
            if is_last:
                if not stat.S_ISREG(observed.st_mode):
                    raise BundleAdmissionError(
                        "production bundle file has an invalid filesystem type"
                    )
            elif not stat.S_ISDIR(observed.st_mode):
                raise BundleAdmissionError("production bundle path traverses a non-directory")
            next_fd = _open_admitted_entry(
                current_fd,
                part,
                observed,
                directory=not is_last,
            )
            previous_fd = current_fd
            current_fd = next_fd
            os.close(previous_fd)
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _open_admitted_entry(
    parent_fd: int,
    name: str,
    observed: os.stat_result,
    *,
    directory: bool,
) -> int:
    descriptor = -1
    try:
        descriptor = os.open(name, _open_flags(directory=directory), dir_fd=parent_fd)
        actual = os.fstat(descriptor)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise BundleAdmissionError("production bundle changed during admission") from error
    if _file_identity(observed) != _file_identity(actual):
        os.close(descriptor)
        raise BundleAdmissionError("production bundle changed during admission")
    return descriptor


def _open_flags(*, directory: bool) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if (
        nofollow is None
        or directory_flag is None
        or nonblock is None
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        raise BundleAdmissionError(
            "production bundle descriptor-relative no-follow admission is unavailable"
        )
    flags = (
        os.O_RDONLY
        | cast(int, nofollow)
        | cast(int, getattr(os, "O_CLOEXEC", 0))
        | cast(int, nonblock)
    )
    return flags | cast(int, directory_flag) if directory else flags


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("ascii")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    try:
        write_manifest = bool(arguments and arguments[0] == "--write-manifest")
        remaining = arguments[1:] if write_manifest else arguments
        if len(remaining) > 1:
            raise BundleAdmissionError("frontend bundle accepts one optional root path")
        root = Path(remaining[0]) if remaining else DIST_DIRECTORY
        if write_manifest:
            write_bundle_manifest(root)
        else:
            verify_bundle(root)
    except BundleAdmissionError:
        print(json.dumps({"code": "frontend_bundle_rejected"}), file=sys.stderr)
        return 2
    print(json.dumps({"assetBoundary": "manifest-verified", "state": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
