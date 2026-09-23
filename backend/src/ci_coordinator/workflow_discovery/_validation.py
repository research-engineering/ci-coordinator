"""Shared bounded-value validation for workflow discovery contracts."""

from __future__ import annotations

import re
from typing import Final

from ci_coordinator.kernel import utf16_sort_key

MAX_TEXT_BYTES: Final = 4_096
MAX_WORKFLOW_PATH_BYTES: Final = 512
MAX_WORKFLOW_FILES: Final = 64
MAX_WORKFLOW_FILE_BYTES: Final = 262_144
MAX_WORKFLOW_AGGREGATE_BYTES: Final = 4_194_304
MAX_REPORT_BYTES: Final = 1_572_864
MAX_REPORT_FACTS: Final = 32_768
MAX_REPORT_UNKNOWNS: Final = 32_768
MAX_CALL_EDGES: Final = 4_096
MAX_REPORT_JOBS: Final = 1_024

_FULL_SHA1 = re.compile(r"^[0-9a-f]{40}$")


def require_text(
    value: object,
    name: str,
    *,
    maximum_bytes: int,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise ValueError(f"{name} must be bounded Unicode scalar text")
    if not allow_empty and value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must contain only Unicode scalar values") from error
    if size > maximum_bytes:
        raise ValueError(f"{name} exceeds its byte bound")
    return value


def require_workflow_path(value: object) -> str:
    path = require_text(value, "workflow path", maximum_bytes=MAX_WORKFLOW_PATH_BYTES)
    prefix = ".github/workflows/"
    if (
        not path.startswith(prefix)
        or "/" in path[len(prefix) :]
        or not path.endswith((".yml", ".yaml"))
        or "\\" in path
        or ".." in path.split("/")
    ):
        raise ValueError("workflow path must identify a direct workflow-directory YAML file")
    return path


def require_sha1(value: object, name: str) -> str:
    if type(value) is not str or _FULL_SHA1.fullmatch(value) is None:
        raise ValueError(f"{name} must be exactly 40 lowercase hexadecimal characters")
    return value


def is_full_sha1(value: object) -> bool:
    return type(value) is str and _FULL_SHA1.fullmatch(value) is not None


def require_sha256(value: object, name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be lowercase SHA-256 hexadecimal")
    return value


def require_identifier(value: object, prefix: str, name: str) -> str:
    if (
        type(value) is not str
        or not value.startswith(prefix)
        or len(value) != len(prefix) + 32
        or any(character not in "0123456789abcdef" for character in value[len(prefix) :])
    ):
        raise ValueError(f"{name} must be a canonical content-derived identifier")
    return value


def require_exact_tuple[T](values: object, item_type: type[T], name: str) -> tuple[T, ...]:
    if type(values) is not tuple or any(type(item) is not item_type for item in values):
        raise TypeError(f"{name} must be an exact tuple of {item_type.__name__}")
    return values


def require_canonical_text_tuple(
    values: tuple[str, ...],
    name: str,
    *,
    allow_empty: bool,
    maximum_bytes: int = MAX_TEXT_BYTES,
) -> None:
    if type(values) is not tuple or (not allow_empty and not values):
        raise ValueError(f"{name} must be a canonical tuple")
    for value in values:
        require_text(value, name, maximum_bytes=maximum_bytes)
    if values != tuple(sorted(set(values), key=utf16_sort_key)):
        raise ValueError(f"{name} must be sorted and unique")
