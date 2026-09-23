"""Shared primitive admission for untrusted GitHub response values."""

from __future__ import annotations

from typing import cast

from ci_coordinator.kernel import StrictJsonError, load_strict_json
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


def json_object_or_none(body: bytes) -> dict[str, object] | None:
    try:
        value = load_strict_json(body)
    except StrictJsonError:
        return None
    return object_or_none(value)


def object_or_none(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        return None
    return cast(dict[str, object], value)


def non_negative_safe_integer(value: object) -> int | None:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_JSON_INTEGER:
        return None
    return value


def positive_safe_integer(value: object) -> int | None:
    integer = non_negative_safe_integer(value)
    return integer if integer is not None and integer > 0 else None


def canonical_text_or_none(value: object, *, maximum_bytes: int) -> str | None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        return None
    return value


def repository_component_is_admitted(value: str) -> bool:
    return value not in {".", ".."} and "/" not in value and "\0" not in value
