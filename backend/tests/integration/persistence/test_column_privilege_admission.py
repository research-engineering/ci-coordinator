from __future__ import annotations

import asyncio
from typing import Literal
from unittest.mock import Mock

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.persistence import PostgresUnitOfWork
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.compatibility_repository import load_current_compatibility
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.database_security_attestation import public_access_is_restricted
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.migration_settings import to_psycopg_connection_string
from ci_coordinator.persistence.principal_attestation import runtime_principal_is_restricted
from ci_coordinator.persistence.runtime_principal_access import (
    RuntimePrincipalAccessError,
    check_runtime_principal_access,
)

from .conftest import RUNTIME_ROLE

pytestmark = pytest.mark.persistence
_COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
_CLEAN_METADATA_FACTS = {
    "SELECT": (True, False, True, False),
    "INSERT": (False, False, False, False),
    "UPDATE": (False, False, False, False),
    "REFERENCES": (False, False, False, False),
}


@pytest.mark.parametrize(
    ("privilege", "grant_option", "rejected"),
    (
        ("INSERT", False, True),
        ("UPDATE", False, True),
        ("REFERENCES", False, True),
        ("SELECT", True, True),
        ("SELECT", False, False),
    ),
)
def test_metadata_column_grant_is_an_independent_admission_operand(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    privilege: Literal["SELECT", "INSERT", "UPDATE", "REFERENCES"],
    grant_option: bool,
    rejected: bool,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _runtime_admits(runtime)
            _check_metadata_access(postgres_database_url, rejected=False)
            async with admin.begin() as connection:
                await acquire_compatibility_fence(
                    connection, profile, CompatibilityFenceMode.MIGRATION
                )
                before = await _metadata_facts(connection)
                assert before == _CLEAN_METADATA_FACTS
                table_acl = await _metadata_table_acl(connection)
                assert await _metadata_column_acl(connection) == ()
                suffix = " WITH GRANT OPTION" if grant_option else ""
                await connection.execute(
                    text(
                        f"GRANT {privilege} (version_num) ON public.alembic_version "
                        f"TO {RUNTIME_ROLE}{suffix}"
                    )
                )
            try:
                async with admin.connect() as connection:
                    after = await _metadata_facts(connection)
                    expected = dict(before)
                    expected[privilege] = (*before[privilege][:2], True, grant_option)
                    assert after == expected
                    assert await _metadata_table_acl(connection) == table_acl
                    assert await _metadata_column_acl(connection) == ((privilege, grant_option),)
                async with runtime.connect() as connection:
                    await acquire_compatibility_fence(
                        connection, profile, CompatibilityFenceMode.PARTICIPANT
                    )
                    assert await public_access_is_restricted(connection)
                    assert (
                        await runtime_principal_is_restricted(connection, profile) is not rejected
                    )
                _check_metadata_access(postgres_database_url, rejected=rejected)
                if rejected:
                    await _runtime_rejects(
                        runtime,
                        monkeypatch,
                        "runtime database principal is not capability-restricted",
                    )
                else:
                    await _runtime_admits(runtime)
            finally:
                async with admin.begin() as connection:
                    await acquire_compatibility_fence(
                        connection, profile, CompatibilityFenceMode.MIGRATION
                    )
                    await connection.execute(
                        text(
                            f"REVOKE {privilege} (version_num) ON public.alembic_version "
                            f"FROM {RUNTIME_ROLE}"
                        )
                    )
            async with admin.connect() as connection:
                assert await _metadata_facts(connection) == before
                assert await _metadata_column_acl(connection) == ()
            await _runtime_admits(runtime)
            _check_metadata_access(postgres_database_url, rejected=False)
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("consumer", ("runtime", "migration"))
def test_public_column_select_rejects_without_changing_runtime_effective_privileges(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    consumer: Literal["runtime", "migration"],
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _runtime_admits(runtime)
            await _migration_attests(admin)
            async with admin.begin() as connection:
                await acquire_compatibility_fence(
                    connection, profile, CompatibilityFenceMode.MIGRATION
                )
                assert await public_access_is_restricted(connection)
                assert await _public_column_select(connection) is False
                assert await _public_schema_or_table_access(connection) is False
                await connection.execute(
                    text(
                        "GRANT SELECT (payload_canonical_json) "
                        "ON ci_coordinator.audit_events TO PUBLIC"
                    )
                )
            try:
                async with admin.connect() as connection:
                    assert await _public_column_select(connection) is True
                    assert await _public_schema_or_table_access(connection) is False
                async with runtime.connect() as connection:
                    await acquire_compatibility_fence(
                        connection, profile, CompatibilityFenceMode.PARTICIPANT
                    )
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT has_table_privilege(current_user, "
                                "'ci_coordinator.audit_events', 'SELECT')"
                            )
                        )
                        is True
                    )
                    assert await runtime_principal_is_restricted(connection, profile)
                    assert not await public_access_is_restricted(connection)
                if consumer == "runtime":
                    await _runtime_rejects(
                        runtime, monkeypatch, "PUBLIC has application-schema privileges"
                    )
                else:
                    with pytest.raises(
                        RuntimeError,
                        match=r"^resulting application schema grants authority to PUBLIC$",
                    ):
                        await _migration_attests(admin)
            finally:
                async with admin.begin() as connection:
                    await acquire_compatibility_fence(
                        connection, profile, CompatibilityFenceMode.MIGRATION
                    )
                    await connection.execute(
                        text(
                            "REVOKE SELECT (payload_canonical_json) "
                            "ON ci_coordinator.audit_events FROM PUBLIC"
                        )
                    )
            async with admin.connect() as connection:
                assert await _public_column_select(connection) is False
                assert await public_access_is_restricted(connection)
            await _runtime_admits(runtime)
            await _migration_attests(admin)
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


async def _metadata_facts(connection: AsyncConnection) -> dict[str, tuple[object, ...]]:
    rows = await connection.execute(
        text(
            "SELECT privilege.name, "
            "has_table_privilege(:role, 'public.alembic_version', privilege.name), "
            "has_table_privilege(:role, 'public.alembic_version', "
            "privilege.name || ' WITH GRANT OPTION'), "
            "has_any_column_privilege(:role, 'public.alembic_version', privilege.name), "
            "has_any_column_privilege(:role, 'public.alembic_version', "
            "privilege.name || ' WITH GRANT OPTION') "
            "FROM unnest(CAST(:privileges AS text[])) AS privilege(name) ORDER BY privilege.name"
        ),
        {"role": RUNTIME_ROLE, "privileges": list(_COLUMN_PRIVILEGES)},
    )
    return {row[0]: tuple(row[1:]) for row in rows}


async def _metadata_table_acl(connection: AsyncConnection) -> object:
    return await connection.scalar(
        text("SELECT relacl::text FROM pg_class WHERE oid = 'public.alembic_version'::regclass")
    )


async def _metadata_column_acl(connection: AsyncConnection) -> tuple[tuple[object, ...], ...]:
    rows = await connection.execute(
        text(
            "SELECT acl.privilege_type, acl.is_grantable FROM pg_attribute AS attribute "
            "CROSS JOIN LATERAL aclexplode(attribute.attacl) AS acl "
            "WHERE attribute.attrelid = 'public.alembic_version'::regclass "
            "AND attribute.attname = 'version_num' AND attribute.attnum > 0 "
            "AND NOT attribute.attisdropped "
            "AND acl.grantee = (SELECT oid FROM pg_roles WHERE rolname = :role) "
            "ORDER BY acl.privilege_type"
        ),
        {"role": RUNTIME_ROLE},
    )
    return tuple(tuple(row) for row in rows)


def _check_metadata_access(database_url: str, *, rejected: bool) -> None:
    with psycopg.connect(to_psycopg_connection_string(database_url)) as connection:
        if rejected:
            with pytest.raises(RuntimePrincipalAccessError) as error:
                check_runtime_principal_access(connection, RUNTIME_ROLE)
            assert error.value.code == "runtime_access_mismatch"
        else:
            check_runtime_principal_access(connection, RUNTIME_ROLE)


async def _runtime_admits(engine: AsyncEngine) -> None:
    async with PostgresUnitOfWork(engine) as transaction:
        assert transaction.audit_events is not None
        await transaction.rollback()


async def _runtime_rejects(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    transaction = PostgresUnitOfWork(engine)
    initialize = Mock(wraps=transaction._initialize_repositories)
    monkeypatch.setattr(transaction, "_initialize_repositories", initialize)
    with pytest.raises(DatabaseCapabilityUnavailable, match=message):
        async with transaction:
            pytest.fail("repository exposed despite rejected column privileges")
    initialize.assert_not_called()


async def _migration_attests(engine: AsyncEngine) -> None:
    profile = load_bundled_profile()
    async with engine.begin() as connection:
        await acquire_compatibility_fence(connection, profile, CompatibilityFenceMode.MIGRATION)
        current = await load_current_compatibility(connection, profile)
        await connection.run_sync(attest_resulting_capabilities, current.declaration)


async def _public_column_select(connection: AsyncConnection) -> bool:
    value = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_attribute AS attribute "
            "CROSS JOIN LATERAL aclexplode(attribute.attacl) AS acl "
            "WHERE attribute.attrelid = 'ci_coordinator.audit_events'::regclass "
            "AND attribute.attname = 'payload_canonical_json' AND attribute.attnum > 0 "
            "AND NOT attribute.attisdropped AND acl.grantee = 0 "
            "AND acl.privilege_type = 'SELECT' AND NOT acl.is_grantable)"
        )
    )
    assert type(value) is bool
    return value


async def _public_schema_or_table_access(connection: AsyncConnection) -> bool:
    value = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace AS namespace "
            "CROSS JOIN LATERAL aclexplode(namespace.nspacl) AS acl "
            "WHERE namespace.nspname = 'ci_coordinator' AND acl.grantee = 0) "
            "OR EXISTS (SELECT 1 FROM pg_class AS relation "
            "CROSS JOIN LATERAL aclexplode(relation.relacl) AS acl "
            "WHERE relation.oid = 'ci_coordinator.audit_events'::regclass AND acl.grantee = 0)"
        )
    )
    assert type(value) is bool
    return value
