from __future__ import annotations

from datetime import UTC, datetime
from typing import cast


def _timestamp(value: object) -> datetime:
    text = _text(value)
    if not text.endswith("Z"):
        raise ValueError("production admission timestamp must be UTC")
    parsed = datetime.fromisoformat(text)
    if parsed.microsecond % 1_000 != 0 or _render_timestamp(parsed) != text:
        raise ValueError("production admission timestamp is noncanonical")
    return parsed


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("production admission object shape is invalid")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 4_096:
        raise ValueError("production admission text is invalid")
    return value


def _positive_integer(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 9_007_199_254_740_991:
        raise ValueError("production admission integer is invalid")
    return value


def _nonnegative_integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
        raise ValueError("production admission integer is invalid")
    return value
