"""Native migration, catalog and runtime-role witnesses for history population bounds."""

from __future__ import annotations

import asyncio
import json
from types import MappingProxyType

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url

from ci_coordinator.persistence import (
    migration_result_attestation,
    principal_attestation,
    runtime_principal_access,
)
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_history_population_bound_schema_contract import (
    history_population_bound_schema_matches_sync,
)
from ci_coordinator.persistence.ci_history_schema_contract import HISTORY_CATALOG
from ci_coordinator.persistence.ci_history_unit_of_work import (
    PostgresHistoryAdministrationUnitOfWork,
    PostgresHistoryUnitOfWork,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.schema_capabilities import (
    CI_HISTORY_ARCHIVE,
    CI_HISTORY_POPULATION_BOUND,
)

from .conftest import RUNTIME_PASSWORD, RUNTIME_ROLE, _provision_runtime_principal, alembic_config

pytestmark = pytest.mark.persistence

_PREDECESSOR = "20261011_0019"
_SUCCESSOR = "20261012_0020"
_BOUNDS = "ci_coordinator.ci_history_population_bounds"
_SUCCESSOR_ONLY_RELATIONS = frozenset({"ci_history_population_bounds"})


def project_predecessor_runtime_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project the 20261011_0019 binary's exact runtime manifest into the bind parameters.

    The predecessor's grants are the successor's minus the relation this expansion adds, so
    the version-shared apply and attestation code can stand in for the predecessor image.
    """
    table_grants = MappingProxyType(
        {
            relation: privileges
            for relation, privileges in runtime_principal_access.RUNTIME_TABLE_GRANTS.items()
            if relation not in _SUCCESSOR_ONLY_RELATIONS
        }
    )
    for module in (runtime_principal_access, principal_attestation):
        monkeypatch.setattr(module, "RUNTIME_TABLE_GRANTS", table_grants)
    monkeypatch.setattr(
        principal_attestation,
        "_EXPECTED_TABLE_PRIVILEGES_JSON",
        _canonical_json(
            [
                {
                    "allowed": privilege in granted,
                    "privilege_type": privilege,
                    "relation_name": name,
                }
                for name, granted in table_grants.items()
                for privilege in runtime_principal_access.RUNTIME_TABLE_PRIVILEGES
            ]
        ),
    )
    monkeypatch.setattr(
        principal_attestation, "_EXPECTED_TABLE_GRANTS_JSON", _canonical_json(dict(table_grants))
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _capabilities(connection: Connection, revision: str) -> set[tuple[str, str]]:
    return {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            text(
                "SELECT capability_id, descriptor_hash FROM "
                "ci_coordinator.database_compatibility_capabilities WHERE revision_id = :revision"
            ),
            {"revision": revision},
        )
    }


def _runtime_url(database_url: str) -> str:
    return (
        make_url(database_url)
        .set(username=RUNTIME_ROLE, password=RUNTIME_PASSWORD)
        .render_as_string(hide_password=False)
    )


def test_forward_0020_is_atomic_preserves_0019_and_adds_only_population_bound(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kills: a mixed add/remove successor, retiring archive v1, or a non-atomic expansion.
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _PREDECESSOR)
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.connect() as connection:
            prior = _capabilities(connection, _PREDECESSOR)
        with monkeypatch.context() as patch:
            patch.setitem(
                migration_result_attestation._CAPABILITY_ATTESTORS,
                CI_HISTORY_POPULATION_BOUND.capability_id,
                lambda _connection: False,
            )
            with pytest.raises(RuntimeError, match="ci-actions-history-population-bound"):
                command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == _PREDECESSOR
            )
            assert connection.scalar(text(f"SELECT to_regclass('{_BOUNDS}')")) is None
            assert _capabilities(connection, _PREDECESSOR) == prior
            assert _capabilities(connection, _SUCCESSOR) == set()
        command.upgrade(config, _SUCCESSOR)
        command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert history_population_bound_schema_matches_sync(connection)
            assert ci_economics_schema_matches_contract_sync(connection, contract=HISTORY_CATALOG)
            added = CI_HISTORY_POPULATION_BOUND.declaration()
            archive = CI_HISTORY_ARCHIVE.declaration()
            assert _capabilities(connection, _SUCCESSOR) == prior | {
                (added.capability_id, added.descriptor_hash)
            }
            assert (archive.capability_id, archive.descriptor_hash) in prior
            # The backfill state is an empty relation: no provenance is guessed.
            assert connection.scalar(text(f"SELECT count(*) FROM {_BOUNDS}")) == 0
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege('public', :table, 'SELECT')"),
                    {"table": _BOUNDS},
                )
                is False
            )
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, _PREDECESSOR)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("damage", "repair"),
    [
        (
            f"ALTER TABLE {_BOUNDS} DROP CONSTRAINT ck_ci_history_population_bounds_catalog_floor",
            f"ALTER TABLE {_BOUNDS} ADD CONSTRAINT ck_ci_history_population_bounds_catalog_floor "
            "CHECK (repository_created_at IS NULL OR "
            "isfinite(repository_created_at) AND repository_created_at <= recorded_at AND "
            "created_from = (date_trunc('day', repository_created_at AT TIME ZONE 'UTC') "
            "AT TIME ZONE 'UTC'))",
        ),
        (
            f"ALTER TABLE {_BOUNDS} DROP CONSTRAINT fk_ci_history_population_bounds_dataset",
            f"ALTER TABLE {_BOUNDS} ADD CONSTRAINT fk_ci_history_population_bounds_dataset "
            "FOREIGN KEY (installation_id, repository_id) REFERENCES "
            "ci_coordinator.ci_history_datasets (installation_id, repository_id) "
            "ON DELETE RESTRICT",
        ),
    ],
    ids=("catalog-floor-dropped", "dataset-fk-dropped"),
)
def test_damaged_population_bound_catalog_fails_closed_only_for_administration(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    damage: str,
    repair: str,
) -> None:
    # Kills: an admission that ignores the new catalog, or one that also blocks the worker.
    owner = create_engine(postgres_database_url)

    async def attempt() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            with pytest.raises(DatabaseCapabilityUnavailable, match="population-bound"):
                async with PostgresHistoryAdministrationUnitOfWork(runtime):
                    pytest.fail("damaged population-bound catalog exposed history administration")
            async with PostgresHistoryUnitOfWork(runtime) as transaction:
                await transaction.rollback()
        finally:
            await runtime.dispose()

    try:
        with owner.begin() as connection:
            assert history_population_bound_schema_matches_sync(connection)
            connection.execute(text(damage))
            assert not history_population_bound_schema_matches_sync(connection)
        asyncio.run(attempt())
    finally:
        with owner.begin() as connection:
            connection.execute(text(repair))
            assert history_population_bound_schema_matches_sync(connection)
        owner.dispose()


def test_predecessor_manifest_role_admits_predecessor_closure_after_expand(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kills: the migration granting runtime privileges itself, or retiring a predecessor
    # capability that a drained predecessor image still requires.
    command.upgrade(alembic_config(unmigrated_database_url), _SUCCESSOR)
    project_predecessor_runtime_acl(monkeypatch)
    _provision_runtime_principal(unmigrated_database_url)

    async def attempt() -> None:
        runtime = create_postgres_engine(_runtime_url(unmigrated_database_url))
        try:
            async with PostgresHistoryUnitOfWork(runtime) as transaction:
                await transaction.rollback()
        finally:
            await runtime.dispose()

    asyncio.run(attempt())
    owner = create_engine(unmigrated_database_url)
    try:
        with owner.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
                    {"role": RUNTIME_ROLE, "table": _BOUNDS},
                )
                is False
            )
    finally:
        owner.dispose()


def test_successor_grants_are_rejected_by_predecessor_manifest_attestation(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kills: a claim of shared-role coexistence; pins the fail-closed rollout fence.
    command.upgrade(alembic_config(unmigrated_database_url), _SUCCESSOR)
    _provision_runtime_principal(unmigrated_database_url)
    project_predecessor_runtime_acl(monkeypatch)

    async def attempt() -> None:
        runtime = create_postgres_engine(_runtime_url(unmigrated_database_url))
        try:
            with pytest.raises(DatabaseCapabilityUnavailable, match="not capability-restricted"):
                async with PostgresHistoryUnitOfWork(runtime):
                    pytest.fail("predecessor manifest admitted a successor-provisioned role")
        finally:
            await runtime.dispose()

    asyncio.run(attempt())
