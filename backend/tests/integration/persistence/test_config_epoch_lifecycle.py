from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from ci_coordinator.audit_replay import AuditEventInput, prepare_audit_event
from ci_coordinator.config_control import (
    PolicySourceFormat,
    RepositoryScope,
    ValidatedEpochDraft,
)
from ci_coordinator.config_epochs import (
    ConfigEpochActivationApplied,
    ConfigEpochActivationCommand,
    ConfigEpochActivationDuplicate,
    ConfigEpochActivationOperationConflict,
    ConfigEpochActivationRevisionConflict,
    ConfigEpochRegistrationCreated,
    ConfigEpochRegistrationDuplicate,
    ConfigEpochReplayCommand,
    prepare_config_epoch_activation,
)
from ci_coordinator.persistence import (
    PersistenceInvariantViolation,
    PostgresConfigEpochUnitOfWork,
    PostgresUnitOfWork,
)
from ci_coordinator.persistence.config_epoch_schema_attestation import (
    config_epoch_lifecycle_schema_matches_contract,
)
from ci_coordinator.persistence.connection import create_postgres_engine

from ._audit_replay_support import load_test_audit_records
from ._config_epoch_support import config_epoch_draft

pytestmark = pytest.mark.persistence

_PROPOSAL_MANIFEST_ID = "proposal:" + "f" * 32
_AUTHORITY_EVIDENCE_HASH = "a" * 64
_AUTHORITY_OBSERVED_AT = "2026-07-14T11:59:59.000Z"


def test_register_activate_and_exact_operation_replay(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = _draft("json")
        command = _command(draft.scope, draft.epoch_id, None, "activate-a")
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                assert isinstance(
                    await unit_of_work.config_epochs.register(draft),
                    ConfigEpochRegistrationCreated,
                )
                assert await unit_of_work.config_epochs.load_active(draft.scope) is None
                result = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(command)
                )
                assert isinstance(result, ConfigEpochActivationApplied)
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                snapshot = await unit_of_work.config_epochs.load_active(draft.scope)
                registration_replay = await unit_of_work.config_epochs.register(draft)
                replay = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(command)
                )
                await unit_of_work.rollback()
            assert snapshot is not None
            assert snapshot.active.epoch_id == draft.epoch_id
            assert snapshot.active.revision == 1
            assert snapshot.draft == draft
            assert isinstance(registration_replay, ConfigEpochRegistrationDuplicate)
            assert isinstance(replay, ConfigEpochActivationDuplicate)
            assert replay.record.active == snapshot.active
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_rollback_cas_persists_bounded_reason_and_compared_epoch_evidence(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        active = _draft("json")
        target = _draft("yaml-1.2")
        reason = "restore conservative validation"
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                await unit_of_work.config_epochs.register(active)
                await unit_of_work.config_epochs.register(target)
                initial = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(
                        _command(active.scope, active.epoch_id, None, "initial")
                    )
                )
                assert isinstance(initial, ConfigEpochActivationApplied)
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                loaded_target = await unit_of_work.config_epochs.load_epoch(
                    active.scope,
                    target.epoch_id,
                )
                assert loaded_target == target
                assert (
                    await unit_of_work.config_epochs.load_epoch(
                        RepositoryScope(active.scope.installation_id, 999),
                        target.epoch_id,
                    )
                    is None
                )
                stale_comparison = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(
                        ConfigEpochActivationCommand(
                            scope=active.scope,
                            target_epoch_id=target.epoch_id,
                            expected_revision=1,
                            operation_id="stale-comparison",
                            actor="operator:123",
                            occurred_at="2026-07-14T12:00:30.000Z",
                            mutation_kind="rollback",
                            expected_active_epoch_id="f" * 64,
                            coverage_relation="equal",
                            reason=reason,
                        )
                    )
                )
                assert isinstance(stale_comparison, ConfigEpochActivationRevisionConflict)
                rollback = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(
                        ConfigEpochActivationCommand(
                            scope=active.scope,
                            target_epoch_id=target.epoch_id,
                            expected_revision=1,
                            operation_id="rollback-to-target",
                            actor="operator:123",
                            occurred_at="2026-07-14T12:01:00.000Z",
                            mutation_kind="rollback",
                            expected_active_epoch_id=active.epoch_id,
                            coverage_relation="equal",
                            reason=reason,
                        )
                    )
                )
                assert isinstance(rollback, ConfigEpochActivationApplied)
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                replay = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(
                        ConfigEpochActivationCommand(
                            scope=active.scope,
                            target_epoch_id=target.epoch_id,
                            expected_revision=1,
                            operation_id="rollback-to-target",
                            actor="operator:123",
                            occurred_at="2026-07-14T13:01:00.000Z",
                            mutation_kind="rollback",
                            expected_active_epoch_id=target.epoch_id,
                            coverage_relation="equal",
                            reason=reason,
                        )
                    )
                )
                snapshot = await unit_of_work.config_epochs.load_active(active.scope)
                events = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.rollback()
            assert isinstance(replay, ConfigEpochActivationDuplicate)
            assert snapshot is not None
            assert snapshot.active.epoch_id == target.epoch_id
            assert snapshot.active.revision == 2
            event = next(item for item in events if item.event_type == "config-epoch-rollback/v1")
            assert event.payload == {
                "schemaVersion": "config-epoch-rollback-audit/v1",
                "operationId": "rollback-to-target",
                "expectedRevision": 1,
                "activeEpochId": active.epoch_id,
                "targetEpochId": target.epoch_id,
                "coverageRelation": "equal",
                "reason": reason,
            }

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                intervening = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(
                        _command(active.scope, active.epoch_id, 2, "intervening-activation")
                    )
                )
                assert isinstance(intervening, ConfigEpochActivationApplied)
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                retained = await unit_of_work.config_epochs.resolve_operation(
                    ConfigEpochReplayCommand(
                        scope=active.scope,
                        target_epoch_id=target.epoch_id,
                        expected_revision=1,
                        operation_id="rollback-to-target",
                        actor="operator:123",
                        mutation_kind="rollback",
                        reason=reason,
                    )
                )
                divergent = await unit_of_work.config_epochs.resolve_operation(
                    ConfigEpochReplayCommand(
                        scope=active.scope,
                        target_epoch_id=target.epoch_id,
                        expected_revision=1,
                        operation_id="rollback-to-target",
                        actor="operator:123",
                        mutation_kind="rollback",
                        reason="different rollback reason",
                    )
                )
                changed_snapshot = await unit_of_work.config_epochs.load_active(active.scope)
                await unit_of_work.rollback()
            assert retained == replay
            assert isinstance(divergent, ConfigEpochActivationOperationConflict)
            assert changed_snapshot is not None
            assert changed_snapshot.active.epoch_id == active.epoch_id
            assert changed_snapshot.active.revision == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_pair_owned_activation_event_cannot_use_the_generic_ledger_port(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = _draft("json")
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                prepared = prepare_config_epoch_activation(
                    _command(draft.scope, draft.epoch_id, None, "generic-port")
                )
                with pytest.raises(PersistenceInvariantViolation, match="pair-owned"):
                    await unit_of_work.audit_events.append(prepared._take_audit_event())
                with pytest.raises(RuntimeError, match="not active"):
                    await unit_of_work.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_expected_revision_has_at_most_one_success(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        first = _draft("json")
        second = _draft("yaml-1.2")
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                assert isinstance(
                    await unit_of_work.config_epochs.register(first),
                    ConfigEpochRegistrationCreated,
                )
                assert isinstance(
                    await unit_of_work.config_epochs.register(second),
                    ConfigEpochRegistrationCreated,
                )
                initial = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(
                        _command(first.scope, first.epoch_id, None, "initial")
                    )
                )
                assert isinstance(initial, ConfigEpochActivationApplied)
                await unit_of_work.commit()

            async def activate(operation_id: str) -> object:
                async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                    result = await unit_of_work.config_epochs.activate(
                        prepare_config_epoch_activation(
                            _command(second.scope, second.epoch_id, 1, operation_id)
                        )
                    )
                    if isinstance(result, ConfigEpochActivationApplied):
                        await unit_of_work.commit()
                    else:
                        await unit_of_work.rollback()
                    return result

            left, right = await asyncio.gather(activate("race-left"), activate("race-right"))
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                snapshot = await unit_of_work.config_epochs.load_active(first.scope)
                await unit_of_work.rollback()
            assert (
                sum(isinstance(value, ConfigEpochActivationApplied) for value in (left, right)) == 1
            )
            assert (
                sum(
                    isinstance(value, ConfigEpochActivationRevisionConflict)
                    for value in (left, right)
                )
                == 1
            )
            assert snapshot is not None
            assert snapshot.active.epoch_id == second.epoch_id
            assert snapshot.active.revision == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_catalog_attestation_rejects_a_same_name_weakened_constraint(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await config_epoch_lifecycle_schema_matches_contract(connection)
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.config_epochs DROP CONSTRAINT "
                            "ck_config_epochs_source_bytes_limit"
                        )
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.config_epochs ADD CONSTRAINT "
                            "ck_config_epochs_source_bytes_limit CHECK "
                            "(octet_length(source_bytes) <= 2097153)"
                        )
                    )
                    assert not await config_epoch_lifecycle_schema_matches_contract(connection)
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_aba_rejects_a_stale_revision_and_allows_a_new_transition(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        first = _draft("json")
        second = _draft("yaml-1.2")
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                await unit_of_work.config_epochs.register(first)
                await unit_of_work.config_epochs.register(second)
                assert isinstance(
                    await unit_of_work.config_epochs.activate(
                        prepare_config_epoch_activation(
                            _command(first.scope, first.epoch_id, None, "initial")
                        )
                    ),
                    ConfigEpochActivationApplied,
                )
                assert isinstance(
                    await unit_of_work.config_epochs.activate(
                        prepare_config_epoch_activation(
                            _command(second.scope, second.epoch_id, 1, "to-b")
                        )
                    ),
                    ConfigEpochActivationApplied,
                )
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                stale = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(
                        _command(first.scope, first.epoch_id, 1, "stale-return-to-a")
                    )
                )
                assert isinstance(stale, ConfigEpochActivationRevisionConflict)
                assert stale.active is not None and stale.active.revision == 2
                await unit_of_work.rollback()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                fresh = await unit_of_work.config_epochs.activate(
                    prepare_config_epoch_activation(
                        _command(first.scope, first.epoch_id, 2, "fresh-return-to-a")
                    )
                )
                assert isinstance(fresh, ConfigEpochActivationApplied)
                assert fresh.record.active.revision == 3
                await unit_of_work.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_audit_conflict_commits_neither_pointer_nor_activation(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = _draft("json")
        operation_id = "conflicting-operation"
        key = f"config-epoch-activation:1:2:{operation_id}"
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                await unit_of_work.audit_events.append(
                    prepare_audit_event(
                        AuditEventInput(
                            idempotency_key=key,
                            subject_type="policy-decision",
                            subject_id="unrelated",
                            event_type="unrelated",
                            created_at="2026-07-14T12:00:00.000Z",
                            actor="operator:123",
                            payload={"unrelated": True},
                        )
                    )
                )
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                await unit_of_work.config_epochs.register(draft)
                with pytest.raises(PersistenceInvariantViolation, match="idempotency"):
                    await unit_of_work.config_epochs.activate(
                        prepare_config_epoch_activation(
                            _command(draft.scope, draft.epoch_id, None, operation_id)
                        )
                    )
                with pytest.raises(RuntimeError, match="not active"):
                    await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                assert await unit_of_work.config_epochs.load_active(draft.scope) is None
                await unit_of_work.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_immutable_epoch_and_activation_rows_reject_direct_mutation(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = _draft("json")
        engine = create_postgres_engine(runtime_postgres_database_url)
        migration_engine = create_postgres_engine(postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                await unit_of_work.config_epochs.register(draft)
                assert isinstance(
                    await unit_of_work.config_epochs.activate(
                        prepare_config_epoch_activation(
                            _command(draft.scope, draft.epoch_id, None, "initial")
                        )
                    ),
                    ConfigEpochActivationApplied,
                )
                await unit_of_work.commit()
            async with migration_engine.begin() as connection:
                with pytest.raises(Exception, match="config epoch history is immutable"):
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "UPDATE ci_coordinator.config_epochs SET source_hash = :hash "
                                "WHERE epoch_id = :epoch_id"
                            ),
                            {"hash": "0" * 64, "epoch_id": draft.epoch_id},
                        )
                with pytest.raises(Exception, match="config epoch history is immutable"):
                    async with connection.begin_nested():
                        await connection.execute(
                            text("DELETE FROM ci_coordinator.config_epoch_activations")
                        )
        finally:
            await engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def _draft(source_format: PolicySourceFormat) -> ValidatedEpochDraft:
    return config_epoch_draft(source_format)


def _command(
    scope: RepositoryScope,
    target_epoch_id: str,
    expected_revision: int | None,
    operation_id: str,
) -> ConfigEpochActivationCommand:
    return ConfigEpochActivationCommand(
        scope=scope,
        target_epoch_id=target_epoch_id,
        expected_revision=expected_revision,
        operation_id=operation_id,
        actor="operator:123",
        occurred_at="2026-07-14T12:00:00.000Z",
        proposal_manifest_id=_PROPOSAL_MANIFEST_ID,
        authority_evidence_hash=_AUTHORITY_EVIDENCE_HASH,
        authority_observed_at=_AUTHORITY_OBSERVED_AT,
    )
