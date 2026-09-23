from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    CompatibilityContractError,
    RevisionDeclaration,
    build_declaration,
)
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityProfile,
    load_bundled_profile,
)
from ci_coordinator.persistence.errors import DatabaseCompatibilityError
from ci_coordinator.persistence.migration_protocol import (
    ForwardDeclarationOutcome,
    apply_forward_declaration,
    validate_pre_retention_downgrade,
)

pytestmark = pytest.mark.persistence


def test_forward_append_exact_replay_and_pre_retention_selection_are_atomic(
    postgres_database_url: str,
) -> None:
    profile = load_bundled_profile()
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                current = _current_declaration(connection, profile)
                successor = _successor(profile, current)
                declaration_count = _declaration_count(connection)
                assert (
                    apply_forward_declaration(
                        connection,
                        profile,
                        previous_revision_id=current.revision_id,
                        proposed=successor,
                        attest_resulting_capabilities=_attest_resulting_capabilities,
                    )
                    is ForwardDeclarationOutcome.APPENDED
                )
                assert _declaration_count(connection) == declaration_count + 1
                assert (
                    apply_forward_declaration(
                        connection,
                        profile,
                        previous_revision_id=current.revision_id,
                        proposed=successor,
                        attest_resulting_capabilities=_attest_resulting_capabilities,
                    )
                    is ForwardDeclarationOutcome.REPLAY_VALIDATED
                )
                with pytest.raises(CompatibilityContractError, match="differs"):
                    apply_forward_declaration(
                        connection,
                        profile,
                        previous_revision_id=current.revision_id,
                        proposed=replace(successor, declaration_hash="0" * 64),
                        attest_resulting_capabilities=_attest_resulting_capabilities,
                    )
                connection.execute(
                    text("UPDATE public.alembic_version SET version_num = :revision_id"),
                    {"revision_id": successor.revision_id},
                )
                validate_pre_retention_downgrade(connection, profile, target=current)
                assert _declaration_count(connection) == declaration_count + 1
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_protocol_rejects_a_stale_forward_predecessor_and_non_ancestor_target(
    postgres_database_url: str,
) -> None:
    profile = load_bundled_profile()
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                current = _current_declaration(connection, profile)
                successor = _successor(profile, current)
                declaration_count = _declaration_count(connection)
                with pytest.raises(
                    DatabaseCompatibilityError,
                    match="does not match the declared migration predecessor",
                ):
                    apply_forward_declaration(
                        connection,
                        profile,
                        previous_revision_id="20990101_0001",
                        proposed=successor,
                        attest_resulting_capabilities=_attest_resulting_capabilities,
                    )
                assert _declaration_count(connection) == declaration_count
                with pytest.raises(CompatibilityContractError, match="strict ancestor"):
                    validate_pre_retention_downgrade(connection, profile, target=current)
                assert _declaration_count(connection) == declaration_count
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_failed_resulting_capability_attestation_appends_zero_rows(
    postgres_database_url: str,
) -> None:
    profile = load_bundled_profile()
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                current = _current_declaration(connection, profile)
                successor = _successor(profile, current)
                declaration_count = _declaration_count(connection)
                with pytest.raises(RuntimeError, match="resulting capability attestation failed"):
                    apply_forward_declaration(
                        connection,
                        profile,
                        previous_revision_id=current.revision_id,
                        proposed=successor,
                        attest_resulting_capabilities=_reject_resulting_capabilities,
                    )
                assert _declaration_count(connection) == declaration_count
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def _current_declaration(
    connection: Connection,
    profile: CompatibilityProfile,
) -> RevisionDeclaration:
    revision_id = connection.scalar(text("SELECT version_num FROM public.alembic_version"))
    assert type(revision_id) is str
    declaration = (
        connection.execute(
            text(
                "SELECT generation, parent_revision_id, transition_kind, declaration_hash "
                "FROM ci_coordinator.database_compatibility_declarations "
                "WHERE revision_id = :revision_id"
            ),
            {"revision_id": revision_id},
        )
        .mappings()
        .one()
    )
    capability_rows = connection.execute(
        text(
            "SELECT capability_id, descriptor_hash "
            "FROM ci_coordinator.database_compatibility_capabilities "
            "WHERE revision_id = :revision_id ORDER BY capability_id"
        ),
        {"revision_id": revision_id},
    ).mappings()
    current = build_declaration(
        profile,
        generation=declaration["generation"],
        revision_id=revision_id,
        parent_revision_id=declaration["parent_revision_id"],
        transition_kind=declaration["transition_kind"],
        capabilities=tuple(
            CapabilityDeclaration(row["capability_id"], row["descriptor_hash"])
            for row in capability_rows
        ),
    )
    assert current.declaration_hash == declaration["declaration_hash"]
    return current


def _successor(
    profile: CompatibilityProfile,
    current: RevisionDeclaration,
) -> RevisionDeclaration:
    return build_declaration(
        profile,
        generation=current.generation + 1,
        revision_id="20991231_9999",
        parent_revision_id=current.revision_id,
        transition_kind="expand",
        capabilities=(
            *current.capabilities,
            CapabilityDeclaration("migration-protocol-test/v1", "1" * 64),
        ),
    )


def _declaration_count(connection: Connection) -> int:
    count = connection.scalar(
        text("SELECT count(*) FROM ci_coordinator.database_compatibility_declarations")
    )
    assert type(count) is int
    return count


def _attest_resulting_capabilities(_declaration: RevisionDeclaration) -> None:
    return None


def _reject_resulting_capabilities(_declaration: RevisionDeclaration) -> None:
    raise RuntimeError("resulting capability attestation failed")
