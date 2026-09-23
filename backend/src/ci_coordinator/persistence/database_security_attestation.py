from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.schema import APPLICATION_SCHEMA


async def public_access_is_restricted(connection: AsyncConnection) -> bool:
    """Collect whether PUBLIC has any privilege on the application object closure."""
    return await connection.run_sync(public_access_is_restricted_sync)


def public_access_is_restricted_sync(connection: Connection) -> bool:
    result = connection.scalar(
        text(
            "SELECT NOT EXISTS ("
            "SELECT 1 FROM pg_namespace AS namespace "
            "CROSS JOIN LATERAL aclexplode("
            "COALESCE(namespace.nspacl, acldefault('n', namespace.nspowner))"
            ") AS privilege "
            "WHERE namespace.nspname = :schema AND privilege.grantee = 0"
            ") AND NOT EXISTS ("
            "SELECT 1 FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "CROSS JOIN LATERAL aclexplode("
            "COALESCE(relation.relacl, acldefault('r', relation.relowner))"
            ") AS privilege "
            "WHERE namespace.nspname = :schema AND relation.relkind IN ('r', 'S', 'v', 'm') "
            "AND privilege.grantee = 0"
            ") AND NOT EXISTS ("
            "SELECT 1 FROM pg_proc AS routine "
            "JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
            "CROSS JOIN LATERAL aclexplode("
            "COALESCE(routine.proacl, acldefault('f', routine.proowner))"
            ") AS privilege "
            "WHERE namespace.nspname = :schema AND privilege.grantee = 0"
            ") AND NOT EXISTS ("
            "SELECT 1 FROM pg_type AS type "
            "JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace "
            "CROSS JOIN LATERAL aclexplode("
            "COALESCE(type.typacl, acldefault('T', type.typowner))"
            ") AS privilege "
            "WHERE namespace.nspname = :schema AND type.typelem = 0 AND privilege.grantee = 0"
            ")"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    return result is True


async def application_routines_are_security_invoker(connection: AsyncConnection) -> bool:
    """Reject application-schema routines that can execute with owner authority."""
    return await connection.run_sync(application_routines_are_security_invoker_sync)


def application_routines_are_security_invoker_sync(connection: Connection) -> bool:
    result = connection.scalar(
        text(
            "SELECT NOT EXISTS ("
            "SELECT 1 FROM pg_proc AS routine "
            "JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
            "WHERE namespace.nspname = :schema AND routine.prosecdef"
            ")"
        ),
        {"schema": APPLICATION_SCHEMA},
    )
    return result is True
