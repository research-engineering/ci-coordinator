from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditEventInput,
    prepare_audit_event,
)
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import (
    ConfigEpochRegistrationCommand,
    ConfigEpochRegistrationCommitted,
    ConfigEpochRegistrationOperationConflict,
    ConfigEpochRegistrationReplay,
    PreparedConfigEpochRegistration,
    prepare_config_epoch_registration,
)
from ci_coordinator.persistence import (
    PersistenceInvariantViolation,
    PostgresConfigEpochUnitOfWork,
    PostgresUnitOfWork,
)
from ci_coordinator.persistence.config_epoch_registration_data_attestation import (
    config_epoch_registration_pairs_match_contract,
)
from ci_coordinator.persistence.config_epoch_registration_schema_attestation import (
    config_epoch_registration_schema_matches_contract,
)
from ci_coordinator.persistence.connection import create_postgres_engine

from ._audit_replay_support import load_test_audit_records
from ._config_epoch_support import config_epoch_draft

pytestmark = pytest.mark.persistence

_ACTOR = "keycloak-human:v1:" + "a" * 64
_OCCURRED_AT = "2026-09-01T10:00:00.000Z"


def test_registration_commit_exact_replay_conflict_and_content_reuse(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = config_epoch_draft()
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                committed = await unit_of_work.config_epochs.register_operation(
                    _prepared(draft, "register-1")
                )
                assert isinstance(committed, ConfigEpochRegistrationCommitted)
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                replay = await unit_of_work.config_epochs.register_operation(
                    _prepared(
                        draft,
                        "register-1",
                        occurred_at="2026-09-01T10:01:00.000Z",
                    )
                )
                actor_conflict = await unit_of_work.config_epochs.register_operation(
                    _prepared(draft, "register-1", actor="keycloak-human:v1:" + "b" * 64)
                )
                source_conflict = await unit_of_work.config_epochs.register_operation(
                    _prepared(config_epoch_draft(name="changed"), "register-1")
                )
                reused_content = await unit_of_work.config_epochs.register_operation(
                    _prepared(draft, "register-2")
                )
                await unit_of_work.commit()
            assert isinstance(replay, ConfigEpochRegistrationReplay)
            assert replay.record == committed.record
            assert isinstance(actor_conflict, ConfigEpochRegistrationOperationConflict)
            assert actor_conflict.existing == committed.record
            assert isinstance(source_conflict, ConfigEpochRegistrationOperationConflict)
            assert source_conflict.existing == committed.record
            assert isinstance(reused_content, ConfigEpochRegistrationCommitted)
            assert reused_content.record.epoch_id == committed.record.epoch_id

            async with PostgresUnitOfWork(engine) as unit_of_work:
                events = await load_test_audit_records(unit_of_work.audit_events)
                await unit_of_work.rollback()
            async with engine.connect() as connection:
                counts = tuple(
                    (
                        await connection.execute(
                            text(
                                "SELECT "
                                "(SELECT count(*) FROM ci_coordinator.config_epochs), "
                                "(SELECT count(*) FROM ci_coordinator.config_epoch_registrations)"
                            )
                        )
                    ).one()
                )
            assert counts == (1, 2)
            assert [event.event_type for event in events] == [
                "config-epoch-registration/v1",
                "config-epoch-registration/v1",
            ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_registration_audit_conflict_rolls_back_content_and_receipt(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = config_epoch_draft()
        operation_id = "audit-conflict"
        idempotency_key = (
            f"config-epoch-registration:{draft.scope.installation_id}:"
            f"{draft.scope.repository_id}:{operation_id}"
        )
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresUnitOfWork(engine) as unit_of_work:
                appended = await unit_of_work.audit_events.append(
                    prepare_audit_event(
                        AuditEventInput(
                            idempotency_key=idempotency_key,
                            subject_type="policy-decision",
                            subject_id="unrelated",
                            event_type="unrelated",
                            created_at=_OCCURRED_AT,
                            actor=_ACTOR,
                            payload={"unrelated": True},
                        )
                    )
                )
                assert isinstance(appended, AuditAppendAppended)
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                with pytest.raises(PersistenceInvariantViolation, match="idempotency"):
                    await unit_of_work.config_epochs.register_operation(
                        _prepared(draft, operation_id)
                    )
                with pytest.raises(RuntimeError, match="not active"):
                    await unit_of_work.commit()

            async with engine.connect() as connection:
                counts = tuple(
                    (
                        await connection.execute(
                            text(
                                "SELECT "
                                "(SELECT count(*) FROM ci_coordinator.config_epochs), "
                                "(SELECT count(*) FROM ci_coordinator.config_epoch_registrations), "
                                "(SELECT count(*) FROM ci_coordinator.audit_events)"
                            )
                        )
                    ).one()
                )
            assert counts == (0, 0, 1)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_registration_replay_rejects_corrupted_owner_event(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = config_epoch_draft()
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        migration_engine = create_postgres_engine(postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(runtime_engine) as unit_of_work:
                committed = await unit_of_work.config_epochs.register_operation(
                    _prepared(draft, "corrupted-replay")
                )
                assert isinstance(committed, ConfigEpochRegistrationCommitted)
                await unit_of_work.commit()

            async with migration_engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE ci_coordinator.audit_events "
                        "SET payload_canonical_json = :payload_canonical_json "
                        "WHERE audit_event_id = :audit_event_id"
                    ),
                    {
                        "audit_event_id": committed.record.audit_event_id,
                        "payload_canonical_json": b'{"tampered":true}',
                    },
                )

            with pytest.raises(PersistenceInvariantViolation, match="event integrity"):
                async with PostgresConfigEpochUnitOfWork(runtime_engine) as unit_of_work:
                    await unit_of_work.config_epochs.register_operation(
                        _prepared(draft, "corrupted-replay")
                    )
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_concurrent_same_operation_commits_at_most_one_registration_pair(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = config_epoch_draft()
        engine = create_postgres_engine(runtime_postgres_database_url)

        async def register() -> object:
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                result = await unit_of_work.config_epochs.register_operation(
                    _prepared(draft, "concurrent-registration")
                )
                await unit_of_work.commit()
                return result

        try:
            left, right = await asyncio.gather(register(), register())
            assert (
                sum(
                    isinstance(result, ConfigEpochRegistrationCommitted) for result in (left, right)
                )
                == 1
            )
            assert (
                sum(isinstance(result, ConfigEpochRegistrationReplay) for result in (left, right))
                == 1
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_status_uses_bounded_keyset_pages_and_scope_first_source_reads(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        drafts = tuple(config_epoch_draft(name=f"repo-{index}") for index in range(3))
        scope = drafts[0].scope
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                for index, draft in enumerate(drafts):
                    await unit_of_work.config_epochs.register_operation(
                        _prepared(draft, f"register-{index}")
                    )
                await unit_of_work.commit()

            async with PostgresConfigEpochUnitOfWork(engine) as unit_of_work:
                first = await unit_of_work.config_epochs.read_status(
                    scope,
                    after_epoch_id=None,
                    limit=2,
                )
                assert first.epochs.next_cursor is not None
                second = await unit_of_work.config_epochs.read_status(
                    scope,
                    after_epoch_id=first.epochs.next_cursor,
                    limit=2,
                )
                foreign_scope = RepositoryScope(scope.installation_id, scope.repository_id + 1)
                foreign_status = await unit_of_work.config_epochs.read_status(
                    foreign_scope,
                    after_epoch_id=None,
                    limit=2,
                )
                foreign_source = await unit_of_work.config_epochs.load_epoch(
                    foreign_scope,
                    drafts[0].epoch_id,
                )
                exact_source = await unit_of_work.config_epochs.load_epoch(
                    scope,
                    drafts[0].epoch_id,
                )
                await unit_of_work.rollback()
            observed_ids = tuple(item.epoch_id for item in first.epochs.items + second.epochs.items)
            assert observed_ids == tuple(sorted(draft.epoch_id for draft in drafts))
            assert second.epochs.next_cursor is None
            assert foreign_status.epochs.items == ()
            assert foreign_source is None
            assert exact_source == drafts[0]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_registration_schema_attests_and_rows_are_immutable(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        draft = config_epoch_draft()
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        migration_engine = create_postgres_engine(postgres_database_url)
        try:
            async with migration_engine.connect() as connection:
                assert await config_epoch_registration_schema_matches_contract(connection)
            async with PostgresConfigEpochUnitOfWork(runtime_engine) as unit_of_work:
                await unit_of_work.config_epochs.register_operation(_prepared(draft, "immutable"))
                await unit_of_work.commit()
            async with migration_engine.begin() as connection:
                with pytest.raises(Exception, match="config epoch history is immutable"):
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "UPDATE ci_coordinator.config_epoch_registrations "
                                "SET operation_id = 'mutated'"
                            )
                        )
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_registration_schema_attestation_rejects_a_disabled_immutability_trigger(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                assert await config_epoch_registration_schema_matches_contract(connection)
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.config_epoch_registrations "
                        "DISABLE TRIGGER tr_config_epoch_registrations_immutable"
                    )
                )
                assert not await config_epoch_registration_schema_matches_contract(connection)
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.config_epoch_registrations "
                        "ENABLE TRIGGER tr_config_epoch_registrations_immutable"
                    )
                )
                assert await config_epoch_registration_schema_matches_contract(connection)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "corruption",
    (
        "DELETE FROM ci_coordinator.config_epoch_registrations",
        "UPDATE ci_coordinator.config_epoch_registrations "
        "SET operation_id = operation_id || '-changed'",
    ),
    ids=("orphan-owner-event", "mismatched-operation"),
)
def test_registration_data_attestation_rejects_non_bijective_pairs(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    corruption: str,
) -> None:
    async def scenario() -> None:
        draft = config_epoch_draft()
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        migration_engine = create_postgres_engine(postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(runtime_engine) as unit_of_work:
                await unit_of_work.config_epochs.register_operation(
                    _prepared(draft, "pair-attestation")
                )
                await unit_of_work.commit()

            async with migration_engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await config_epoch_registration_pairs_match_contract(connection)
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.config_epoch_registrations "
                            "DISABLE TRIGGER tr_config_epoch_registrations_immutable"
                        )
                    )
                    await connection.execute(text(corruption))
                    assert not await config_epoch_registration_pairs_match_contract(connection)
                finally:
                    await transaction.rollback()

            async with migration_engine.connect() as connection:
                assert await config_epoch_registration_pairs_match_contract(connection)
                await connection.execute(
                    text("CREATE TEMP TABLE attestation_scope_witness (id int)")
                )
                await connection.execute(
                    text("INSERT INTO attestation_scope_witness (id) VALUES (1)")
                )
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def _prepared(
    draft: ValidatedEpochDraft,
    operation_id: str,
    *,
    actor: str = _ACTOR,
    occurred_at: str = _OCCURRED_AT,
) -> PreparedConfigEpochRegistration:
    return prepare_config_epoch_registration(
        ConfigEpochRegistrationCommand(
            draft=draft,
            operation_id=operation_id,
            actor=actor,
            occurred_at=occurred_at,
        )
    )
