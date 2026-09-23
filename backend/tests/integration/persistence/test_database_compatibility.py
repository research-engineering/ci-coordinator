from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from ci_coordinator.persistence.compatibility_admission import admit_schema_dependent_operation
from ci_coordinator.persistence.compatibility_contracts import CapabilityDeclaration
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.connection import (
    configure_read_committed,
    create_postgres_engine,
    verify_read_committed,
)
from ci_coordinator.persistence.data_attestation import (
    audit_ledger_bridge_constraints_are_validated,
    audit_ledger_seed_is_valid,
)
from ci_coordinator.persistence.errors import (
    DatabaseCapabilityUnavailable,
    DatabaseCompatibilityError,
)
from ci_coordinator.persistence.schema_capabilities import audit_ledger_requirements

pytestmark = pytest.mark.persistence


def test_current_head_admission_covers_the_operation_capability_closure(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        required = audit_ledger_requirements(profile)
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with engine.connect() as raw_connection:
                connection = await configure_read_committed(raw_connection, profile)
                async with connection.begin():
                    await verify_read_committed(connection, profile)
                    await acquire_compatibility_fence(
                        connection,
                        profile,
                        CompatibilityFenceMode.PARTICIPANT,
                    )
                    admitted = await admit_schema_dependent_operation(
                        connection,
                        profile,
                        required,
                    )
                    assert admitted.declaration.generation >= 1
                    assert all(
                        capability in admitted.declaration.capabilities for capability in required
                    )
                    if admitted.parent is not None:
                        assert admitted.parent.generation == admitted.declaration.generation - 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unvalidated_required_data_bridge_constraint_is_rejected(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                assert await audit_ledger_bridge_constraints_are_validated(connection)
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.audit_events DROP CONSTRAINT "
                        "ck_audit_events_actor_byte_limit"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.audit_events ADD CONSTRAINT "
                        "ck_audit_events_actor_byte_limit CHECK (octet_length(actor) >= 1 "
                        "AND octet_length(actor) <= 4096) NOT VALID"
                    )
                )
                assert not await audit_ledger_bridge_constraints_are_validated(connection)
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.audit_events DROP CONSTRAINT "
                        "ck_audit_events_actor_byte_limit"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.audit_events ADD CONSTRAINT "
                        "ck_audit_events_actor_byte_limit CHECK (octet_length(actor) >= 1 "
                        "AND octet_length(actor) <= 4096)"
                    )
                )
            await engine.dispose()

    asyncio.run(scenario())


def test_missing_required_audit_ledger_seed_is_rejected(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await audit_ledger_seed_is_valid(connection)
                    await connection.execute(
                        text("DELETE FROM ci_coordinator.audit_ledger_head WHERE head_id = 1")
                    )
                    assert not await audit_ledger_seed_is_valid(connection)
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_missing_current_capability_fails_before_schema_attestation(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        required = (
            *audit_ledger_requirements(profile),
            CapabilityDeclaration("test-unavailable/v1", "0" * 64),
        )
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as raw_connection:
                connection = await configure_read_committed(raw_connection, profile)
                transaction = await connection.begin()
                try:
                    await verify_read_committed(connection, profile)
                    await acquire_compatibility_fence(
                        connection,
                        profile,
                        CompatibilityFenceMode.PARTICIPANT,
                    )
                    with pytest.raises(DatabaseCapabilityUnavailable, match="does not provide"):
                        await admit_schema_dependent_operation(connection, profile, required)
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_non_read_committed_transaction_is_rejected_before_fence_acquisition(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with engine.connect() as raw_connection:
                connection = await raw_connection.execution_options(
                    isolation_level="REPEATABLE READ"
                )
                async with connection.begin():
                    with pytest.raises(DatabaseCompatibilityError, match="isolation"):
                        await verify_read_committed(connection, profile)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
