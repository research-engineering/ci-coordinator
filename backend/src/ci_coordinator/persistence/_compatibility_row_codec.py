"""Pure decoding of database compatibility rows into immutable contracts."""

from __future__ import annotations

from typing import Literal, Protocol, cast

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    RevisionDeclaration,
)
from ci_coordinator.persistence.errors import DatabaseCompatibilityError


class _RowValues(Protocol):
    def __getitem__(self, key: str, /) -> object: ...


def revision_declaration_from_row(
    row: _RowValues,
    capabilities: tuple[CapabilityDeclaration, ...],
) -> RevisionDeclaration:
    return RevisionDeclaration(
        generation=_required_int(row, "generation"),
        lineage_id=_required_string(row, "lineage_id"),
        revision_id=_required_string(row, "revision_id"),
        parent_revision_id=_optional_string(row, "parent_revision_id"),
        transition_kind=_required_transition_kind(row),
        protocol_version=_required_int(row, "protocol_version"),
        capabilities=capabilities,
        declaration_hash=_required_string(row, "declaration_hash"),
    )


def capability_from_row(row: _RowValues) -> CapabilityDeclaration:
    return CapabilityDeclaration(
        capability_id=_required_string(row, "capability_id"),
        descriptor_hash=_required_string(row, "descriptor_hash"),
    )


def _required_string(row: _RowValues, key: str) -> str:
    value = row[key]
    if type(value) is not str:
        raise DatabaseCompatibilityError(f"database compatibility field {key} is invalid")
    return value


def _optional_string(row: _RowValues, key: str) -> str | None:
    value = row[key]
    if value is not None and type(value) is not str:
        raise DatabaseCompatibilityError(f"database compatibility field {key} is invalid")
    return value


def _required_int(row: _RowValues, key: str) -> int:
    value = row[key]
    if type(value) is not int:
        raise DatabaseCompatibilityError(f"database compatibility field {key} is invalid")
    return value


def _required_transition_kind(
    row: _RowValues,
) -> Literal["bootstrap", "expand", "contract"]:
    value = _required_string(row, "transition_kind")
    if value not in {"bootstrap", "expand", "contract"}:
        raise DatabaseCompatibilityError("database compatibility transition kind is invalid")
    return cast(Literal["bootstrap", "expand", "contract"], value)
