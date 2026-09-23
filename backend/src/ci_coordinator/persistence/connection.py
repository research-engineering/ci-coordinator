from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.errors import DatabaseCompatibilityError


def create_postgres_engine(
    database_url: str,
    *,
    pool_pre_ping: bool = True,
    pool_size: int = 5,
    pool_timeout_seconds: int = 30,
) -> AsyncEngine:
    if type(pool_size) is not int or not 1 <= pool_size <= 128:
        raise ValueError("database pool size must be an integer in [1, 128]")
    if type(pool_timeout_seconds) is not int or not 1 <= pool_timeout_seconds <= 3_600:
        raise ValueError("database pool timeout must be an integer in [1, 3600]")
    url = validate_postgres_url(database_url)
    return create_async_engine(
        url,
        pool_pre_ping=pool_pre_ping,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=pool_timeout_seconds,
        hide_parameters=True,
    )


def validate_postgres_url(database_url: str) -> URL:
    """Parse and admit a DSN without allocating an engine or network client."""
    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("database URL must use the postgresql+psycopg driver")
    return url


async def configure_read_committed(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
) -> AsyncConnection:
    """Configure the admitted isolation level before a transaction starts."""
    try:
        return await connection.execution_options(isolation_level=profile.isolation_level)
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not configure READ COMMITTED before starting a transaction"
        ) from error


async def verify_read_committed(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
) -> None:
    """Reject a transaction whose effective isolation differs from the profile."""
    try:
        effective = await connection.scalar(text("SHOW transaction_isolation"))
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not verify database transaction isolation"
        ) from error
    if effective != profile.isolation_expected_value:
        raise DatabaseCompatibilityError(
            "database transaction isolation does not satisfy the compatibility profile"
        )
