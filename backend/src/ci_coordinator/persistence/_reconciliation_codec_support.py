"""Shared scalar admission for reconciliation row codecs."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from ci_coordinator.kernel import is_safe_json_integer
from ci_coordinator.persistence.canonical_row import CanonicalRowCodecError


class ReconciliationStateCodecError(CanonicalRowCodecError):
    pass


def _revision(value: object, context: str) -> int:
    if type(value) is not int or value < 0 or not is_safe_json_integer(value):
        raise ReconciliationStateCodecError(f"{context} must be a non-negative JSON safe integer")
    return value


def _positive_revision(value: object, context: str) -> int:
    if type(value) is not int or value < 1 or not is_safe_json_integer(value):
        raise ReconciliationStateCodecError(f"{context} must be a positive JSON safe integer")
    return value


def _positive(value: object, context: str) -> int:
    if type(value) is not int or value < 1 or not is_safe_json_integer(value):
        raise ReconciliationStateCodecError(f"{context} must be a positive JSON safe integer")
    return value


def _bounded_positive(value: object, maximum: int, context: str) -> int:
    parsed = _positive(value, context)
    if parsed > maximum:
        raise ReconciliationStateCodecError(f"{context} exceeds its admitted bound")
    return parsed


def _bounded_non_negative(value: object, maximum: int, context: str) -> int:
    parsed = _revision(value, context)
    if parsed > maximum:
        raise ReconciliationStateCodecError(f"{context} exceeds its admitted bound")
    return parsed


def _aware_datetime(value: object, context: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ReconciliationStateCodecError(f"{context} must be timezone-aware")
    return value


def _mapping(value: object, context: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ReconciliationStateCodecError(f"stored {context} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, context: str) -> str:
    if type(value) is not str or not value:
        raise ReconciliationStateCodecError(f"stored {context} must be non-empty text")
    return value


def _text_list(value: object, context: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        raise ReconciliationStateCodecError(f"stored {context} must be non-empty text values")
    return tuple(cast(list[str], value))
