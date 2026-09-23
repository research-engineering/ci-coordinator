"""Bounded reads of the immutable database compatibility declaration chain."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence._compatibility_row_codec import (
    capability_from_row,
    revision_declaration_from_row,
)
from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    CompatibilityContractError,
    RevisionDeclaration,
    capabilities_cover,
    required_capabilities,
    validate_successor,
)
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.errors import (
    DatabaseCapabilityUnavailable,
    DatabaseCompatibilityError,
)
from ci_coordinator.persistence.schema import (
    compatibility_capabilities,
    compatibility_declarations,
)


@dataclass(frozen=True, slots=True)
class CurrentCompatibility:
    declaration: RevisionDeclaration
    parent: RevisionDeclaration | None


async def admit_current_capabilities(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
    required: tuple[CapabilityDeclaration, ...],
) -> CurrentCompatibility:
    """Read exactly the current declaration chain and prove required coverage."""
    required_set = required_capabilities(profile, required)
    current = await load_current_compatibility(connection, profile)
    if not capabilities_cover(required_set, current.declaration.capabilities):
        raise DatabaseCapabilityUnavailable(
            "current database declaration does not provide every required capability"
        )
    return current


async def load_current_compatibility(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
) -> CurrentCompatibility:
    """Read at most the current declaration and its direct parent under a fence."""
    revision_id = await _current_alembic_revision(connection)
    current = await _load_declaration(connection, profile, revision_id)
    parent = (
        None
        if current.parent_revision_id is None
        else await _load_declaration(connection, profile, current.parent_revision_id)
    )
    try:
        validate_successor(profile, parent, current)
    except CompatibilityContractError as error:
        raise DatabaseCompatibilityError(
            "database compatibility declaration chain is invalid"
        ) from error
    return CurrentCompatibility(declaration=current, parent=parent)


async def _current_alembic_revision(connection: AsyncConnection) -> str:
    try:
        result = await connection.execute(
            text("SELECT version_num FROM public.alembic_version ORDER BY version_num LIMIT 2")
        )
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError("could not read the current Alembic revision") from error
    revisions = tuple(row[0] for row in result)
    if len(revisions) != 1 or type(revisions[0]) is not str:
        raise DatabaseCompatibilityError("database must expose exactly one Alembic revision")
    return revisions[0]


async def _load_declaration(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
    revision_id: str,
) -> RevisionDeclaration:
    try:
        result = await connection.execute(
            select(compatibility_declarations).where(
                compatibility_declarations.c.revision_id == revision_id
            )
        )
        declaration_row = result.mappings().one_or_none()
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not read a database compatibility declaration"
        ) from error
    if declaration_row is None:
        raise DatabaseCapabilityUnavailable(
            "current Alembic revision has no compatibility declaration"
        )
    capabilities = await _load_capabilities(connection, profile, revision_id)
    try:
        return revision_declaration_from_row(declaration_row, capabilities)
    except CompatibilityContractError as error:
        raise DatabaseCompatibilityError(
            "database compatibility declaration violates its immutable contract"
        ) from error


async def _load_capabilities(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
    revision_id: str,
) -> tuple[CapabilityDeclaration, ...]:
    try:
        result = await connection.execute(
            select(
                compatibility_capabilities.c.capability_id,
                compatibility_capabilities.c.descriptor_hash,
            )
            .where(compatibility_capabilities.c.revision_id == revision_id)
            .order_by(compatibility_capabilities.c.capability_id)
            .limit(profile.capability_read_limit)
        )
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not read database compatibility capabilities"
        ) from error
    rows = tuple(result.mappings())
    if len(rows) == profile.capability_read_limit:
        raise DatabaseCompatibilityError("database compatibility capability bound is exceeded")
    try:
        return tuple(capability_from_row(row) for row in rows)
    except CompatibilityContractError as error:
        raise DatabaseCompatibilityError(
            "database compatibility capabilities violate their immutable contract"
        ) from error
