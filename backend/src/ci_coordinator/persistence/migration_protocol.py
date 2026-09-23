"""Synchronous append-only declaration protocol for future Alembic revisions."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from sqlalchemy import insert, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from ci_coordinator.persistence._compatibility_row_codec import (
    capability_from_row,
    revision_declaration_from_row,
)
from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    CompatibilityContractError,
    RevisionDeclaration,
    assert_exact_replay,
    validate_successor,
)
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.errors import DatabaseCompatibilityError
from ci_coordinator.persistence.schema import (
    compatibility_capabilities,
    compatibility_declarations,
)


class ForwardDeclarationOutcome(Enum):
    APPENDED = "appended"
    REPLAY_VALIDATED = "replay_validated"


def apply_forward_declaration(
    connection: Connection,
    profile: CompatibilityProfile,
    *,
    previous_revision_id: str,
    proposed: RevisionDeclaration,
    attest_resulting_capabilities: Callable[[RevisionDeclaration], None],
) -> ForwardDeclarationOutcome:
    """Append one valid successor only after its caller-owned attestation passes."""
    actual_previous_revision_id = _current_alembic_revision(connection)
    if actual_previous_revision_id != previous_revision_id:
        raise DatabaseCompatibilityError(
            "current Alembic revision does not match the declared migration predecessor"
        )

    predecessor = _load_declaration(connection, profile, previous_revision_id)
    if predecessor is not None:
        _validate_stored_declaration(connection, profile, predecessor)
    existing = _load_declaration(connection, profile, proposed.revision_id)
    if existing is not None:
        assert_exact_replay(existing, proposed)
        _validate_forward_relation(profile, predecessor, proposed)
        attest_resulting_capabilities(proposed)
        return ForwardDeclarationOutcome.REPLAY_VALIDATED

    if predecessor is None:
        if _latest_declaration(connection, profile) is not None:
            raise DatabaseCompatibilityError(
                "a new declaration cannot follow an undeclared migration predecessor"
            )
    else:
        if _latest_declaration(connection, profile) != predecessor:
            raise DatabaseCompatibilityError(
                "a new declaration cannot fork retained compatibility history"
            )
    _validate_forward_relation(profile, predecessor, proposed)
    attest_resulting_capabilities(proposed)
    _insert_declaration(connection, proposed)
    return ForwardDeclarationOutcome.APPENDED


def validate_pre_retention_downgrade(
    connection: Connection,
    profile: CompatibilityProfile,
    *,
    target: RevisionDeclaration,
) -> None:
    """Validate an already-declared strict ancestor without writing history."""
    current = _require_declaration(
        connection,
        profile,
        _current_alembic_revision(connection),
        "current Alembic revision has no compatibility declaration",
    )
    _validate_stored_declaration(connection, profile, current)
    stored_target = _require_declaration(
        connection,
        profile,
        target.revision_id,
        "downgrade target has no compatibility declaration",
    )
    _validate_stored_declaration(connection, profile, stored_target)
    assert_exact_replay(stored_target, target)
    if target.generation >= current.generation:
        raise CompatibilityContractError(
            "database_downgrade_target_invalid",
            "pre-retention downgrade target must be an existing strict ancestor",
        )


def _validate_forward_relation(
    profile: CompatibilityProfile,
    predecessor: RevisionDeclaration | None,
    proposed: RevisionDeclaration,
) -> None:
    validate_successor(profile, predecessor, proposed)


def _validate_stored_declaration(
    connection: Connection,
    profile: CompatibilityProfile,
    declaration: RevisionDeclaration,
) -> None:
    parent = (
        None
        if declaration.parent_revision_id is None
        else _require_declaration(
            connection,
            profile,
            declaration.parent_revision_id,
            "database declaration parent is missing",
        )
    )
    try:
        validate_successor(profile, parent, declaration)
    except CompatibilityContractError as error:
        raise DatabaseCompatibilityError(
            "stored database compatibility declaration chain is invalid"
        ) from error


def _current_alembic_revision(connection: Connection) -> str:
    try:
        revisions = tuple(
            row[0]
            for row in connection.execute(
                text("SELECT version_num FROM public.alembic_version ORDER BY version_num LIMIT 2")
            )
        )
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError("could not read the current Alembic revision") from error
    if len(revisions) != 1 or type(revisions[0]) is not str:
        raise DatabaseCompatibilityError("database must expose exactly one Alembic revision")
    return revisions[0]


def _latest_declaration(
    connection: Connection,
    profile: CompatibilityProfile,
) -> RevisionDeclaration | None:
    try:
        result = connection.execute(
            select(compatibility_declarations.c.revision_id)
            .order_by(compatibility_declarations.c.generation.desc())
            .limit(1)
        )
        revision_id = result.scalar_one_or_none()
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not read the latest database compatibility declaration"
        ) from error
    if revision_id is None:
        return None
    if type(revision_id) is not str:
        raise DatabaseCompatibilityError("database compatibility revision identifier is invalid")
    return _require_declaration(
        connection,
        profile,
        revision_id,
        "latest database compatibility declaration is missing",
    )


def _require_declaration(
    connection: Connection,
    profile: CompatibilityProfile,
    revision_id: str,
    missing_message: str,
) -> RevisionDeclaration:
    declaration = _load_declaration(connection, profile, revision_id)
    if declaration is None:
        raise DatabaseCompatibilityError(missing_message)
    return declaration


def _load_declaration(
    connection: Connection,
    profile: CompatibilityProfile,
    revision_id: str,
) -> RevisionDeclaration | None:
    try:
        declaration_row = (
            connection.execute(
                select(compatibility_declarations).where(
                    compatibility_declarations.c.revision_id == revision_id
                )
            )
            .mappings()
            .one_or_none()
        )
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not read a database compatibility declaration"
        ) from error
    if declaration_row is None:
        return None
    capabilities = _load_capabilities(connection, profile, revision_id)
    try:
        return revision_declaration_from_row(declaration_row, capabilities)
    except CompatibilityContractError as error:
        raise DatabaseCompatibilityError(
            "database compatibility declaration violates its immutable contract"
        ) from error


def _load_capabilities(
    connection: Connection,
    profile: CompatibilityProfile,
    revision_id: str,
) -> tuple[CapabilityDeclaration, ...]:
    try:
        rows = tuple(
            connection.execute(
                select(
                    compatibility_capabilities.c.capability_id,
                    compatibility_capabilities.c.descriptor_hash,
                )
                .where(compatibility_capabilities.c.revision_id == revision_id)
                .order_by(compatibility_capabilities.c.capability_id)
                .limit(profile.capability_read_limit)
            ).mappings()
        )
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not read database compatibility capabilities"
        ) from error
    if len(rows) == profile.capability_read_limit:
        raise DatabaseCompatibilityError("database compatibility capability bound is exceeded")
    try:
        return tuple(capability_from_row(row) for row in rows)
    except CompatibilityContractError as error:
        raise DatabaseCompatibilityError(
            "database compatibility capabilities violate their immutable contract"
        ) from error


def _insert_declaration(connection: Connection, declaration: RevisionDeclaration) -> None:
    try:
        connection.execute(
            insert(compatibility_declarations).values(
                generation=declaration.generation,
                revision_id=declaration.revision_id,
                parent_revision_id=declaration.parent_revision_id,
                transition_kind=declaration.transition_kind,
                lineage_id=declaration.lineage_id,
                protocol_version=declaration.protocol_version,
                declaration_hash=declaration.declaration_hash,
            )
        )
        connection.execute(
            insert(compatibility_capabilities),
            [
                {
                    "revision_id": declaration.revision_id,
                    "capability_id": capability.capability_id,
                    "descriptor_hash": capability.descriptor_hash,
                }
                for capability in declaration.capabilities
            ],
        )
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not append the database compatibility declaration"
        ) from error
