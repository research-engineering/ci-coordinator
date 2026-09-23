from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from ci_coordinator.ci_economics import initial_collection_state, load_bundled_ci_economics_profile
from ci_coordinator.persistence import (
    PostgresCiEconomicsUnitOfWork,
    compatibility_profile,
    migration_result_attestation,
)
from ci_coordinator.persistence.ci_economics_collection_codec import (
    decode_collection_state,
    encode_collection_record,
)
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG
from ci_coordinator.persistence.ci_economics_v4_schema_contract import V4_CATALOG
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityTimeouts,
    load_bundled_profile,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.schema import ci_workflow_attempt_collections as collections
from ci_coordinator.persistence.schema_capabilities import (
    CI_ECONOMICS_EVIDENCE_V3,
    CI_ECONOMICS_EVIDENCE_V4,
)

from ._ci_economics_support import provider_source
from .conftest import alembic_config

pytestmark = pytest.mark.persistence
_BEFORE = "20260913_0013"
_RETIRED = "20260915_0014"
_AFTER = "20260915_0015"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _population() -> list[dict[str, object]]:
    source = provider_source(5000, _NOW)
    policy = load_bundled_ci_economics_profile().collection_policy
    pending = initial_collection_state(source.source_id, _NOW, _NOW, policy)
    leased = replace(
        pending,
        status="leased",
        revision=1,
        attempt_count=1,
        claim_generation=1,
        next_attempt_at=None,
        lease_owner_id="c" * 64,
        lease_token="d" * 64,
        lease_acquired_at=_NOW,
        lease_expires_at=_NOW + timedelta(seconds=60),
    )
    deferred = replace(
        pending,
        status="deferred",
        revision=2,
        attempt_count=1,
        claim_generation=1,
        last_failure_reason="provider_unavailable",
    )
    captured = replace(
        deferred,
        status="captured",
        next_attempt_at=None,
        final_outcome="captured",
        completed_at=_NOW + timedelta(seconds=1),
    )
    terminal = replace(
        captured,
        status="terminal_unavailable",
        final_outcome="terminal_unavailable",
        terminal_reason="deadline_exceeded",
    )
    states = (
        pending,
        leased,
        replace(leased, last_failure_reason="provider_unavailable"),
        deferred,
        captured,
        replace(captured, last_failure_reason=None),
        terminal,
        replace(
            terminal,
            terminal_reason="attempts_exhausted",
            attempt_count=policy.maximum_attempts,
            claim_generation=policy.maximum_attempts,
            revision=policy.maximum_attempts,
        ),
        replace(
            terminal, terminal_reason="evidence_conflict", last_failure_reason="evidence_conflict"
        ),
        replace(captured, status="expired", expired_at=captured.evidence_retain_until),
        replace(terminal, status="expired", expired_at=terminal.evidence_retain_until),
    )
    rows = []
    for index, state in enumerate(states):
        own_source = provider_source(5000 + index, _NOW)
        row = encode_collection_record(replace(state, subject_id=own_source.source_id), own_source)
        assert decode_collection_state(row).status == state.status
        rows.append(row)
    return rows


def _capabilities(connection: Connection, revision: str) -> set[tuple[str, str]]:
    return {
        (row[0], row[1])
        for row in connection.execute(
            text(
                "SELECT capability_id, descriptor_hash FROM "
                "ci_coordinator.database_compatibility_capabilities WHERE revision_id = :revision"
            ),
            {"revision": revision},
        )
    }


def _rows(connection: Connection) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(select(collections).order_by(collections.c.subject_id))
    )


def _history(connection: Connection) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT * FROM ci_coordinator.database_compatibility_declarations "
                "ORDER BY generation"
            )
        )
    )


def test_total_catalog_accepts_valid_population_and_rejects_isolated_nulls(
    postgres_database_url: str,
) -> None:
    engine = create_engine(postgres_database_url)
    try:
        with engine.begin() as connection:
            rows = _population()
            assert {row["status"] for row in rows} == {
                "pending",
                "leased",
                "deferred",
                "captured",
                "terminal_unavailable",
                "expired",
            }
            connection.execute(collections.insert(), rows)
            assert ci_economics_schema_matches_contract_sync(connection, contract=V4_CATALOG)
            assert not ci_economics_schema_matches_contract_sync(connection, contract=V3_CATALOG)
            for row in rows:
                columns = (
                    ("lease_owner_id", "lease_token", "lease_acquired_at", "lease_expires_at")
                    if row["status"] == "leased"
                    else ("final_outcome",)
                    if row["final_outcome"] is not None
                    else ()
                )
                for column in columns:
                    mutant = {**row, column: None}
                    with pytest.raises(ValueError):
                        decode_collection_state(mutant)
                    with (
                        pytest.raises(IntegrityError, match="ck_ci_workflow_attempt_collections"),
                        connection.begin_nested(),
                    ):
                        connection.execute(
                            collections.update()
                            .where(collections.c.subject_id == row["subject_id"])
                            .values({column: None})
                        )
            assert len(_rows(connection)) == len(rows)
    finally:
        engine.dispose()


def test_forward_pair_preserves_rows_and_rolls_back_rejected_final_attestation(
    unmigrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _BEFORE)
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(collections.insert(), _population())
            before = _rows(connection)
            history = _history(connection)
            capabilities = _capabilities(connection, _BEFORE)
        profile = load_bundled_profile()
        with engine.begin() as holder, monkeypatch.context() as patch:
            holder.execute(
                text("SELECT pg_advisory_xact_lock_shared(:class_id, :object_id)"),
                {"class_id": profile.fence_class_id, "object_id": profile.fence_object_id},
            )
            patch.setattr(
                compatibility_profile,
                "load_bundled_profile",
                lambda: replace(
                    profile, migration_timeouts=CompatibilityTimeouts(100, 5000, 10000)
                ),
            )
            with pytest.raises(DBAPIError) as rejected:
                command.upgrade(config, _AFTER)
            assert getattr(rejected.value.orig, "sqlstate", None) == "55P03"
            assert holder.scalar(text("SELECT version_num FROM public.alembic_version")) == _BEFORE
            assert _history(holder) == history
            assert _rows(holder) == before
        with monkeypatch.context() as patch:
            patch.setitem(
                migration_result_attestation._CAPABILITY_ATTESTORS,
                "ci-economics-evidence/v4",
                lambda _connection: False,
            )
            with pytest.raises(RuntimeError, match="ci-economics-evidence/v4"):
                command.upgrade(config, _AFTER)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version")) == _BEFORE
            )
            assert _history(connection) == history
            assert _rows(connection) == before
            assert ci_economics_schema_matches_contract_sync(connection, contract=V3_CATALOG)
        command.upgrade(config, _AFTER)
        command.upgrade(config, _AFTER)
        with engine.connect() as connection:
            old = CI_ECONOMICS_EVIDENCE_V3.declaration()
            new = CI_ECONOMICS_EVIDENCE_V4.declaration()
            retained = capabilities - {(old.capability_id, old.descriptor_hash)}
            assert _capabilities(connection, _RETIRED) == retained
            assert _capabilities(connection, _AFTER) == retained | {
                (new.capability_id, new.descriptor_hash)
            }
            assert _history(connection)[:-2] == history
            assert _rows(connection) == before
            assert ci_economics_schema_matches_contract_sync(connection, contract=V4_CATALOG)
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, _BEFORE)
    finally:
        engine.dispose()


def test_invalid_retained_unknown_states_reject_migration_without_repair(
    unmigrated_database_url: str,
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _BEFORE)
    engine = create_engine(unmigrated_database_url)
    try:
        candidates = [
            {**row, column: None}
            for row in _population()
            for column in (
                ("lease_owner_id",)
                if row["status"] == "leased"
                else ("final_outcome",)
                if row["final_outcome"] is not None
                else ()
            )
        ]
        for mutant in candidates:
            with engine.begin() as connection:
                connection.execute(collections.insert(), mutant)
                before = _rows(connection)
                history = _history(connection)
            with pytest.raises(RuntimeError, match="without data repair"):
                command.upgrade(config, _AFTER)
            with engine.begin() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                    == _BEFORE
                )
                assert _history(connection) == history
                assert _rows(connection) == before
                assert ci_economics_schema_matches_contract_sync(connection, contract=V3_CATALOG)
                # Isolated fixture reset must not impersonate the guarded retention lifecycle.
                connection.execute(
                    text("TRUNCATE ci_coordinator.ci_workflow_attempt_collections CASCADE")
                )
    finally:
        engine.dispose()


def test_current_economics_rejects_explicit_intermediate_head(
    unmigrated_database_url: str,
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _RETIRED)

    async def scenario() -> None:
        engine = create_postgres_engine(unmigrated_database_url)
        try:
            with pytest.raises(DatabaseCapabilityUnavailable, match="every required capability"):
                async with PostgresCiEconomicsUnitOfWork(engine):
                    pytest.fail("intermediate migration must not expose economics repositories")
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    command.upgrade(config, _AFTER)
