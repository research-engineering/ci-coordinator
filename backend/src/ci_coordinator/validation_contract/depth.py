"""Validation-depth ordering shared by planning and execution."""

from __future__ import annotations

from typing import Literal

type ValidationDepth = Literal["smoke", "targeted", "standard", "full", "exhaustive"]

VALIDATION_DEPTHS: tuple[ValidationDepth, ...] = (
    "smoke",
    "targeted",
    "standard",
    "full",
    "exhaustive",
)


def validation_depth(value: object, *, field_name: str = "validation depth") -> ValidationDepth:
    if type(value) is not str or value not in VALIDATION_DEPTHS:
        raise ValueError(f"{field_name} is not admitted")
    return value


def depth_rank(value: ValidationDepth) -> int:
    return VALIDATION_DEPTHS.index(validation_depth(value))


def max_depth(left: ValidationDepth, right: ValidationDepth) -> ValidationDepth:
    return left if depth_rank(left) >= depth_rank(right) else right
