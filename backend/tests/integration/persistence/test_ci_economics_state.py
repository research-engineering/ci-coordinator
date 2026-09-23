from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration.persistence._ci_economics_support import (
    _CONTRACT,
    subject_scope,
)
from tests.integration.persistence._ci_economics_support import (
    database_now as _database_now,
)
from tests.integration.persistence._ci_economics_support import (
    snapshot as _snapshot,
)
from tests.integration.persistence._ci_economics_support import (
    store as _store,
)
from tests.integration.persistence._ci_economics_support import (
    subject as _subject,
)
from tests.integration.persistence._ci_economics_support import (
    terminalize_reconciliation as _terminalize_reconciliation,
)

from ci_coordinator.ci_economics import (
    CiEconomicsStoreUnavailable,
    CollectionState,
)
from ci_coordinator.ci_economics.sources import ReconciliationCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence import (
    PostgresCiEconomicsUnitOfWork,
)
from ci_coordinator.persistence.ci_economics_collection_codec import (
    decode_collection_state,
    encode_collection_record,
)
from ci_coordinator.persistence.ci_economics_repository import (
    _PostgresCiEconomicsRepository,
)
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract,
    ci_economics_schema_mismatch_details_sync,
    ci_economics_schema_mismatches_sync,
)
from ci_coordinator.persistence.ci_economics_v4_schema_contract import V4_CATALOG
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshot_jobs,
    ci_workflow_attempt_snapshots,
    reconciliation_subjects,
)
from ci_coordinator.reconciliation import (
    ReconciliationSubject,
)

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize(
    ("remove_object", "install_same_name_drift", "expected_mismatch"),
    (
        (
            "ALTER TABLE ci_coordinator.ci_workflow_observations ALTER COLUMN "
            "retain_until DROP DEFAULT",
            "ALTER TABLE ci_coordinator.ci_workflow_observations ALTER COLUMN "
            "retain_until SET DEFAULT statement_timestamp() + INTERVAL '89 days'",
            "columns",
        ),
        (
            "ALTER TABLE ci_coordinator.ci_workflow_observations DROP CONSTRAINT "
            "ck_ci_workflow_observations_canonical_byte_limit",
            "ALTER TABLE ci_coordinator.ci_workflow_observations ADD CONSTRAINT "
            "ck_ci_workflow_observations_canonical_byte_limit CHECK "
            "(octet_length(observation_canonical_json) <= 16384)",
            "constraint-definitions",
        ),
        (
            "DROP INDEX ci_coordinator.ix_ci_workflow_observations_attempt_jobs",
            "CREATE INDEX ix_ci_workflow_observations_attempt_jobs ON "
            "ci_coordinator.ci_workflow_observations (delivery_id)",
            "index-definitions",
        ),
        (
            "DROP TRIGGER tr_ci_workflow_observations_retention_guard ON "
            "ci_coordinator.ci_workflow_observations",
            "CREATE TRIGGER tr_ci_workflow_observations_retention_guard "
            "BEFORE UPDATE OR DELETE ON ci_coordinator.ci_workflow_observations "
            "FOR EACH ROW WHEN (false) EXECUTE FUNCTION "
            "ci_coordinator.guard_ci_economics_mutation()",
            "triggers",
        ),
        (
            "DROP TRIGGER tr_ci_workflow_attempt_collections_retention_guard ON "
            "ci_coordinator.ci_workflow_attempt_collections",
            "CREATE TRIGGER tr_ci_workflow_attempt_collections_retention_guard "
            "BEFORE DELETE ON ci_coordinator.ci_workflow_attempt_collections "
            "FOR EACH ROW WHEN (false) EXECUTE FUNCTION "
            "ci_coordinator.guard_ci_economics_mutation()",
            "triggers",
        ),
    ),
)
def test_catalog_attestation_rejects_same_name_semantic_drift(
    postgres_database_url: str,
    remove_object: str,
    install_same_name_drift: str,
    expected_mismatch: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await ci_economics_schema_matches_contract(
                        connection, contract=V4_CATALOG
                    )
                    await connection.execute(text(remove_object))
                    await connection.execute(text(install_same_name_drift))
                    assert not await ci_economics_schema_matches_contract(
                        connection, contract=V4_CATALOG
                    )
                    mismatches = await connection.run_sync(
                        ci_economics_schema_mismatches_sync, contract=V4_CATALOG
                    )
                    assert expected_mismatch in mismatches
                    details = await connection.run_sync(
                        ci_economics_schema_mismatch_details_sync, contract=V4_CATALOG
                    )
                    assert any(
                        component == expected_mismatch and detail for component, detail in details
                    )
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_catalog_attestation_binds_triggers_to_the_canonical_routine_schema(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await ci_economics_schema_matches_contract(
                        connection, contract=V4_CATALOG
                    )
                    await connection.execute(text("CREATE SCHEMA ci_economics_attestation_drift"))
                    await connection.execute(
                        text(
                            "CREATE FUNCTION "
                            "ci_economics_attestation_drift.guard_ci_economics_mutation() "
                            "RETURNS trigger LANGUAGE plpgsql AS $$ "
                            "BEGIN RETURN OLD; END; $$"
                        )
                    )
                    await connection.execute(
                        text(
                            "DROP TRIGGER tr_ci_workflow_observations_retention_guard ON "
                            "ci_coordinator.ci_workflow_observations"
                        )
                    )
                    await connection.execute(
                        text(
                            "CREATE TRIGGER tr_ci_workflow_observations_retention_guard "
                            "BEFORE UPDATE OR DELETE ON ci_coordinator.ci_workflow_observations "
                            "FOR EACH ROW EXECUTE FUNCTION "
                            "ci_economics_attestation_drift.guard_ci_economics_mutation()"
                        )
                    )

                    details = dict(
                        await connection.run_sync(
                            ci_economics_schema_mismatch_details_sync, contract=V4_CATALOG
                        )
                    )
                    assert tuple(details) == ("triggers",)
                    assert "ci_economics_attestation_drift" in details["triggers"]
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_catalog_attestation_rejects_relation_rewrite_rules(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await ci_economics_schema_matches_contract(
                        connection, contract=V4_CATALOG
                    )
                    await connection.execute(
                        text(
                            "CREATE RULE suppress_observation_insert AS ON INSERT TO "
                            "ci_coordinator.ci_workflow_observations DO INSTEAD NOTHING"
                        )
                    )
                    assert not await ci_economics_schema_matches_contract(
                        connection, contract=V4_CATALOG
                    )
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_attempt_snapshot_is_atomic_scoped_and_honest_about_missing_evidence(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        now = await _database_now(engine)
        subject = _subject(303)
        store = _store(engine)
        try:
            await _terminalize_reconciliation(engine, subject, now)
            assert await store.register_eligible(limit=10) == 1
            claim = await store.claim_next(worker_id="1" * 64)
            assert claim is not None

            snapshot = _snapshot(subject, now)
            assert await store.record_snapshot(claim, snapshot) == "captured"
            assert await store.record_snapshot(claim, snapshot) == "claim_lost"

            page = await store.list_attempts(subject_scope(subject), after_cursor=None, limit=10)
            foreign = await store.list_attempts(
                RepositoryScope(subject.installation_id, subject.repository_id + 1),
                after_cursor=None,
                limit=10,
            )
            economics = await store.load_attempt(snapshot.attempt)

            assert len(page.items) == 1
            assert page.items[0].attempt == snapshot.attempt
            assert foreign.items == ()
            assert economics is not None
            assert economics.queue.quality == "unknown"
            assert economics.attempt_wall.quality == "unknown"
            assert economics.runner_occupancy.quality == "exact"
            assert economics.runner_occupancy.known_value_ms == 3_000
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_two_replicas_cannot_claim_one_economics_subject(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        now = await _database_now(engine)
        subject = _subject(304)
        try:
            await _terminalize_reconciliation(engine, subject, now)
            assert await _store(engine).register_eligible(limit=10) == 1

            left, right = await asyncio.gather(
                _store(engine).claim_next(worker_id="1" * 64),
                _store(engine).claim_next(worker_id="2" * 64),
            )

            claims = tuple(claim for claim in (left, right) if claim is not None)
            assert len(claims) == 1
            assert claims[0].generation == claims[0].attempt_count == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_runtime_role_cannot_delete_a_live_collection(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        now = await _database_now(engine)
        subject = _subject(310)
        store = _store(engine)
        try:
            await _terminalize_reconciliation(engine, subject, now)
            assert await store.register_eligible(limit=10) == 1

            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    with pytest.raises(DBAPIError, match="tombstone has not expired"):
                        await connection.execute(
                            delete(ci_workflow_attempt_collections).where(
                                ci_workflow_attempt_collections.c.subject_id == subject.subject_id
                            )
                        )
                finally:
                    await transaction.rollback()

            async with engine.connect() as connection:
                retained = await connection.scalar(
                    select(func.count())
                    .select_from(ci_workflow_attempt_collections)
                    .where(ci_workflow_attempt_collections.c.subject_id == subject.subject_id)
                )
            assert retained == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("expired", "source_age_days"),
    ((True, 89), (False, 92)),
    ids=("expired-before-tombstone", "non-expired-after-tombstone"),
)
def test_runtime_role_cannot_delete_when_either_tombstone_guard_condition_holds(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    expired: bool,
    source_age_days: int,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        now = await _database_now(runtime_engine)
        subject = _subject(313 if expired else 314)
        state = _terminal_unavailable_collection_state(
            subject,
            now,
            source_age_days=source_age_days,
            expired=expired,
        )
        try:
            await _terminalize_reconciliation(runtime_engine, subject, now)
            async with admin_engine.begin() as connection:
                await connection.execute(
                    insert(ci_workflow_attempt_collections).values(
                        encode_collection_record(
                            state, ReconciliationCollectionSource(subject, _CONTRACT)
                        )
                    )
                )

            async with runtime_engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    with pytest.raises(DBAPIError, match="tombstone has not expired"):
                        await connection.execute(
                            delete(ci_workflow_attempt_collections).where(
                                ci_workflow_attempt_collections.c.subject_id == subject.subject_id
                            )
                        )
                finally:
                    await transaction.rollback()
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_acquisition_cas_rejects_a_lease_that_expired_before_the_write(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        now = await _database_now(engine)
        subject = _subject(312)
        store = _store(engine)
        try:
            await _terminalize_reconciliation(engine, subject, now)
            assert await store.register_eligible(limit=10) == 1

            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                repository = transaction.ci_economics_collection
                selected = await repository._lock_next_due()
                assert selected is not None
                previous, source, _database_time = selected
                assert source.source_id == previous.subject_id
                successor = replace(
                    previous,
                    status="leased",
                    revision=previous.revision + 1,
                    attempt_count=previous.attempt_count + 1,
                    next_attempt_at=None,
                    claim_generation=previous.claim_generation + 1,
                    lease_owner_id="1" * 64,
                    lease_token="2" * 64,
                    lease_acquired_at=previous.source_created_at,
                    lease_expires_at=previous.source_created_at + timedelta(microseconds=1),
                )

                assert not await repository._write_supervisor_transition(previous, successor)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_runtime_role_can_purge_only_an_expired_due_tombstone(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        now = await _database_now(runtime_engine)
        subject = _subject(311)
        state = _terminal_unavailable_collection_state(
            subject,
            now,
            source_age_days=92,
            expired=True,
        )
        try:
            await _terminalize_reconciliation(runtime_engine, subject, now)
            async with admin_engine.begin() as connection:
                await connection.execute(
                    insert(ci_workflow_attempt_collections).values(
                        encode_collection_record(
                            state, ReconciliationCollectionSource(subject, _CONTRACT)
                        )
                    )
                )

            assert await _store(runtime_engine).purge_tombstones(limit=10) == 1

            async with runtime_engine.connect() as connection:
                retained = await connection.scalar(
                    select(func.count())
                    .select_from(ci_workflow_attempt_collections)
                    .where(ci_workflow_attempt_collections.c.subject_id == subject.subject_id)
                )
            assert retained == 0
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_captured_evidence_expires_purges_and_cannot_reregister(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        now = await _database_now(runtime_engine)
        subject = _subject(315)
        fresh_subject = _subject(316)
        store = _store(runtime_engine)
        try:
            await _terminalize_reconciliation(runtime_engine, subject, now)
            assert await store.register_eligible(limit=10) == 1
            claim = await store.claim_next(worker_id="1" * 64)
            assert claim is not None
            snapshot = _snapshot(subject, now - timedelta(days=92))
            assert await store.record_snapshot(claim, snapshot) == "captured"
            measured = await store.load_measurements(snapshot.attempt)
            assert measured is not None and measured.attempt == snapshot.attempt
            assert measured.measurements.runner_occupancy.known_value_ms == 3_000

            await _project_captured_attempt_to_later_database_epoch(
                admin_engine,
                subject.subject_id,
                age_days=92,
            )
            await _attest_projected_retention_epoch(admin_engine, subject.subject_id)
            assert await store.load_attempt(snapshot.attempt) is not None
            assert await store.load_measurements(snapshot.attempt) is None

            assert await store.expire_evidence(limit=10) == 1
            async with runtime_engine.connect() as connection:
                tombstone = (
                    await connection.execute(
                        select(
                            ci_workflow_attempt_collections.c.status,
                            ci_workflow_attempt_collections.c.final_outcome,
                            ci_workflow_attempt_collections.c.expired_at,
                        ).where(ci_workflow_attempt_collections.c.subject_id == subject.subject_id)
                    )
                ).one()
                snapshot_count = await connection.scalar(
                    select(func.count())
                    .select_from(ci_workflow_attempt_snapshots)
                    .where(ci_workflow_attempt_snapshots.c.subject_id == subject.subject_id)
                )
                job_count = await connection.scalar(
                    select(func.count())
                    .select_from(ci_workflow_attempt_snapshot_jobs)
                    .where(ci_workflow_attempt_snapshot_jobs.c.subject_id == subject.subject_id)
                )
            assert tuple(tombstone)[:2] == ("expired", "captured")
            assert tombstone.expired_at is not None
            assert snapshot_count == job_count == 0
            assert await store.load_attempt(snapshot.attempt) is None

            assert await store.purge_tombstones(limit=10) == 1
            assert await store.register_eligible(limit=10) == 0

            fresh_now = await _database_now(runtime_engine)
            await _terminalize_reconciliation(runtime_engine, fresh_subject, fresh_now)
            assert await store.register_eligible(limit=10) == 1
            async with runtime_engine.connect() as connection:
                remaining_subject_ids = tuple(
                    await connection.scalars(
                        select(ci_workflow_attempt_collections.c.subject_id).order_by(
                            ci_workflow_attempt_collections.c.subject_id
                        )
                    )
                )
            assert remaining_subject_ids == (fresh_subject.subject_id,)
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_snapshot_insert_rolls_back_when_the_holder_cas_loses(
    monkeypatch: pytest.MonkeyPatch,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        now = await _database_now(engine)
        subject = _subject(308)
        store = _store(engine)
        try:
            await _terminalize_reconciliation(engine, subject, now)
            assert await store.register_eligible(limit=10) == 1
            claim = await store.claim_next(worker_id="1" * 64)
            assert claim is not None

            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                repository = transaction.ci_economics_collection

                async def lose_holder_cas(*_args: object) -> bool:
                    return False

                monkeypatch.setattr(repository, "_write_holder_transition", lose_holder_cas)
                assert await repository.record_snapshot(claim, _snapshot(subject, now)) == (
                    "claim_lost"
                )

            async with engine.connect() as connection:
                snapshot_count = await connection.scalar(
                    select(func.count()).select_from(ci_workflow_attempt_snapshots)
                )
                state = await connection.scalar(
                    select(ci_workflow_attempt_collections.c.status).where(
                        ci_workflow_attempt_collections.c.subject_id == subject.subject_id
                    )
                )
            assert snapshot_count == 0
            assert state == "leased"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_snapshot_reader_rejects_header_and_job_cardinality_divergence(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        now = await _database_now(runtime_engine)
        subject = _subject(309)
        store = _store(runtime_engine)
        try:
            await _terminalize_reconciliation(runtime_engine, subject, now)
            assert await store.register_eligible(limit=10) == 1
            claim = await store.claim_next(worker_id="1" * 64)
            assert claim is not None
            snapshot = _snapshot(subject, now)
            assert await store.record_snapshot(claim, snapshot) == "captured"

            async with admin_engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(
                        text(
                            "DROP TRIGGER tr_ci_workflow_attempt_snapshots_retention_guard "
                            "ON ci_coordinator.ci_workflow_attempt_snapshots"
                        )
                    )
                    await connection.execute(
                        update(ci_workflow_attempt_snapshots)
                        .where(ci_workflow_attempt_snapshots.c.subject_id == subject.subject_id)
                        .values(job_count=2)
                    )
                    repository = _PostgresCiEconomicsRepository(
                        connection,
                        lambda: None,
                        lambda: None,
                    )

                    with pytest.raises(
                        CiEconomicsStoreUnavailable,
                        match="job count diverges",
                    ):
                        await repository._load_snapshot(subject.subject_id)
                finally:
                    await transaction.rollback()
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_deferred_prefix_cannot_starve_a_later_due_subject(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        now = await _database_now(engine)
        first_subject = _subject(305)
        second_subject = _subject(306)
        store = _store(engine)
        try:
            await _terminalize_reconciliation(engine, first_subject, now)
            await _terminalize_reconciliation(engine, second_subject, now)
            assert await store.register_eligible(limit=10) == 2

            first_claim = await store.claim_next(worker_id="1" * 64)
            assert first_claim is not None
            assert await store.defer_claim(first_claim, "provider_unavailable") == "applied"
            second_claim = await store.claim_next(worker_id="1" * 64)

            assert second_claim is not None
            assert second_claim.source.source_id != first_claim.source.source_id
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_expiry_rejects_captured_outcome_without_its_snapshot(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        now = await _database_now(runtime_engine)
        subject = _subject(307)
        source = now - timedelta(days=91)
        evidence_retain_until = source + timedelta(days=90)
        state = CollectionState(
            subject_id=subject.subject_id,
            policy_hash="9" * 64,
            source_created_at=source,
            deadline_at=source + timedelta(days=7),
            evidence_retain_until=evidence_retain_until,
            tombstone_retain_until=evidence_retain_until + timedelta(days=1),
            status="captured",
            revision=2,
            attempt_count=1,
            max_attempts=5,
            backoff_seconds=1,
            max_backoff_seconds=300,
            next_attempt_at=None,
            claim_generation=1,
            lease_owner_id=None,
            lease_token=None,
            lease_acquired_at=None,
            lease_expires_at=None,
            last_failure_reason=None,
            final_outcome="captured",
            terminal_reason=None,
            completed_at=source + timedelta(days=1),
            expired_at=None,
        )
        try:
            await _terminalize_reconciliation(runtime_engine, subject, now)
            async with admin_engine.begin() as connection:
                await connection.execute(
                    insert(ci_workflow_attempt_collections).values(
                        encode_collection_record(
                            state, ReconciliationCollectionSource(subject, _CONTRACT)
                        )
                    )
                )

            with pytest.raises(CiEconomicsStoreUnavailable, match="expiry"):
                await _store(runtime_engine).expire_evidence(limit=10)

            async with admin_engine.connect() as connection:
                persisted_status = await connection.scalar(
                    select(ci_workflow_attempt_collections.c.status).where(
                        ci_workflow_attempt_collections.c.subject_id == subject.subject_id
                    )
                )
            assert persisted_status == "captured"
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def _terminal_unavailable_collection_state(
    subject: ReconciliationSubject,
    now: datetime,
    *,
    source_age_days: int,
    expired: bool,
) -> CollectionState:
    source = now - timedelta(days=source_age_days)
    evidence_retain_until = source + timedelta(days=90)
    return CollectionState(
        subject_id=subject.subject_id,
        policy_hash="8" * 64,
        source_created_at=source,
        deadline_at=source + timedelta(days=7),
        evidence_retain_until=evidence_retain_until,
        tombstone_retain_until=evidence_retain_until + timedelta(days=1),
        status="expired" if expired else "terminal_unavailable",
        revision=3,
        attempt_count=1,
        max_attempts=5,
        backoff_seconds=1,
        max_backoff_seconds=300,
        next_attempt_at=None,
        claim_generation=1,
        lease_owner_id=None,
        lease_token=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        last_failure_reason="provider_unavailable",
        final_outcome="terminal_unavailable",
        terminal_reason="deadline_exceeded",
        completed_at=source + timedelta(days=7),
        expired_at=evidence_retain_until if expired else None,
    )


async def _project_captured_attempt_to_later_database_epoch(
    admin_engine: AsyncEngine,
    subject_id: str,
    *,
    age_days: int,
) -> None:
    parameters = {"age_days": age_days, "subject_id": subject_id}
    async with admin_engine.begin() as connection:
        # Shift the trigger-protected retention epoch only inside this privileged
        # test transaction; SET LOCAL restores normal trigger enforcement at exit.
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        subject_update = await connection.execute(
            text(
                "UPDATE ci_coordinator.reconciliation_subjects SET "
                "created_at = created_at - make_interval(days => :age_days), "
                "deadline_at = deadline_at - make_interval(days => :age_days), "
                "next_attempt_at = next_attempt_at - make_interval(days => :age_days), "
                "lease_acquired_at = lease_acquired_at - make_interval(days => :age_days), "
                "lease_expires_at = lease_expires_at - make_interval(days => :age_days) "
                "WHERE subject_id = :subject_id"
            ),
            parameters,
        )
        collection_update = await connection.execute(
            text(
                "UPDATE ci_coordinator.ci_workflow_attempt_collections SET "
                "source_created_at = source_created_at - make_interval(days => :age_days), "
                "deadline_at = deadline_at - make_interval(days => :age_days), "
                "evidence_retain_until = evidence_retain_until "
                "- make_interval(days => :age_days), "
                "tombstone_retain_until = tombstone_retain_until "
                "- make_interval(days => :age_days), "
                "next_attempt_at = next_attempt_at - make_interval(days => :age_days), "
                "lease_acquired_at = lease_acquired_at - make_interval(days => :age_days), "
                "lease_expires_at = lease_expires_at - make_interval(days => :age_days), "
                "completed_at = completed_at - make_interval(days => :age_days), "
                "expired_at = expired_at - make_interval(days => :age_days), "
                "created_at = created_at - make_interval(days => :age_days), "
                "updated_at = updated_at - make_interval(days => :age_days) "
                "WHERE subject_id = :subject_id"
            ),
            parameters,
        )
        snapshot_update = await connection.execute(
            text(
                "UPDATE ci_coordinator.ci_workflow_attempt_snapshots SET "
                "recorded_at = recorded_at - make_interval(days => :age_days), "
                "retain_until = retain_until - make_interval(days => :age_days) "
                "WHERE subject_id = :subject_id"
            ),
            parameters,
        )
        assert (
            subject_update.rowcount == collection_update.rowcount == snapshot_update.rowcount == 1
        )


async def _attest_projected_retention_epoch(
    admin_engine: AsyncEngine,
    subject_id: str,
) -> None:
    async with admin_engine.connect() as connection:
        replication_role = await connection.scalar(text("SHOW session_replication_role"))
        row = (
            (
                await connection.execute(
                    select(
                        ci_workflow_attempt_collections,
                        reconciliation_subjects.c.created_at.label("reconciliation_created_at"),
                        ci_workflow_attempt_snapshots.c.retain_until.label("snapshot_retain_until"),
                    )
                    .join(
                        reconciliation_subjects,
                        reconciliation_subjects.c.subject_id
                        == ci_workflow_attempt_collections.c.subject_id,
                    )
                    .join(
                        ci_workflow_attempt_snapshots,
                        ci_workflow_attempt_snapshots.c.subject_id
                        == ci_workflow_attempt_collections.c.subject_id,
                    )
                    .where(ci_workflow_attempt_collections.c.subject_id == subject_id)
                )
            )
            .mappings()
            .one()
        )
        job_count = await connection.scalar(
            select(func.count())
            .select_from(ci_workflow_attempt_snapshot_jobs)
            .where(ci_workflow_attempt_snapshot_jobs.c.subject_id == subject_id)
        )
    state = decode_collection_state(dict(row))
    assert replication_role == "origin"
    assert state.status == "captured"
    assert state.source_created_at == row["reconciliation_created_at"]
    assert state.evidence_retain_until == row["snapshot_retain_until"]
    assert job_count is not None and job_count > 0
