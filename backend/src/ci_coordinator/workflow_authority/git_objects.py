"""Exact SHA-1 Git blob and tree object construction."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha1
from typing import Protocol


class TreeEntry(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def mode(self) -> str: ...

    @property
    def object_type(self) -> str: ...

    @property
    def object_id(self) -> str: ...


def git_blob_oid(content: bytes) -> str:
    if type(content) is not bytes:
        raise TypeError("Git blob content must be exact bytes")
    return _sha1_oid("blob", content)


def git_tree_content(entries: Sequence[TreeEntry]) -> bytes:
    chunks: list[bytes] = []
    for entry in sorted(entries, key=git_tree_sort_key):
        mode = b"40000" if entry.mode == "040000" else entry.mode.encode("ascii")
        name = entry.name.encode("utf-8", errors="strict")
        chunks.extend((mode, b" ", name, b"\0", bytes.fromhex(entry.object_id)))
    return b"".join(chunks)


def git_tree_oid(entries: Sequence[TreeEntry]) -> str:
    return _sha1_oid("tree", git_tree_content(entries))


def git_tree_sort_key(entry: TreeEntry) -> bytes:
    suffix = b"/" if entry.object_type == "tree" else b"\0"
    return entry.name.encode("utf-8", errors="strict") + suffix


def _sha1_oid(object_type: str, content: bytes) -> str:
    header = f"{object_type} {len(content)}\0".encode("ascii")
    return sha1(header + content, usedforsecurity=False).hexdigest()
