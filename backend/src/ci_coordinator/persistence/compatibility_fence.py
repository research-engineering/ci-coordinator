"""Transaction-local compatibility fences and timeout discipline."""

from __future__ import annotations

from enum import Enum

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityProfile,
    CompatibilityTimeouts,
)
from ci_coordinator.persistence.errors import (
    DatabaseCompatibilityError,
    DatabaseCompatibilityOutcomeUnknown,
    DatabaseCompatibilityTimeout,
    DatabaseCompatibilityTransactionTimeout,
    DatabaseQueryCancelled,
)


class CompatibilityFenceMode(Enum):
    PARTICIPANT = "participant"
    MIGRATION = "migration"


async def configure_transaction_timeouts(
    connection: AsyncConnection,
    timeouts: CompatibilityTimeouts,
) -> None:
    """Set the complete local timeout profile before waiting on the fence."""
    try:
        await connection.execute(
            text(
                "SELECT pg_catalog.set_config('search_path', "
                "'pg_catalog', true), "
                "pg_catalog.set_config('lock_timeout', :lock_timeout, true), "
                "pg_catalog.set_config('statement_timeout', :statement_timeout, true), "
                "pg_catalog.set_config('transaction_timeout', :transaction_timeout, true)"
            ),
            {
                "lock_timeout": f"{timeouts.lock_timeout_ms}ms",
                "statement_timeout": f"{timeouts.statement_timeout_ms}ms",
                "transaction_timeout": f"{timeouts.transaction_timeout_ms}ms",
            },
        )
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not configure database compatibility timeouts"
        ) from error


async def acquire_compatibility_fence(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
    mode: CompatibilityFenceMode,
) -> None:
    """Acquire the profile-owned transaction advisory lock exactly once."""
    timeouts = (
        profile.participant_timeouts
        if mode is CompatibilityFenceMode.PARTICIPANT
        else profile.migration_timeouts
    )
    await configure_transaction_timeouts(connection, timeouts)
    function = (
        "pg_catalog.pg_advisory_xact_lock_shared"
        if mode is CompatibilityFenceMode.PARTICIPANT
        else "pg_catalog.pg_advisory_xact_lock"
    )
    try:
        await connection.execute(
            text(f"SELECT {function}(:class_id, :object_id)"),
            {"class_id": profile.fence_class_id, "object_id": profile.fence_object_id},
        )
    except DBAPIError as error:
        raise _failure_for_sqlstate(getattr(error.orig, "sqlstate", None)) from error
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not acquire database compatibility fence"
        ) from error


def _failure_for_sqlstate(sqlstate: object) -> DatabaseCompatibilityError:
    if sqlstate == "55P03":
        return DatabaseCompatibilityTimeout("database compatibility fence acquisition timed out")
    if sqlstate == "57014":
        return DatabaseQueryCancelled("database compatibility fence query was cancelled")
    if sqlstate == "25P04":
        return DatabaseCompatibilityTransactionTimeout(
            "database compatibility transaction timed out"
        )
    if type(sqlstate) is str and sqlstate.startswith("08"):
        return DatabaseCompatibilityOutcomeUnknown(
            "database compatibility fence outcome is unknown after connection loss"
        )
    return DatabaseCompatibilityError("could not acquire database compatibility fence")
