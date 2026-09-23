from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Literal

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration.persistence._ci_economics_support import (
    database_now,
    snapshot,
    store,
    subject,
    terminalize_reconciliation,
)

from ci_coordinator.ci_economics import (
    CiEconomicsEvidenceConflict,
    CiEconomicsStoreUnavailable,
    CollectionClaim,
    ProviderAttemptSnapshot,
    initial_collection_state,
    load_bundled_ci_economics_profile,
)
from ci_coordinator.ci_economics.read_models import IndependentAttemptSnapshot
from ci_coordinator.ci_economics.sources import (
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_collection_codec import decode_collection_state
from ci_coordinator.persistence.ci_economics_collection_repository import (
    _PostgresCiEconomicsCollectionRepository,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import (
    CommitOutcomeUnknown,
    PersistenceInvariantViolation,
    StoreUnavailable,
)
from ci_coordinator.persistence.schema import (
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshot_jobs,
    ci_workflow_attempt_snapshots,
    reconciliation_subjects,
)

pytestmark = pytest.mark.persistence

type HolderOperation = Literal["capture", "defer", "reject"]


def test_independent_snapshot_replays_without_reconciliation_or_v1_projection(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            target = subject(330)
            provider = snapshot(target, now)
            source = ProviderRunCollectionSource(provider.attempt, now, "2026-03-10", "a" * 64)
            evidence = replace(provider, subject_id=source.source_id)
            state = initial_collection_state(
                source.source_id, now, now, load_bundled_ci_economics_profile().collection_policy
            )
            collection = store(engine)
            assert await collection.register_provider_source(source) == "registered"
            assert await collection.register_provider_source(source) == "replayed"
            assert (
                await collection.register_provider_source(
                    replace(source, source_evidence_digest="b" * 64)
                )
                == "source_conflict"
            )
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(reconciliation_subjects)
                    )
                    == 0
                )

            claim = await collection.claim_next(worker_id="1" * 64)
            assert claim is not None and claim.source == source
            assert await collection.record_snapshot(claim, evidence) == "captured"
            before = await _retained_rows(engine)
            assert before[0][0]["source_kind"] == "provider_run"
            assert before[0][0]["contract_hash"] is None
            assert before[0][0]["planned_route"] == "unknown"
            assert await collection.load_attempt(evidence.attempt) is None
            measured = await collection.load_measurements(evidence.attempt)
            assert measured is not None
            assert isinstance(measured.snapshot, IndependentAttemptSnapshot)
            assert measured.snapshot.source == source
            assert measured.snapshot.evidence == evidence
            assert measured.snapshot.retain_until == state.evidence_retain_until
            assert measured.measurements.runner_occupancy.known_value_ms == 3_000
            assert measured.measurements.queue.quality == "unknown"
            assert measured.measurements.queue.known_value_ms is None
            assert measured.measurements.attempt_wall.quality == "unknown"
            assert measured.measurements.attempt_wall.known_value_ms is None
            assert (
                measured.measurements.attempt_wall.reason_code == "attempt_wall_evidence_incomplete"
            )
            for wrong in (
                replace(
                    evidence.attempt, scope=replace(evidence.attempt.scope, installation_id=102)
                ),
                replace(evidence.attempt, scope=replace(evidence.attempt.scope, repository_id=203)),
                replace(evidence.attempt, workflow_run_id=331),
                replace(evidence.attempt, run_attempt=2),
                replace(evidence.attempt, head_sha="b" * 40),
            ):
                assert await collection.load_measurements(wrong) is None
            page = await collection.list_attempts(
                evidence.attempt.scope, after_cursor=None, limit=1
            )
            assert page.items == () and page.next_cursor is None

            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert (
                    await transaction.ci_economics.record_snapshot(
                        evidence, source, retain_until=state.evidence_retain_until
                    )
                    == "replayed"
                )
                await transaction.commit()
            changed = replace(
                snapshot(target, now + timedelta(seconds=1)), subject_id=source.source_id
            )
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                with pytest.raises(CiEconomicsEvidenceConflict):
                    await transaction.ci_economics.record_snapshot(
                        changed, source, retain_until=state.evidence_retain_until
                    )
            assert await _retained_rows(engine) == before
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["capture", "defer", "reject"])
def test_every_holder_transition_rejects_substituted_source_contract(
    runtime_postgres_database_url: str,
    operation: HolderOperation,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            target = subject(329)
            evidence = snapshot(target, now)
            collection = store(engine)
            await terminalize_reconciliation(engine, target, now)
            assert await collection.register_eligible(limit=1) == 1
            claim = await collection.claim_next(worker_id="1" * 64)
            assert claim is not None
            assert isinstance(claim.source, ReconciliationCollectionSource)
            signals = claim.source.contract.provider_signals
            assert len(signals) == 1
            changed_contract = replace(
                claim.source.contract,
                provider_signals=(),
            )
            assert changed_contract.contract_hash != claim.source.contract.contract_hash
            substituted = replace(claim, source=replace(claim.source, contract=changed_contract))
            assert substituted.source.source_id == claim.source.source_id
            async with engine.connect() as connection:
                before = tuple(
                    (await connection.execute(select(ci_workflow_attempt_collections))).all()
                )

            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                with pytest.raises(PersistenceInvariantViolation):
                    await _holder_transition(
                        transaction.ci_economics_collection, substituted, evidence, operation
                    )

            async with engine.connect() as connection:
                after = tuple(
                    (await connection.execute(select(ci_workflow_attempt_collections))).all()
                )
            assert after == before
            assert await _retained_rows(engine) == ((), ())
            assert await collection.record_snapshot(claim, evidence) == "captured"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_first", [False, True])
def test_source_coexistence_preserves_first_snapshot_and_each_original_lifetime(
    runtime_postgres_database_url: str, provider_first: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            target = subject(335)
            evidence = snapshot(target, now)
            independent = ProviderRunCollectionSource(
                evidence.attempt, now - timedelta(hours=1), "2026-03-10", "a" * 64
            )
            collection = store(engine)
            first_rows = None
            for is_provider in (provider_first, not provider_first):
                if is_provider:
                    assert await collection.register_provider_source(independent) == "registered"
                    source_id = independent.source_id
                else:
                    await terminalize_reconciliation(engine, target, now)
                    assert await collection.register_eligible(limit=1) == 1
                    source_id = target.subject_id
                claim = await collection.claim_next(worker_id="1" * 64)
                assert claim is not None and claim.source.source_id == source_id
                selected_evidence = replace(evidence, subject_id=source_id)
                if first_rows is None:
                    assert await collection.record_snapshot(claim, selected_evidence) == "captured"
                    first_rows = await _retained_rows(engine)
                    assert len(first_rows[0]) == len(first_rows[1]) == 1
                else:
                    async with engine.connect() as connection:
                        before = tuple(
                            (
                                await connection.execute(
                                    select(ci_workflow_attempt_collections).order_by(
                                        ci_workflow_attempt_collections.c.subject_id
                                    )
                                )
                            )
                            .mappings()
                            .all()
                        )
                    assert len(before) == 2
                    assert len({row["evidence_retain_until"] for row in before}) == 2
                    with pytest.raises(CiEconomicsEvidenceConflict):
                        await collection.record_snapshot(claim, selected_evidence)
                    assert await _retained_rows(engine) == first_rows
                    async with engine.connect() as connection:
                        after = tuple(
                            (
                                await connection.execute(
                                    select(ci_workflow_attempt_collections).order_by(
                                        ci_workflow_attempt_collections.c.subject_id
                                    )
                                )
                            )
                            .mappings()
                            .all()
                        )
                    assert after == before
            selected = await collection.load_measurements(evidence.attempt)
            assert selected is not None and first_rows is not None
            assert selected.snapshot.retain_until == first_rows[0][0]["retain_until"]
            assert isinstance(selected.snapshot, IndependentAttemptSnapshot) is provider_first
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_snapshot_replay_preserves_original_evidence_and_rejects_conflicting_facts(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            target = subject(320)
            evidence = snapshot(target, now)
            collection = store(engine)
            await terminalize_reconciliation(engine, target, now)
            assert await collection.register_eligible(limit=1) == 1
            claim = await collection.claim_next(worker_id="1" * 64)
            assert claim is not None
            assert isinstance(claim.source, ReconciliationCollectionSource)

            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                state, _ = await transaction.ci_economics_collection._lock_claim_state(claim)
                retain_until = state.evidence_retain_until
                assert (
                    await transaction.ci_economics.record_snapshot(
                        evidence, claim.source, retain_until=retain_until
                    )
                    == "captured"
                )
                assert (
                    await transaction.ci_economics_collection.record_snapshot(claim, evidence)
                    == "captured"
                )
                await transaction.commit()

            before = await _retained_rows(engine)
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert (
                    await transaction.ci_economics.record_snapshot(
                        evidence, claim.source, retain_until=retain_until
                    )
                    == "replayed"
                )
                await transaction.commit()
            assert await _retained_rows(engine) == before

            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                with pytest.raises(CiEconomicsEvidenceConflict):
                    await transaction.ci_economics.record_snapshot(
                        snapshot(target, now + timedelta(seconds=1)),
                        claim.source,
                        retain_until=retain_until,
                    )
            assert await _retained_rows(engine) == before
            assert await collection.record_snapshot(claim, evidence) == "claim_lost"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_duplicate_completions_capture_exactly_once(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            target = subject(321)
            evidence = snapshot(target, now)
            collection = store(engine)
            await terminalize_reconciliation(engine, target, now)
            assert await collection.register_eligible(limit=1) == 1
            claim = await collection.claim_next(worker_id="1" * 64)
            assert claim is not None

            async with asyncio.timeout(20), asyncio.TaskGroup() as group:
                completions = [
                    group.create_task(store(engine).record_snapshot(claim, evidence))
                    for _ in range(2)
                ]

            assert sorted(task.result() for task in completions) == ["captured", "claim_lost"]
            headers, jobs = await _retained_rows(engine)
            assert len(headers) == len(jobs) == 1
            assert headers[0]["subject_id"] == jobs[0]["subject_id"] == target.subject_id
            retained = await collection.load_attempt(evidence.attempt)
            assert retained is not None
            assert retained.snapshot.jobs == evidence.jobs
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("commit_succeeds", (False, True))
def test_public_snapshot_completion_rejects_commit_failure_without_assuming_rollback(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    commit_succeeds: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            target = subject(323)
            evidence = snapshot(target, now)
            collection = store(engine)
            await terminalize_reconciliation(engine, target, now)
            assert await collection.register_eligible(limit=1) == 1
            claim = await collection.claim_next(worker_id="1" * 64)
            assert claim is not None
            commit = PostgresCiEconomicsUnitOfWork.commit

            async def fail_commit(transaction: PostgresCiEconomicsUnitOfWork) -> None:
                pending = await transaction.ci_economics.load_attempt(evidence.attempt)
                assert pending is not None
                if commit_succeeds:
                    await commit(transaction)
                    raise CommitOutcomeUnknown("injected acknowledgement failure")
                raise StoreUnavailable("injected pre-commit failure")

            with monkeypatch.context() as patch:
                # noinspection PyUnresolvedReferences
                patch.setattr(PostgresCiEconomicsUnitOfWork, "commit", fail_commit)
                with pytest.raises(CiEconomicsStoreUnavailable, match="snapshot"):
                    await collection.record_snapshot(claim, evidence)

            headers, jobs = await _retained_rows(engine)
            assert len(headers) == len(jobs) == int(commit_succeeds)
            expected = "claim_lost" if commit_succeeds else "captured"
            assert await collection.record_snapshot(claim, evidence) == expected
            retained = await collection.load_attempt(evidence.attempt)
            assert retained is not None
            assert retained.snapshot.jobs == evidence.jobs
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("first_owner", ("expired_holder", "reclaimer"))
@pytest.mark.parametrize("operation", ("capture", "defer", "reject"))
def test_expired_claim_is_fenced_in_both_database_lock_orders(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    first_owner: str,
    operation: HolderOperation,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(engine)
            target = subject(322)
            evidence = snapshot(target, now)
            collection = store(engine)
            await terminalize_reconciliation(engine, target, now)
            assert await collection.register_eligible(limit=1) == 1
            initial = await collection.claim_next(worker_id="1" * 64)
            assert initial is not None
            expired = await _translate_claim_to_expired_epoch(admin_engine, initial)

            async with asyncio.timeout(20):
                if first_owner == "expired_holder":
                    async with PostgresCiEconomicsUnitOfWork(engine) as holder:
                        state, _ = await holder.ci_economics_collection._lock_claim_state(expired)
                        assert state.revision == expired.revision
                        assert await store(engine).claim_next(worker_id="2" * 64) is None
                        assert (
                            await _holder_transition(
                                holder.ci_economics_collection, expired, evidence, operation
                            )
                            == "claim_lost"
                        )
                    successor = await collection.claim_next(worker_id="2" * 64)
                else:
                    holder_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

                    async def stale_holder() -> str:
                        async with PostgresCiEconomicsUnitOfWork(engine) as holder:
                            pid = await holder.ci_economics_collection._connection.scalar(
                                select(func.pg_backend_pid())
                            )
                            assert type(pid) is int
                            holder_pid.set_result(pid)
                            return await _holder_transition(
                                holder.ci_economics_collection, expired, evidence, operation
                            )

                    async with (
                        asyncio.TaskGroup() as group,
                        PostgresCiEconomicsUnitOfWork(engine) as reclaimer,
                    ):
                        successor = await reclaimer.ci_economics_collection.claim_next(
                            worker_id="2" * 64
                        )
                        assert successor is not None
                        owner_pid = await reclaimer.ci_economics_collection._connection.scalar(
                            select(func.pg_backend_pid())
                        )
                        assert type(owner_pid) is int
                        old_transition = group.create_task(stale_holder())
                        await _wait_for_database_blocking(admin_engine, await holder_pid, owner_pid)
                        await reclaimer.commit()
                    assert old_transition.result() == "claim_lost"

            assert successor is not None
            assert successor.revision == expired.revision + 1
            assert successor.generation == expired.generation + 1
            assert successor.attempt_count == expired.attempt_count + 1
            assert successor.worker_id == "2" * 64
            assert successor.token != expired.token
            assert await _retained_rows(engine) == ((), ())
            assert await collection.record_snapshot(successor, evidence) == "captured"
            assert await collection.record_snapshot(expired, evidence) == "claim_lost"
            headers, jobs = await _retained_rows(engine)
            assert len(headers) == len(jobs) == 1
        finally:
            await admin_engine.dispose()
            await engine.dispose()

    asyncio.run(scenario())


async def _holder_transition(
    repository: _PostgresCiEconomicsCollectionRepository,
    claim: CollectionClaim,
    evidence: ProviderAttemptSnapshot,
    operation: HolderOperation,
) -> str:
    if operation == "capture":
        return await repository.record_snapshot(claim, evidence)
    if operation == "defer":
        return await repository.defer_claim(claim, "provider_unavailable")
    return await repository.reject_claim(claim)


async def _wait_for_database_blocking(
    engine: AsyncEngine, waiting_pid: int, owning_pid: int
) -> None:
    async with engine.connect() as connection:
        while True:
            blockers = await connection.scalar(select(func.pg_blocking_pids(waiting_pid)))
            assert blockers is not None
            if owning_pid in blockers:
                return
            await asyncio.sleep(0.01)


async def _translate_claim_to_expired_epoch(
    engine: AsyncEngine, claim: CollectionClaim
) -> CollectionClaim:
    shift = claim.lease_expires_at - claim.claimed_at + timedelta(seconds=30)
    groups = (
        (
            reconciliation_subjects,
            (
                "created_at",
                "deadline_at",
                "next_attempt_at",
                "lease_acquired_at",
                "lease_expires_at",
            ),
        ),
        (
            ci_workflow_attempt_collections,
            (
                "source_created_at",
                "deadline_at",
                "evidence_retain_until",
                "tombstone_retain_until",
                "next_attempt_at",
                "lease_acquired_at",
                "lease_expires_at",
                "completed_at",
                "expired_at",
                "created_at",
                "updated_at",
            ),
        ),
    )
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        for relation, columns in groups:
            result = await connection.execute(
                update(relation)
                .where(relation.c.subject_id == claim.source.source_id)
                .values({name: relation.c[name] - shift for name in columns})
            )
            assert result.rowcount == 1

    expired = replace(
        claim, claimed_at=claim.claimed_at - shift, lease_expires_at=claim.lease_expires_at - shift
    )
    async with engine.connect() as connection:
        assert await connection.scalar(text("SHOW session_replication_role")) == "origin"
        row = (
            (
                await connection.execute(
                    select(ci_workflow_attempt_collections).where(
                        ci_workflow_attempt_collections.c.subject_id == claim.source.source_id
                    )
                )
            )
            .mappings()
            .one()
        )
        source_created_at = await connection.scalar(
            select(reconciliation_subjects.c.created_at).where(
                reconciliation_subjects.c.subject_id == claim.source.source_id
            )
        )
    state = decode_collection_state(dict(row))
    assert state.status == "leased"
    assert state.source_created_at == source_created_at
    assert state.policy_hash == expired.policy_hash
    assert (state.revision, state.claim_generation, state.attempt_count) == (
        expired.revision,
        expired.generation,
        expired.attempt_count,
    )
    assert (state.lease_owner_id, state.lease_token) == (expired.worker_id, expired.token)
    assert (state.lease_acquired_at, state.lease_expires_at) == (
        expired.claimed_at,
        expired.lease_expires_at,
    )
    assert expired.lease_expires_at < await database_now(engine) < state.deadline_at
    return expired


async def _retained_rows(
    engine: AsyncEngine,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    async with engine.connect() as connection:
        headers = tuple(
            dict(row)
            for row in (await connection.execute(select(ci_workflow_attempt_snapshots)))
            .mappings()
            .all()
        )
        jobs = tuple(
            dict(row)
            for row in (await connection.execute(select(ci_workflow_attempt_snapshot_jobs)))
            .mappings()
            .all()
        )
    return headers, jobs
