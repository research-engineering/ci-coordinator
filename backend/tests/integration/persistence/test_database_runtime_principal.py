from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from ci_coordinator.persistence import PostgresShadowReconciliationUnitOfWork, PostgresUnitOfWork
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.principal_attestation import runtime_principal_is_restricted
from ci_coordinator.persistence.schema_attestation import public_access_is_restricted
from ci_coordinator.persistence.shadow_reconciliation_principal_attestation import (
    shadow_reconciliation_v2_runtime_principal_is_restricted,
)

from ._audit_replay_support import load_test_audit_records
from .conftest import RUNTIME_ROLE

pytestmark = pytest.mark.persistence


def test_only_the_direct_capability_runtime_principal_is_admitted(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with migration_engine.begin() as connection:
                assert not await runtime_principal_is_restricted(connection, profile)
                assert await public_access_is_restricted(connection)
            async with runtime_engine.connect() as connection:
                assert await runtime_principal_is_restricted(connection, profile)
                assert await shadow_reconciliation_v2_runtime_principal_is_restricted(connection)
                await connection.rollback()
                ddl_transaction = await connection.begin()
                try:
                    with pytest.raises(Exception, match=r"must be owner|permission denied"):
                        await connection.execute(
                            text(
                                "ALTER TABLE ci_coordinator.audit_events "
                                "ADD COLUMN rejected integer"
                            )
                        )
                finally:
                    await ddl_transaction.rollback()
                declaration_transaction = await connection.begin()
                try:
                    with pytest.raises(Exception, match="permission denied"):
                        await connection.execute(
                            text(
                                "UPDATE ci_coordinator.database_compatibility_declarations "
                                "SET declaration_hash = :digest WHERE generation = 1"
                            ),
                            {"digest": "0" * 64},
                        )
                finally:
                    await declaration_transaction.rollback()
                shadow_state_transaction = await connection.begin()
                try:
                    with pytest.raises(Exception, match="permission denied"):
                        await connection.execute(
                            text(
                                "UPDATE ci_coordinator.reconciliation_subjects "
                                "SET contract_hash = :digest"
                            ),
                            {"digest": "0" * 64},
                        )
                finally:
                    await shadow_state_transaction.rollback()
            with pytest.raises(DatabaseCapabilityUnavailable, match="runtime database principal"):
                async with PostgresUnitOfWork(migration_engine):
                    pass
            async with PostgresUnitOfWork(runtime_engine) as unit_of_work:
                assert await load_test_audit_records(unit_of_work.audit_events) == ()
                await unit_of_work.rollback()
            async with PostgresShadowReconciliationUnitOfWork(runtime_engine) as unit_of_work:
                assert await unit_of_work.shadow_evidence.list_records("shadow-profile/v1") == ()
                await unit_of_work.rollback()
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "privilege",
    (
        "INSERT",
        "UPDATE",
        "REFERENCES",
        "INSERT (version_num)",
        "UPDATE (version_num)",
        "REFERENCES (version_num)",
    ),
)
def test_principal_attestation_rejects_excess_migration_metadata_authority(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    privilege: str,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with runtime_engine.connect() as connection:
                assert await runtime_principal_is_restricted(connection, profile)
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text(f"GRANT {privilege} ON public.alembic_version TO {RUNTIME_ROLE}")
                )
            try:
                async with runtime_engine.connect() as connection:
                    assert not await runtime_principal_is_restricted(connection, profile)
            finally:
                async with migration_engine.begin() as connection:
                    await connection.execute(
                        text(f"REVOKE {privilege} ON public.alembic_version FROM {RUNTIME_ROLE}")
                    )
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("setup_statements", "cleanup_statements"),
    (
        (
            (f"GRANT UPDATE ON ci_coordinator.audit_events TO {RUNTIME_ROLE}",),
            (f"REVOKE UPDATE ON ci_coordinator.audit_events FROM {RUNTIME_ROLE}",),
        ),
        (
            (f"GRANT SELECT ON ci_coordinator.audit_events TO {RUNTIME_ROLE} WITH GRANT OPTION",),
            (f"REVOKE GRANT OPTION FOR SELECT ON ci_coordinator.audit_events FROM {RUNTIME_ROLE}",),
        ),
        (
            (f"ALTER ROLE {RUNTIME_ROLE} CREATEROLE",),
            (f"ALTER ROLE {RUNTIME_ROLE} NOCREATEROLE",),
        ),
        (
            (f"ALTER ROLE {RUNTIME_ROLE} INHERIT",),
            (f"ALTER ROLE {RUNTIME_ROLE} NOINHERIT",),
        ),
        (
            (f"GRANT MAINTAIN ON ci_coordinator.audit_events TO {RUNTIME_ROLE}",),
            (f"REVOKE MAINTAIN ON ci_coordinator.audit_events FROM {RUNTIME_ROLE}",),
        ),
        (
            (
                "CREATE VIEW ci_coordinator.runtime_privilege_view AS SELECT 1 AS value",
                f"GRANT SELECT ON ci_coordinator.runtime_privilege_view TO {RUNTIME_ROLE}",
            ),
            (
                f"REVOKE SELECT ON ci_coordinator.runtime_privilege_view FROM {RUNTIME_ROLE}",
                "DROP VIEW ci_coordinator.runtime_privilege_view",
            ),
        ),
        (
            (
                "CREATE SEQUENCE ci_coordinator.runtime_privilege_sequence",
                (
                    "GRANT USAGE ON SEQUENCE ci_coordinator.runtime_privilege_sequence "
                    f"TO {RUNTIME_ROLE}"
                ),
            ),
            (
                (
                    "REVOKE USAGE ON SEQUENCE ci_coordinator.runtime_privilege_sequence "
                    f"FROM {RUNTIME_ROLE}"
                ),
                "DROP SEQUENCE ci_coordinator.runtime_privilege_sequence",
            ),
        ),
        (
            (
                "CREATE DOMAIN ci_coordinator.runtime_privilege_domain AS integer",
                f"GRANT USAGE ON DOMAIN ci_coordinator.runtime_privilege_domain TO {RUNTIME_ROLE}",
            ),
            (
                (
                    "REVOKE USAGE ON DOMAIN ci_coordinator.runtime_privilege_domain "
                    f"FROM {RUNTIME_ROLE}"
                ),
                "DROP DOMAIN ci_coordinator.runtime_privilege_domain",
            ),
        ),
        (
            (
                "CREATE ROLE ci_coordinator_membership_parent_test NOLOGIN",
                f"GRANT ci_coordinator_membership_parent_test TO {RUNTIME_ROLE}",
            ),
            (
                f"REVOKE ci_coordinator_membership_parent_test FROM {RUNTIME_ROLE}",
                "DROP ROLE ci_coordinator_membership_parent_test",
            ),
        ),
        (
            (
                "CREATE ROLE ci_coordinator_membership_child_test NOLOGIN",
                f"GRANT {RUNTIME_ROLE} TO ci_coordinator_membership_child_test",
            ),
            (
                f"REVOKE {RUNTIME_ROLE} FROM ci_coordinator_membership_child_test",
                "DROP ROLE ci_coordinator_membership_child_test",
            ),
        ),
        (
            (
                (
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA ci_coordinator "
                    f"GRANT SELECT ON TABLES TO {RUNTIME_ROLE}"
                ),
            ),
            (
                (
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA ci_coordinator "
                    f"REVOKE SELECT ON TABLES FROM {RUNTIME_ROLE}"
                ),
            ),
        ),
        (
            (f"ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO {RUNTIME_ROLE}",),
            (f"ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM {RUNTIME_ROLE}",),
        ),
    ),
)
def test_principal_attestation_rejects_each_excess_authority_fact(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    setup_statements: tuple[str, ...],
    cleanup_statements: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with runtime_engine.connect() as connection:
                assert await runtime_principal_is_restricted(connection, profile)
            async with migration_engine.begin() as connection:
                for statement in setup_statements:
                    await connection.execute(text(statement))
            try:
                async with runtime_engine.connect() as connection:
                    assert not await runtime_principal_is_restricted(connection, profile)
            finally:
                async with migration_engine.begin() as connection:
                    for statement in cleanup_statements:
                        await connection.execute(text(statement))
            async with runtime_engine.connect() as connection:
                assert await runtime_principal_is_restricted(connection, profile)
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_shadow_reconciliation_principal_rejects_extra_column_authority(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        grant = (
            "GRANT UPDATE (contract_hash) ON ci_coordinator.reconciliation_subjects "
            f"TO {RUNTIME_ROLE}"
        )
        revoke = (
            "REVOKE UPDATE (contract_hash) ON ci_coordinator.reconciliation_subjects "
            f"FROM {RUNTIME_ROLE}"
        )
        try:
            async with migration_engine.begin() as connection:
                await connection.execute(text(grant))
            try:
                async with runtime_engine.connect() as connection:
                    assert not await shadow_reconciliation_v2_runtime_principal_is_restricted(
                        connection
                    )
            finally:
                async with migration_engine.begin() as connection:
                    await connection.execute(text(revoke))
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("setup_statement", "cleanup_statement"),
    (
        (
            (
                "REVOKE INSERT (operation_id) ON ci_coordinator.config_epoch_registrations "
                f"FROM {RUNTIME_ROLE}"
            ),
            (
                "GRANT INSERT (operation_id) ON ci_coordinator.config_epoch_registrations "
                f"TO {RUNTIME_ROLE}"
            ),
        ),
        (
            (
                "GRANT UPDATE (operation_id) ON ci_coordinator.config_epoch_registrations "
                f"TO {RUNTIME_ROLE}"
            ),
            (
                "REVOKE UPDATE (operation_id) ON ci_coordinator.config_epoch_registrations "
                f"FROM {RUNTIME_ROLE}"
            ),
        ),
    ),
)
def test_config_registration_principal_rejects_missing_or_excess_column_authority(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    setup_statement: str,
    cleanup_statement: str,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with migration_engine.begin() as connection:
                await connection.execute(text(setup_statement))
            try:
                async with runtime_engine.connect() as connection:
                    assert not await runtime_principal_is_restricted(connection, profile)
            finally:
                async with migration_engine.begin() as connection:
                    await connection.execute(text(cleanup_statement))
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())
