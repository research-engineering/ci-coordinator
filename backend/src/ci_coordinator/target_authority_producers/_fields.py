from __future__ import annotations

from collections.abc import Mapping

from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.target_authority_relation import (
    AuthorityField,
    AuthorityFieldEntry,
    TargetAuthorityRowFamily,
    row_field_names,
)


def authority_fields(
    family: TargetAuthorityRowFamily,
    values: Mapping[str, AuthorityField],
) -> tuple[AuthorityFieldEntry, ...]:
    expected = row_field_names(family)
    if set(values) != set(expected):
        raise ValueError("authority field projection does not close its family schema")
    return tuple(
        AuthorityFieldEntry(name, values[name]) for name in sorted(values, key=utf16_sort_key)
    )


def present(value: object) -> AuthorityField:
    return AuthorityField.present(value)


def not_applicable() -> AuthorityField:
    return AuthorityField.not_applicable()


def unknown(reason: str) -> AuthorityField:
    return AuthorityField.unknown(reason)
