from __future__ import annotations

import pytest

from ci_coordinator.persistence.migration_settings import (
    MIGRATION_DATABASE_DSN_ENV,
    MIGRATION_DATABASE_URL_PLACEHOLDER,
    config_parser_url,
    resolve_migration_database_url,
    to_psycopg_connection_string,
)


def test_placeholder_requires_and_admits_deployment_migration_dsn() -> None:
    dsn = "postgresql+psycopg://migration:p%40ss@database/coordinator"

    assert (
        resolve_migration_database_url(
            MIGRATION_DATABASE_URL_PLACEHOLDER,
            {MIGRATION_DATABASE_DSN_ENV: dsn},
        )
        == dsn
    )
    assert config_parser_url(dsn) == dsn.replace("%", "%%")
    assert to_psycopg_connection_string(dsn) == "postgresql://migration:p%40ss@database/coordinator"


def test_explicit_programmatic_url_does_not_require_environment() -> None:
    dsn = "postgresql+psycopg://migration@database/coordinator"

    assert resolve_migration_database_url(dsn, {}) == dsn


@pytest.mark.parametrize(
    "configured_url",
    [
        MIGRATION_DATABASE_URL_PLACEHOLDER,
        "sqlite:///coordinator.sqlite",
        "postgresql+psycopg://migration@database",
        "postgresql+psycopg://migration@database/coordinator\nignored",
    ],
)
def test_migration_database_url_rejects_missing_or_unsupported_values(
    configured_url: str,
) -> None:
    with pytest.raises(ValueError, match="migration database DSN"):
        resolve_migration_database_url(configured_url, {})


def test_psycopg_connection_string_rejects_an_unowned_driver() -> None:
    with pytest.raises(ValueError, match="PostgreSQL through psycopg"):
        to_psycopg_connection_string("postgresql+asyncpg://migration@database/coordinator")
