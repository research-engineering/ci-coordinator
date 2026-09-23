from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.data_attestation import (
    application_schema_is_empty_of_public_default_privileges,
)
from ci_coordinator.persistence.schema_attestation import (
    application_routines_are_security_invoker,
    public_access_is_restricted,
)

pytestmark = pytest.mark.persistence


def test_public_has_no_application_schema_access_and_history_is_append_only(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                assert await public_access_is_restricted(connection)
                with pytest.raises(Exception, match="append-only"):
                    await connection.execute(
                        text(
                            "UPDATE ci_coordinator.database_compatibility_declarations "
                            "SET declaration_hash = :digest WHERE generation = 1"
                        ),
                        {"digest": "0" * 64},
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_routine_and_type_public_access_and_security_definer_are_rejected(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "CREATE FUNCTION ci_coordinator.test_privilege_probe() "
                        "RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
                    )
                )
                await connection.execute(
                    text("REVOKE ALL ON FUNCTION ci_coordinator.test_privilege_probe() FROM PUBLIC")
                )
                assert await public_access_is_restricted(connection)
                assert not await application_routines_are_security_invoker(connection)
                await connection.execute(
                    text("CREATE DOMAIN ci_coordinator.test_privilege_domain AS integer")
                )
                assert await _runtime_domain_and_array_usage(connection) == (True, True)
                assert not await public_access_is_restricted(connection)
                await connection.execute(
                    text("REVOKE ALL ON DOMAIN ci_coordinator.test_privilege_domain FROM PUBLIC")
                )
                assert await _runtime_domain_and_array_usage(connection) == (False, False)
                assert await public_access_is_restricted(connection)
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DROP DOMAIN IF EXISTS ci_coordinator.test_privilege_domain")
                )
                await connection.execute(
                    text("DROP FUNCTION IF EXISTS ci_coordinator.test_privilege_probe()")
                )
            await engine.dispose()

    asyncio.run(scenario())


async def _runtime_domain_and_array_usage(connection: AsyncConnection) -> tuple[bool, bool]:
    row = (
        await connection.execute(
            text(
                "SELECT "
                "has_type_privilege(:role, "
                "to_regtype('ci_coordinator.test_privilege_domain'), 'USAGE'), "
                "has_type_privilege(:role, "
                "to_regtype('ci_coordinator._test_privilege_domain'), 'USAGE')"
            ),
            {"role": "ci_coordinator_runtime_test"},
        )
    ).one()
    return row[0] is True, row[1] is True


def test_schema_local_public_default_privileges_are_rejected(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                assert await application_schema_is_empty_of_public_default_privileges(connection)
                await connection.execute(
                    text(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA ci_coordinator "
                        "GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
                    )
                )
                assert not await application_schema_is_empty_of_public_default_privileges(
                    connection
                )
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA ci_coordinator "
                        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
                    )
                )
            await engine.dispose()

    asyncio.run(scenario())


def test_public_schema_create_grant_is_rejected(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("GRANT CREATE ON SCHEMA ci_coordinator TO PUBLIC"))
                assert not await public_access_is_restricted(connection)
        finally:
            async with engine.begin() as connection:
                await connection.execute(text("REVOKE CREATE ON SCHEMA ci_coordinator FROM PUBLIC"))
            await engine.dispose()

    asyncio.run(scenario())
