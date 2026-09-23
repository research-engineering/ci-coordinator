from datetime import UTC, datetime

from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


def positive_id(value: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
        raise ValueError(f"{name} must be a positive safe integer")


def utc_time(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observation time must be an aware datetime")
    return value.astimezone(UTC)


def digest(value: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("observation digest must be lowercase SHA-256 hexadecimal")
