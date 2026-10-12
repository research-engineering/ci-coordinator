"""Native witnesses for recorded history population-bound provenance."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from ci_coordinator.ci_economics.history_administration import RepositoryCreationObserved
from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigured,
    HistoryPopulationSourceRefused,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_population_bound import (
    ci_history_population_bounds,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import audit_events, ci_history_datasets, ci_history_scans

from ._history_support import history_command, history_store

pytestmark = pytest.mark.persistence

_CREATED_AT = ARCHIVE_TIME.replace(hour=12, minute=34, second=56)
_BOUNDS = "ci_coordinator.ci_history_population_bounds"


def _catalog_command(created_at: datetime = _CREATED_AT, **changes: object) -> ConfigureHistory:
    bound = created_at.replace(hour=0, minute=0, second=0, microsecond=0)
    return ConfigureHistory.model_validate(
        {
            **history_command().model_dump(),
            "initialCreatedFrom": bound.isoformat(),
            "populationBoundSource": {
                "kind": "repository_created_at",
                "repositoryCreatedAt": created_at.isoformat(),
            },
            **changes,
        }
    )


def _expansion(created_from: datetime, operation_id: str) -> ConfigureHistory:
    return ConfigureHistory.model_validate(
        {
            **history_command().model_dump(),
            "expectedRevision": 1,
            "initialCreatedFrom": None,
            "expandCreatedFrom": created_from.isoformat(),
            "operationId": operation_id,
        }
    )


def test_bound_row_commits_atomically_with_audit_dataset_and_scan(
    runtime_postgres_database_url: str,
) -> None:
    # Kills: inserting provenance outside the configuration transaction, or omitting it.
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store, command = history_store(engine), _catalog_command()
        try:
            assert await store.find_history_receipt(command) is None
            result = await store.configure_history(
                command, verified_creation=RepositoryCreationObserved(command.scope, _CREATED_AT)
            )
            assert isinstance(result, HistoryConfigured) and not result.replayed
            async with engine.connect() as connection:
                rows = (await connection.execute(select(ci_history_population_bounds))).all()
                assert len(rows) == 1
                row = rows[0]._mapping
                assert (row["generation"], row["configuration_revision"]) == (1, 1)
                assert row["created_from"] == ARCHIVE_TIME
                assert row["source"] == "repository_created_at"
                assert row["repository_created_at"] == _CREATED_AT
                assert row["recorded_at"] == result.snapshot.configured_at
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 1
            status = await store.history_status(command.scope)
            assert status.current_population_bound is not None
            assert status.current_population_bound.source == "repository_created_at"
            assert status.current_population_bound.repository_created_at == _CREATED_AT
            assert await store.find_history_receipt(command) == HistoryConfigured(
                result.snapshot, replayed=True
            )

            earlier = ARCHIVE_TIME - timedelta(days=30)
            expanded = await store.configure_history(_expansion(earlier, "expand-explicit"))
            assert isinstance(expanded, HistoryConfigured)
            status = await store.history_status(command.scope)
            assert status.current_population_bound is not None
            assert status.current_population_bound.created_from == earlier
            assert status.current_population_bound.source == "explicit"
            assert status.current_population_bound.configuration_revision == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("verification", ["missing", "foreign-scope", "other-instant", "explicit"])
def test_store_rejects_catalog_source_without_matching_verification(
    runtime_postgres_database_url: str, verification: str
) -> None:
    # Kills: a caller that bypasses the app-layer provider read recording catalog provenance.
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command() if verification == "explicit" else _catalog_command()
        observed = {
            "missing": None,
            "foreign-scope": RepositoryCreationObserved(
                RepositoryScope(command.installation_id, 999), _CREATED_AT
            ),
            "other-instant": RepositoryCreationObserved(
                command.scope, _CREATED_AT + timedelta(seconds=1)
            ),
            "explicit": RepositoryCreationObserved(command.scope, _CREATED_AT),
        }[verification]
        try:
            with pytest.raises(CiEconomicsStoreUnavailable):
                await store.configure_history(command, verified_creation=observed)
            async with engine.connect() as connection:
                for table in (
                    audit_events,
                    ci_history_datasets,
                    ci_history_scans,
                    ci_history_population_bounds,
                ):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_future_creation_date_against_database_time_is_refused_without_audit(
    runtime_postgres_database_url: str,
) -> None:
    # Kills: trusting the provider or application clock over database time.
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        future = datetime(2999, 1, 1, 12, tzinfo=UTC)
        command = _catalog_command(future)
        try:
            assert await store.configure_history(
                command, verified_creation=RepositoryCreationObserved(command.scope, future)
            ) == HistoryPopulationSourceRefused("creation_date_in_future")
            async with engine.connect() as connection:
                for table in (audit_events, ci_history_datasets, ci_history_population_bounds):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_replay_with_catalog_source_requires_matching_row(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    # Kills: skipping the receipt rule that binds a catalog-source replay to its provenance.
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store, command = history_store(engine), _catalog_command()
        verified = RepositoryCreationObserved(command.scope, _CREATED_AT)
        try:
            first = await store.configure_history(command, verified_creation=verified)
            assert isinstance(first, HistoryConfigured)
            assert await store.configure_history(
                command, verified_creation=verified
            ) == HistoryConfigured(first.snapshot, replayed=True)
            owner = create_engine(postgres_database_url)
            try:
                with owner.begin() as connection:
                    connection.execute(delete(ci_history_population_bounds))
            finally:
                owner.dispose()
            with pytest.raises(CiEconomicsStoreUnavailable):
                await store.find_history_receipt(command)
            with pytest.raises(CiEconomicsStoreUnavailable):
                await store.configure_history(command, verified_creation=verified)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_old_writer_expansion_projects_unrecorded(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    # Kills: projecting the minimum row without requiring equality with the cursor bound.
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        earlier = ARCHIVE_TIME - timedelta(days=30)
        try:
            assert isinstance(await store.configure_history(history_command()), HistoryConfigured)
            assert isinstance(
                await store.configure_history(_expansion(earlier, "old-writer-expansion")),
                HistoryConfigured,
            )
            # A predecessor binary moves the cursor without writing provenance.
            owner = create_engine(postgres_database_url)
            try:
                with owner.begin() as connection:
                    connection.execute(
                        delete(ci_history_population_bounds).where(
                            ci_history_population_bounds.c.created_from == earlier
                        )
                    )
            finally:
                owner.dispose()
            status = await store.history_status(history_command().scope)
            assert status.scan is not None
            assert status.scan.checkpoint.cursor.created_from == earlier
            assert status.population_bound is not None
            assert status.population_bound.created_from == ARCHIVE_TIME
            assert status.current_population_bound is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "created_from,source,created_at",
    [
        ("2020-01-02T00:00:00Z", "repository_created_at", "2020-01-01T12:34:56Z"),
        ("2020-01-01T00:00:01Z", "repository_created_at", "2020-01-01T12:34:56Z"),
        ("2020-01-01T00:00:00Z", "repository_created_at", None),
        ("2020-01-01T00:00:00Z", "explicit", "2020-01-01T12:34:56Z"),
        ("2020-01-01T00:00:00Z", "guessed", None),
        ("2020-01-01T00:00:00.5Z", "explicit", None),
        ("2020-01-01T00:00:00Z", "repository_created_at", "2999-01-01T12:00:00Z"),
    ],
)
def test_catalog_floor_constraint_rejects_mismatched_bound(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    created_from: str,
    source: str,
    created_at: str | None,
) -> None:
    # Kills: dropping ck_ci_history_population_bounds_catalog_floor, _source or _identity.
    async def configure() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            assert isinstance(
                await history_store(engine).configure_history(history_command()),
                HistoryConfigured,
            )
        finally:
            await engine.dispose()

    asyncio.run(configure())
    owner = create_engine(postgres_database_url)
    try:
        with (
            owner.connect() as connection,
            pytest.raises(IntegrityError, match="ck_ci_history_population_bounds"),
        ):
            connection.execute(
                text(
                    f"INSERT INTO {_BOUNDS} (installation_id, repository_id, generation, "
                    "created_from, source, repository_created_at, configuration_revision, "
                    "recorded_at) VALUES (101, 202, 2, CAST(:created_from AS timestamptz), "
                    ":source, CAST(:created_at AS timestamptz), 1, clock_timestamp())"
                ),
                {"created_from": created_from, "source": source, "created_at": created_at},
            )
    finally:
        owner.dispose()


def test_runtime_role_cannot_update_or_delete_bound_rows(
    runtime_postgres_database_url: str,
) -> None:
    # Kills: widening the runtime ACL beyond append-only SELECT and INSERT.
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            assert isinstance(
                await history_store(engine).configure_history(history_command()),
                HistoryConfigured,
            )
            for statement in (
                f"UPDATE {_BOUNDS} SET source = 'explicit'",
                f"DELETE FROM {_BOUNDS}",
                f"TRUNCATE {_BOUNDS}",
            ):
                async with engine.connect() as connection:
                    direct = await connection.begin()
                    try:
                        with pytest.raises(DBAPIError, match="permission denied"):
                            await connection.execute(text(statement))
                    finally:
                        await direct.rollback()
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_history_population_bounds)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
