from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

import ci_coordinator.persistence.governance_baseline_repository as baseline_repository
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GovernanceBaselineCommand,
    GovernanceBaselineConflict,
    GovernanceBaselineCreated,
    GovernanceBaselineDraft,
    GovernanceBaselineDuplicate,
    GovernanceBaselineOperationConflict,
    GovernanceBaselinePointer,
    GovernanceBaselineRecord,
    GovernanceBaselineUnchanged,
    PreparedGovernanceBaseline,
    prepare_governance_baseline,
)
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceRepository,
    GovernanceState,
    encode_governance_state,
)
from ci_coordinator.kernel import canonical_json, sha256_hex
from ci_coordinator.persistence import PersistenceInvariantViolation
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable, StoreUnavailable
from ci_coordinator.persistence.governance_baseline_schema_attestation import (
    governance_baseline_schema_matches_contract,
)
from ci_coordinator.persistence.governance_baseline_unit_of_work import (
    PostgresGovernanceBaselineUnitOfWork,
)
from ci_coordinator.persistence.schema import (
    audit_events,
    governance_baseline_operations,
    governance_baselines,
)

from .conftest import RUNTIME_ROLE

pytestmark = pytest.mark.persistence

SCOPE = RepositoryScope(7, 11)
NOW = datetime(2026, 7, 26, 10, tzinfo=UTC)


def test_append_replay_unchanged_and_successor_form_one_exact_chain(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            first = _draft(operation_id="approve-1", state=_state("required_status_checks"))
            async with PostgresGovernanceBaselineUnitOfWork(runtime) as transaction:
                created = await transaction.governance_baselines.accept(_prepare(first, 1))
                assert isinstance(created, GovernanceBaselineCreated)
                await transaction.commit()

            unchanged = _draft(
                operation_id="approve-unchanged",
                state=first.state,
                expected_active=created.record.pointer,
            )
            successor = _draft(
                operation_id="approve-2",
                state=_state("pull_request"),
                expected_active=created.record.pointer,
            )
            async with PostgresGovernanceBaselineUnitOfWork(runtime) as transaction:
                replay = await transaction.governance_baselines.resolve_operation(first.command)
                operation_conflict = await transaction.governance_baselines.resolve_operation(
                    replace(first.command, reason="A changed decision")
                )
                unchanged_result = await transaction.governance_baselines.accept(
                    _prepare(unchanged, 2)
                )
                successor_result = await transaction.governance_baselines.accept(
                    _prepare(successor, 3)
                )
                await transaction.commit()
            assert isinstance(replay, GovernanceBaselineDuplicate)
            assert replay.record == created.record
            assert isinstance(operation_conflict, GovernanceBaselineOperationConflict)
            assert isinstance(unchanged_result, GovernanceBaselineUnchanged)
            assert unchanged_result.record == created.record
            assert isinstance(successor_result, GovernanceBaselineCreated)
            assert successor_result.record.version == 2
            assert successor_result.record.command.expected_active == created.record.pointer

            async with PostgresGovernanceBaselineUnitOfWork(runtime) as transaction:
                unchanged_replay = await transaction.governance_baselines.resolve_operation(
                    unchanged.command
                )
                unchanged_conflict = await transaction.governance_baselines.resolve_operation(
                    replace(unchanged.command, reason="Changed no-op decision")
                )
                active = await transaction.governance_baselines.load_active(SCOPE)
            assert isinstance(unchanged_replay, GovernanceBaselineUnchanged)
            assert unchanged_replay.record == created.record
            assert isinstance(unchanged_conflict, GovernanceBaselineOperationConflict)
            assert active == successor_result.record
            assert await _counts(admin) == (3, 2, 2)
        finally:
            await admin.dispose()
            await runtime.dispose()

    asyncio.run(scenario())


def test_concurrent_successors_cannot_both_commit(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        seed_engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        first_engine = create_postgres_engine(runtime_postgres_database_url)
        second_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            seed = _draft(operation_id="seed", state=_state("required_status_checks"))
            async with PostgresGovernanceBaselineUnitOfWork(seed_engine) as transaction:
                seeded = await transaction.governance_baselines.accept(_prepare(seed, 1))
                assert isinstance(seeded, GovernanceBaselineCreated)
                await transaction.commit()
            barrier = asyncio.Barrier(2)

            async def append(
                engine: AsyncEngine,
                operation_id: str,
                rule_type: str,
            ) -> GovernanceBaselineCreated | GovernanceBaselineConflict:
                draft = _draft(
                    operation_id=operation_id,
                    state=_state(rule_type),
                    expected_active=seeded.record.pointer,
                )
                async with PostgresGovernanceBaselineUnitOfWork(engine) as transaction:
                    await barrier.wait()
                    result = await transaction.governance_baselines.accept(_prepare(draft, 2))
                    assert isinstance(
                        result,
                        GovernanceBaselineCreated | GovernanceBaselineConflict,
                    )
                    await transaction.commit()
                    return result

            results = await asyncio.gather(
                append(first_engine, "successor-a", "pull_request"),
                append(second_engine, "successor-b", "deletion"),
            )
            assert sum(isinstance(result, GovernanceBaselineCreated) for result in results) == 1
            assert sum(isinstance(result, GovernanceBaselineConflict) for result in results) == 1
            assert await _counts(admin) == (2, 2, 2)
        finally:
            await second_engine.dispose()
            await first_engine.dispose()
            await admin.dispose()
            await seed_engine.dispose()

    asyncio.run(scenario())


def test_failed_baseline_insert_rolls_back_pair_owned_audit(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = baseline_repository._record_to_row

    def invalid_row(record: GovernanceBaselineRecord) -> dict[str, object]:
        row = original(record)
        row["version"] = 0
        return row

    monkeypatch.setattr(baseline_repository, "_record_to_row", invalid_row)

    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            with pytest.raises(StoreUnavailable, match="append failed"):
                async with PostgresGovernanceBaselineUnitOfWork(runtime) as transaction:
                    await transaction.governance_baselines.accept(
                        _prepare(
                            _draft(operation_id="invalid", state=_state("pull_request")),
                            1,
                        )
                    )
            assert await _counts(admin) == (0, 0, 0)
        finally:
            await admin.dispose()
            await runtime.dispose()

    asyncio.run(scenario())


def test_schema_trigger_corruption_and_runtime_acl_fail_closed(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            draft = _draft(operation_id="retained", state=_state("pull_request"))
            async with PostgresGovernanceBaselineUnitOfWork(runtime) as transaction:
                retained = await transaction.governance_baselines.accept(_prepare(draft, 1))
                assert isinstance(retained, GovernanceBaselineCreated)
                await transaction.commit()

            for statement in (
                "UPDATE ci_coordinator.governance_baselines SET reason = 'changed'",
                "DELETE FROM ci_coordinator.governance_baselines",
                (
                    "UPDATE ci_coordinator.governance_baseline_operations "
                    "SET result_kind = 'unchanged'"
                ),
                "DELETE FROM ci_coordinator.governance_baseline_operations",
            ):
                async with runtime.connect() as connection:
                    direct = await connection.begin()
                    try:
                        with pytest.raises(DBAPIError, match="permission denied"):
                            await connection.execute(text(statement))
                    finally:
                        await direct.rollback()

            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.governance_baseline_operations "
                        "DISABLE TRIGGER tr_governance_baseline_operations_immutable"
                    )
                )
                assert not await governance_baseline_schema_matches_contract(connection)
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.governance_baseline_operations "
                        "ENABLE TRIGGER tr_governance_baseline_operations_immutable"
                    )
                )
                assert await governance_baseline_schema_matches_contract(connection)
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.governance_baselines "
                        "DISABLE TRIGGER tr_governance_baselines_immutable"
                    )
                )
                assert not await governance_baseline_schema_matches_contract(connection)
                await connection.execute(
                    text("UPDATE ci_coordinator.governance_baselines SET state_digest = :digest"),
                    {"digest": "0" * 64},
                )
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.governance_baselines "
                        "ENABLE TRIGGER tr_governance_baselines_immutable"
                    )
                )
            with pytest.raises(PersistenceInvariantViolation, match="stored governance baseline"):
                async with PostgresGovernanceBaselineUnitOfWork(runtime) as transaction:
                    await transaction.governance_baselines.load_active(SCOPE)
        finally:
            await admin.dispose()
            await runtime.dispose()

    asyncio.run(scenario())


def test_declared_capability_without_exact_runtime_grants_is_unavailable(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        "REVOKE SELECT, INSERT ON ci_coordinator.governance_baseline_operations "
                        f"FROM {RUNTIME_ROLE}"
                    )
                )
            with pytest.raises(DatabaseCapabilityUnavailable, match="runtime database principal"):
                async with PostgresGovernanceBaselineUnitOfWork(runtime):
                    pass
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        "GRANT SELECT, INSERT ON ci_coordinator.governance_baseline_operations "
                        f"TO {RUNTIME_ROLE}"
                    )
                )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def _draft(
    *,
    operation_id: str,
    state: GovernanceState,
    expected_active: GovernanceBaselinePointer | None = None,
) -> GovernanceBaselineDraft:
    return GovernanceBaselineDraft(
        command=GovernanceBaselineCommand(
            scope=SCOPE,
            operation_id=operation_id,
            expected_state_digest=sha256_hex(encode_governance_state(state)),
            expected_active=expected_active,
            actor="owner:42",
            reason="Adopt repository governance",
        ),
        state=state,
        observed_at=NOW,
    )


def _prepare(
    draft: GovernanceBaselineDraft,
    seconds: int,
) -> PreparedGovernanceBaseline:
    return prepare_governance_baseline(
        draft,
        approved_at=NOW + timedelta(seconds=seconds),
    )


def _state(rule_type: str) -> GovernanceState:
    value = {
        "parameters": {},
        "ruleset_id": 41,
        "ruleset_source": "example/repository",
        "ruleset_source_type": "Repository",
        "type": rule_type,
    }
    rule = EffectiveGovernanceRule(
        rule_type=rule_type,
        ruleset_source_type="Repository",
        ruleset_source="example/repository",
        ruleset_id=41,
        canonical_json=canonical_json(value),
    )
    return GovernanceState(
        GovernanceRepository(
            SCOPE,
            owner_id=101,
            owner="example",
            name="repository",
            full_name="example/repository",
            default_branch="master",
        ),
        "2026-03-10",
        (rule,),
    )


async def _counts(engine: AsyncEngine) -> tuple[int, int, int]:
    async with engine.connect() as connection:
        operations, baselines, events = [
            await connection.scalar(select(func.count()).select_from(table))
            for table in (
                governance_baseline_operations,
                governance_baselines,
                audit_events,
            )
        ]
    if operations is None or baselines is None or events is None:
        raise AssertionError("count aggregate unexpectedly returned null")
    return operations, baselines, events
