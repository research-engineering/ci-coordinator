from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

type JsonObject = dict[str, Any]


def read_json_object(path: Path) -> JsonObject:
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError(f"{path} is not valid JSON: {_json_error_message(error)}") from error
    return as_object(value, str(path))


def parse_json_object(source: str, context: str) -> JsonObject:
    try:
        value = _strict_json_loads(source)
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError(f"{context} did not emit JSON: {_json_error_message(error)}") from error
    return as_object(value, context)


def _strict_json_loads(source: str) -> object:
    value: object = json.loads(
        source,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
        parse_float=_finite_json_float,
    )
    _assert_unicode_scalars(value)
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("JSON contains a non-finite number")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON contains a non-finite number")
    return parsed


def _assert_unicode_scalars(value: object) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in current):
                raise ValueError("JSON contains an unpaired Unicode surrogate")
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _json_error_message(error: Exception) -> str:
    return error.msg if isinstance(error, json.JSONDecodeError) else str(error)


def as_object(value: object, context: str) -> JsonObject:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{context} must be an object")
    return cast(JsonObject, value)


def as_array(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be an array")
    return value


def js_json_dumps(value: object, *, indent: int | None = None) -> str:
    separators = (",", ":") if indent is None else None
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        indent=indent,
        separators=separators,
    )


def write_json(value: object) -> None:
    print(js_json_dumps(value, indent=2))


def safe_repo_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("repository path must be a non-empty string")
    path = value.replace("\\", "/")
    if path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"unsafe repository path: {value}")
    return path


def unique_sorted(values: list[str] | tuple[str, ...]) -> list[str]:
    return sorted(set(values))


def object_rows(value: object, label: str) -> list[dict[str, object]]:
    return [as_object(row, label) for row in as_array(value, label)]


def nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} fields differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def exact_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{context} keys must equal {js_json_dumps(sorted(expected))}; "
            f"observed {js_json_dumps(sorted(actual))}"
        )


def trimmed_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{context} must be non-empty trimmed text")
    return value


def nonempty_string_array(value: object, context: str) -> list[str]:
    raw = as_array(value, context)
    if not raw or any(not isinstance(item, str) or not item for item in raw):
        raise ValueError(f"{context} must be a non-empty string array")
    return [item for item in raw if isinstance(item, str)]
