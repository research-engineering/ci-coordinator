from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_settings import (
    config_parser_url,
    resolve_migration_database_url,
)
from ci_coordinator.persistence.schema import metadata

config = context.config
target_metadata = metadata


def run_migrations_offline() -> None:
    raise RuntimeError("database compatibility migrations require online transactional execution")


def run_migrations_online() -> None:
    configured_url = config.get_main_option("sqlalchemy.url")
    migration_url = resolve_migration_database_url(configured_url, os.environ)
    if configured_url != migration_url:
        config.set_main_option("sqlalchemy.url", config_parser_url(migration_url))
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        hide_parameters=True,
    )
    with connectable.connect() as connection:
        profile = load_bundled_profile()
        with connection.begin():
            connection.execute(
                text(
                    "SELECT pg_catalog.set_config('search_path', "
                    "'pg_catalog', true), "
                    "pg_catalog.set_config('lock_timeout', :lock_timeout, true), "
                    "pg_catalog.set_config('statement_timeout', :statement_timeout, true), "
                    "pg_catalog.set_config('transaction_timeout', :transaction_timeout, true)"
                ),
                {
                    "lock_timeout": f"{profile.migration_timeouts.lock_timeout_ms}ms",
                    "statement_timeout": f"{profile.migration_timeouts.statement_timeout_ms}ms",
                    "transaction_timeout": f"{profile.migration_timeouts.transaction_timeout_ms}ms",
                },
            )
            connection.execute(
                text("SELECT pg_catalog.pg_advisory_xact_lock(:class_id, :object_id)"),
                {"class_id": profile.fence_class_id, "object_id": profile.fence_object_id},
            )
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                transaction_per_migration=False,
                transactional_ddl=True,
                version_table_schema="public",
            )
            with context.begin_transaction():
                context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
