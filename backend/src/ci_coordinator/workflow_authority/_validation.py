from __future__ import annotations

import re

from .limits import MAX_PATH_BYTES

_OID = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def require_component(value: object) -> None:
    require_text(value, "Git tree entry name", maximum_bytes=255)
    if not isinstance(value, str) or value in {".", ".."} or "/" in value or "\0" in value:
        raise ValueError("Git tree entry name must be one safe path component")


def require_path(value: object, *, allow_root: bool = False) -> None:
    if value == "" and allow_root:
        return
    require_text(value, "repository path", maximum_bytes=MAX_PATH_BYTES)
    if not isinstance(value, str) or value.startswith("/") or value.endswith("/"):
        raise ValueError("repository path must be relative and canonical")
    if any(part in {"", ".", ".."} for part in value.split("/")) or "\0" in value:
        raise ValueError("repository path contains an unsafe component")


def require_text(value: object, label: str, *, maximum_bytes: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{label} must be bounded canonical Unicode scalar text")
    return value


def require_oid(value: object, label: str) -> str:
    if type(value) is not str or _OID.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-1 Git object id")
    return value


def require_sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value
