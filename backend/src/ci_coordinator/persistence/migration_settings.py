"""Admission for the deployment-owned PostgreSQL migration connection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

MIGRATION_DATABASE_DSN_ENV: Final = "CI_COORDINATOR_MIGRATION_DATABASE_DSN"
MIGRATION_DATABASE_URL_PLACEHOLDER: Final = "postgresql+psycopg://invalid/ci_coordinator"
MAX_MIGRATION_DATABASE_DSN_BYTES: Final = 4_096


def resolve_migration_database_url(
    configured_url: object,
    environment: Mapping[str, str],
) -> str:
    """Resolve a programmatic test URL or the deployment-only migration DSN."""
    candidate = (
        environment.get(MIGRATION_DATABASE_DSN_ENV)
        if configured_url == MIGRATION_DATABASE_URL_PLACEHOLDER
        else configured_url
    )
    if (
        type(candidate) is not str
        or not candidate
        or len(candidate.encode("utf-8")) > MAX_MIGRATION_DATABASE_DSN_BYTES
        or any(character in candidate for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError("migration database DSN is missing or invalid")
    try:
        url = make_url(candidate)
    except ArgumentError as error:
        raise ValueError("migration database DSN is missing or invalid") from error
    if url.drivername != "postgresql+psycopg" or not url.database:
        raise ValueError("migration database DSN must target PostgreSQL through psycopg")
    return candidate


def config_parser_url(value: str) -> str:
    """Escape URL percent signs for Alembic's ConfigParser-backed settings."""
    return value.replace("%", "%%")


def to_psycopg_connection_string(value: str) -> str:
    """Remove the SQLAlchemy driver qualifier without exposing credentials."""
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("migration database DSN must target PostgreSQL through psycopg")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)
