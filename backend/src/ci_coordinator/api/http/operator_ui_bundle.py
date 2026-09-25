"""Bounded filesystem admission for the packaged operator UI bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, cast

from ci_coordinator.api.http.operator_ui_html import project_operator_ui_index

ASSET_BUNDLE_NAMESPACE: Final = "_bundle"
_ASSET_MANIFEST_NAME: Final = "asset-manifest.v1.json"
_ASSET_MANIFEST_SCHEMA: Final = "ci-coordinator-operator-ui-assets/v1"
_MAXIMUM_INDEX_BYTES: Final = 1_048_576
_MAXIMUM_ASSET_BYTES: Final = 8 * 1024 * 1024
_MAXIMUM_BUNDLE_BYTES: Final = 32 * 1024 * 1024
_MAXIMUM_ASSET_COUNT: Final = 1_000
_MAXIMUM_MANIFEST_BYTES: Final = 1_048_576
_MAXIMUM_ASSET_PATH_BYTES: Final = 1_024
_MAXIMUM_ASSET_PATH_PARTS: Final = 16
_MAXIMUM_FILESYSTEM_ENTRIES: Final = _MAXIMUM_ASSET_COUNT * _MAXIMUM_ASSET_PATH_PARTS + 1
_SAFE_ASSET_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,255}")
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


@dataclass(frozen=True, slots=True)
class VerifiedOperatorUiAsset:
    content: bytes
    content_type: str
    etag: str


@dataclass(frozen=True, slots=True)
class OperatorUiBundleSnapshot:
    index: bytes
    assets: tuple[tuple[str, VerifiedOperatorUiAsset], ...]
    bundle_id: str


def admit_operator_ui_bundle(directory: Path) -> OperatorUiBundleSnapshot:
    """Read one exact bundle into an immutable serving snapshot."""
    if not isinstance(directory, Path):
        raise TypeError("operator UI directory must be a Path")
    root_fd = _open_bundle_root(directory)
    try:
        manifest_content = _read_regular_file(
            root_fd,
            PurePosixPath(_ASSET_MANIFEST_NAME),
            _MAXIMUM_MANIFEST_BYTES,
            "asset manifest",
        )
        manifest = _decode_manifest(manifest_content)
        if _canonical_json(manifest) != manifest_content:
            raise ValueError("operator UI asset manifest is not canonical")
        entries = cast("list[dict[str, object]]", manifest["files"])
        expected_paths = tuple(entry["path"] for entry in entries)
        expected_sizes = tuple(cast("int", entry["sizeBytes"]) for entry in entries)
        actual_inventory = _actual_payload_inventory(root_fd)
        if tuple(path for path, _size in actual_inventory) != expected_paths:
            raise ValueError("operator UI asset manifest does not cover the exact bundle")
        if tuple(size for _path, size in actual_inventory) != expected_sizes:
            raise ValueError("operator UI asset identity does not match the manifest")
        index: bytes | None = None
        assets: list[tuple[str, VerifiedOperatorUiAsset]] = []
        for entry in entries:
            path = cast("str", entry["path"])
            content = _read_regular_file(
                root_fd,
                PurePosixPath(path),
                cast("int", entry["sizeBytes"]),
                "asset",
            )
            if (
                len(content) != entry["sizeBytes"]
                or hashlib.sha256(content).hexdigest() != entry["sha256"]
            ):
                raise ValueError("operator UI asset identity does not match the manifest")
            if path == "index.html":
                try:
                    content.decode("utf-8", errors="strict")
                except UnicodeError as error:
                    raise ValueError("operator UI index is not readable UTF-8") from error
                index = content
                continue
            relative_asset_path = PurePosixPath(path).relative_to("assets").as_posix()
            assets.append(
                (
                    relative_asset_path,
                    VerifiedOperatorUiAsset(
                        content=content,
                        content_type=str(entry["contentType"]),
                        etag='"' + str(entry["sha256"]) + '"',
                    ),
                )
            )
        if _actual_payload_inventory(root_fd) != actual_inventory:
            raise ValueError("operator UI bundle changed while it was read")
        if index is None or not assets:
            raise ValueError("operator UI bundle shape exceeded its bound")
        bundle_id = hashlib.sha256(manifest_content).hexdigest()
        projected_index = project_operator_ui_index(
            index,
            asset_paths=frozenset(path for path, _asset in assets),
            asset_prefix=f"/assets/{ASSET_BUNDLE_NAMESPACE}/{bundle_id}/",
            maximum_bytes=_MAXIMUM_INDEX_BYTES,
        )
        if len(projected_index) + sum(len(asset.content) for _path, asset in assets) > (
            _MAXIMUM_BUNDLE_BYTES
        ):
            raise ValueError("operator UI projected bundle exceeded its byte bound")
        return OperatorUiBundleSnapshot(
            index=projected_index,
            assets=tuple(assets),
            bundle_id=bundle_id,
        )
    finally:
        os.close(root_fd)


def _decode_manifest(content: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("operator UI asset manifest is invalid JSON") from error
    if type(decoded) is not dict or set(decoded) != {"files", "schemaVersion"}:
        raise ValueError("operator UI asset manifest has an invalid shape")
    if decoded["schemaVersion"] != _ASSET_MANIFEST_SCHEMA or type(decoded["files"]) is not list:
        raise ValueError("operator UI asset manifest has an invalid contract")
    entries = cast("list[object]", decoded["files"])
    if not 1 <= len(entries) <= _MAXIMUM_ASSET_COUNT:
        raise ValueError("operator UI asset manifest has an invalid cardinality")
    previous_path = ""
    total_bytes = 0
    for entry in entries:
        if type(entry) is not dict or set(entry) != {
            "contentType",
            "path",
            "sha256",
            "sizeBytes",
        }:
            raise ValueError("operator UI asset manifest entry has an invalid shape")
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
            or not 0
            <= size
            <= (_MAXIMUM_INDEX_BYTES if path == "index.html" else _MAXIMUM_ASSET_BYTES)
            or type(content_type) is not str
        ):
            raise ValueError("operator UI asset manifest entry is invalid")
        if content_type != _content_type_for(path):
            raise ValueError("operator UI asset media type is invalid")
        if path == "index.html" and size == 0:
            raise ValueError("operator UI index exceeded its byte bound")
        total_bytes += size
        previous_path = path
    if total_bytes > _MAXIMUM_BUNDLE_BYTES:
        raise ValueError("operator UI bundle shape exceeded its bound")
    return decoded


def _actual_payload_inventory(root_fd: int) -> tuple[tuple[str, int], ...]:
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
        raise ValueError("operator UI bundle is unreadable") from error
    with entries:
        for entry in entries:
            observed_entries += 1
            if observed_entries > _MAXIMUM_FILESYSTEM_ENTRIES:
                raise ValueError("operator UI bundle shape exceeded its bound")
            try:
                status = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ValueError("operator UI bundle contains an unreadable path") from error
            if stat.S_ISLNK(status.st_mode):
                raise ValueError("operator UI bundle contains a symbolic link")
            relative_parts = (*prefix, entry.name)
            if len(relative_parts) > _MAXIMUM_ASSET_PATH_PARTS:
                raise ValueError("operator UI bundle shape exceeded its bound")
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
                raise ValueError("operator UI bundle contains a non-regular file")
            relative = PurePosixPath(*relative_parts).as_posix()
            if relative != _ASSET_MANIFEST_NAME:
                inventory.append((relative, status.st_size))
    return observed_entries


def _content_type_for(path: str) -> str:
    if path == "index.html":
        return "text/html; charset=utf-8"
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or len(pure.parts) < 2
        or len(pure.parts) > _MAXIMUM_ASSET_PATH_PARTS
        or len(path.encode("utf-8")) > _MAXIMUM_ASSET_PATH_BYTES
        or pure.parts[0] != "assets"
        or pure.parts[1] == ASSET_BUNDLE_NAMESPACE
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(_SAFE_ASSET_SEGMENT.fullmatch(part) is None for part in pure.parts)
    ):
        raise ValueError("operator UI asset path is invalid")
    content_type = _CONTENT_TYPES.get(pure.suffix)
    if content_type is None:
        raise ValueError("operator UI asset media type is unsupported")
    return content_type


def _read_regular_file(
    root_fd: int,
    relative_path: PurePosixPath,
    maximum_bytes: int,
    label: str,
) -> bytes:
    descriptor = _open_relative_entry(root_fd, relative_path, label)
    try:
        before = os.fstat(descriptor)
        if not 0 <= before.st_size <= maximum_bytes:
            raise ValueError(f"operator UI {label} exceeded its byte bound")
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
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"operator UI {label} is unavailable") from error
    finally:
        os.close(descriptor)
    final_descriptor = _open_relative_entry(root_fd, relative_path, label)
    try:
        try:
            final_path = os.fstat(final_descriptor)
        except OSError as error:
            raise ValueError(f"operator UI {label} is unavailable") from error
    finally:
        os.close(final_descriptor)
    if len(content) > maximum_bytes:
        raise ValueError(f"operator UI {label} exceeded its byte bound")
    if (
        _file_identity(before) != _file_identity(after)
        or _file_identity(after) != _file_identity(final_path)
        or len(content) != before.st_size
    ):
        raise ValueError(f"operator UI {label} changed while it was read")
    return content


def _open_bundle_root(path: Path) -> int:
    try:
        observed = path.lstat()
    except OSError as error:
        raise ValueError("operator UI directory is unavailable") from error
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise ValueError("operator UI directory has an invalid filesystem type")
    descriptor = -1
    try:
        descriptor = os.open(path, _open_flags(directory=True))
        actual = os.fstat(descriptor)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise ValueError("operator UI directory is unavailable") from error
    if _file_identity(observed) != _file_identity(actual):
        os.close(descriptor)
        raise ValueError("operator UI directory changed during admission")
    return descriptor


def _open_directory_entry(
    parent_fd: int,
    name: str,
    observed: os.stat_result,
) -> int:
    return _open_admitted_entry(
        parent_fd,
        name,
        observed,
        directory=True,
        label="bundle",
    )


def _open_relative_entry(root_fd: int, relative_path: PurePosixPath, label: str) -> int:
    parts = relative_path.parts
    if relative_path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"operator UI {label} path is invalid")
    current_fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            is_last = index == len(parts) - 1
            try:
                observed = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except OSError as error:
                raise ValueError(f"operator UI {label} is unavailable") from error
            if stat.S_ISLNK(observed.st_mode):
                raise ValueError("operator UI bundle contains a symbolic link")
            if is_last:
                if not stat.S_ISREG(observed.st_mode):
                    raise ValueError(f"operator UI {label} has an invalid filesystem type")
            elif not stat.S_ISDIR(observed.st_mode):
                raise ValueError(f"operator UI {label} path traverses a non-directory")
            next_fd = _open_admitted_entry(
                current_fd,
                part,
                observed,
                directory=not is_last,
                label=label,
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
    label: str,
) -> int:
    descriptor = -1
    try:
        descriptor = os.open(name, _open_flags(directory=directory), dir_fd=parent_fd)
        actual = os.fstat(descriptor)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise ValueError(f"operator UI {label} changed during admission") from error
    if _file_identity(observed) != _file_identity(actual):
        os.close(descriptor)
        raise ValueError(f"operator UI {label} changed during admission")
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
        raise ValueError("operator UI descriptor-relative no-follow admission is unavailable")
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
