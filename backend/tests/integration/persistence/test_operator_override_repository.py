from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.audit_replay import AuditEventInput, prepare_audit_event
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.override import (
    OPERATOR_OVERRIDE_APPLIED_AUDIT_EVENT_TYPE,
    ActiveOverride,
    OverrideAuditEvent,
    OverrideCommand,
    OverrideKind,
)
from ci_coordinator.operator_controls.resolution import (
    ActiveOverrideRecords,
    OverrideLookupUnavailable,
    OverrideResolution,
    resolve_active_overrides,
)
from ci_coordinator.operator_controls.use_cases import (
    OverrideApplied,
    OverrideConflict,
    OverrideDuplicate,
    OverrideUnavailable,
    apply_override,
)
from ci_coordinator.persistence.canonical_row import CanonicalRowCodecError
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.operator_override_repository import (
    DurableOperatorOverrideStore,
    PostgresOperatorOverrideUnitOfWork,
    operator_override_runtime_principal_is_restricted,
    operator_override_state_schema_matches_contract,
)
from ci_coordinator.persistence.operator_override_schema import operator_overrides
from ci_coordinator.persistence.schema import audit_events
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork

from ._audit_replay_support import load_test_audit_records
from .conftest import RUNTIME_ROLE

pytestmark = pytest.mark.persistence

_NOW = datetime(2026, 7, 15, 10, tzinfo=UTC)
_SCOPE = RepositoryScope(101, 202)
_COLUMNS = (
    "override_id",
    "operation_id",
    "installation_id",
    "repository_id",
    "kind",
    "target_subject_id",
    "expires_at",
    "applied_at",
    "record_canonical_json",
    "semantic_hash",
    "audit_event_id",
    "audit_input_hash",
)


class _AllowOperator:
    async def allows(self, *, actor: str, action: object, scope: RepositoryScope) -> bool:
        del actor, action, scope
        return True


def test_state_and_audit_share_one_idempotent_operation_boundary(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        first = _override(operation_id="operation-1", reason="incident")
        conflicting = _override(operation_id="operation-1", reason="different incident")
        try:
            applied = await store.apply(first, OverrideAuditEvent.applied(first))
            duplicate = await store.apply(first, OverrideAuditEvent.applied(first))
            conflict = await store.apply(
                conflicting,
                OverrideAuditEvent.applied(conflicting),
            )

            assert isinstance(applied, OverrideApplied)
            assert isinstance(duplicate, OverrideDuplicate)
            assert isinstance(conflict, OverrideConflict)
            assert await _row_count(postgres_database_url, "operator_overrides") == 1
            assert await _row_count(postgres_database_url, "audit_events") == 1
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_retry_at_a_later_time_returns_the_original_durable_override(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        first_engine = create_postgres_engine(runtime_postgres_database_url)
        first_store = DurableOperatorOverrideStore(
            lambda: PostgresOperatorOverrideUnitOfWork(first_engine)
        )
        first = _override(operation_id="operation-retry")
        try:
            assert isinstance(
                await first_store.apply(first, OverrideAuditEvent.applied(first)),
                OverrideApplied,
            )
        finally:
            await first_engine.dispose()

        retry_engine = create_postgres_engine(runtime_postgres_database_url)
        retry_store = DurableOperatorOverrideStore(
            lambda: PostgresOperatorOverrideUnitOfWork(retry_engine)
        )
        retry = _override(
            operation_id="operation-retry",
            applied_at=_NOW + timedelta(minutes=1),
        )
        try:
            outcome = await retry_store.apply(retry, OverrideAuditEvent.applied(retry))

            assert outcome == OverrideDuplicate(first)
            assert await _row_count(postgres_database_url, "operator_overrides") == 1
            assert await _row_count(postgres_database_url, "audit_events") == 1
        finally:
            await retry_engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_public_retry_after_expiry_returns_the_retained_durable_override(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        command = _override(operation_id="operation-post-expiry-retry").command
        assert command.expires_at is not None
        try:
            applied = await apply_override(
                command,
                authorizer=_AllowOperator(),
                store=store,
                now=_NOW,
            )
            duplicate = await apply_override(
                command,
                authorizer=_AllowOperator(),
                store=store,
                now=command.expires_at + timedelta(minutes=1),
            )
            divergent = _override(
                operation_id="operation-post-expiry-retry",
                reason="different incident",
            ).command
            assert divergent.expires_at is not None
            conflict = await apply_override(
                divergent,
                authorizer=_AllowOperator(),
                store=store,
                now=divergent.expires_at + timedelta(minutes=1),
            )

            assert isinstance(applied, OverrideApplied)
            assert duplicate == OverrideDuplicate(applied.override)
            assert isinstance(conflict, OverrideConflict)
            assert await _row_count(postgres_database_url, "operator_overrides") == 1
            assert await _row_count(postgres_database_url, "audit_events") == 1
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_concurrent_retries_serialize_to_one_state_and_audit_pair(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        first = _override(operation_id="operation-concurrent")
        retry = _override(
            operation_id="operation-concurrent",
            applied_at=_NOW + timedelta(seconds=1),
        )
        try:
            outcomes = await asyncio.gather(
                store.apply(first, OverrideAuditEvent.applied(first)),
                store.apply(retry, OverrideAuditEvent.applied(retry)),
            )

            applied = [outcome for outcome in outcomes if isinstance(outcome, OverrideApplied)]
            duplicate = [outcome for outcome in outcomes if isinstance(outcome, OverrideDuplicate)]
            assert len(applied) == 1
            assert len(duplicate) == 1
            assert duplicate[0].override == applied[0].override
            assert await _row_count(postgres_database_url, "operator_overrides") == 1
            assert await _row_count(postgres_database_url, "audit_events") == 1
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_operation_identity_is_unique_only_inside_exact_repository_scope(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        first = _override(operation_id="shared-operation")
        second = _override(
            operation_id="shared-operation",
            scope=RepositoryScope(303, 404),
        )
        try:
            assert isinstance(
                await store.apply(first, OverrideAuditEvent.applied(first)),
                OverrideApplied,
            )
            assert isinstance(
                await store.apply(second, OverrideAuditEvent.applied(second)),
                OverrideApplied,
            )
            assert await _row_count(postgres_database_url, "operator_overrides") == 2
            assert await _row_count(postgres_database_url, "audit_events") == 2
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_resolution_is_scope_bound_latest_active_per_kind(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        old = _override(
            operation_id="force-old",
            applied_at=_NOW,
            expires_at=_NOW + timedelta(hours=1),
        )
        latest_active = _override(
            operation_id="force-active",
            applied_at=_NOW + timedelta(minutes=10),
            expires_at=_NOW + timedelta(minutes=30),
        )
        latest_expired = _override(
            operation_id="force-expired",
            applied_at=_NOW + timedelta(minutes=20),
            expires_at=_NOW + timedelta(minutes=25),
        )
        disable = _override(
            kind="disable_omission",
            subject_id=None,
            operation_id="disable-dynamic",
            applied_at=_NOW + timedelta(minutes=5),
        )
        try:
            for override in (old, latest_active, latest_expired, disable):
                outcome = await store.apply(override, OverrideAuditEvent.applied(override))
                assert isinstance(outcome, OverrideApplied)

            at = _NOW + timedelta(minutes=27)
            subject = await resolve_active_overrides(
                store,
                scope=_SCOPE,
                subject_id="run-7",
                now=at,
            )
            another_subject = await resolve_active_overrides(
                store,
                scope=_SCOPE,
                subject_id="run-8",
                now=at,
            )
            repository = await resolve_active_overrides(
                store,
                scope=_SCOPE,
                subject_id=None,
                now=at,
            )

            assert subject.lookup_available is True
            assert subject.force_full_ci_override == latest_active
            assert subject.disable_dynamic_override == disable
            assert another_subject.force_full_ci_override is None
            assert another_subject.disable_dynamic_override == disable
            assert repository.force_full_ci_override is None
            assert repository.disable_dynamic_override == disable
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_future_force_population_is_typed_unavailable_without_rewriting_its_pair(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        old = _override(
            operation_id="old-due",
            applied_at=_NOW - timedelta(minutes=20),
            expires_at=_NOW + timedelta(hours=1),
        )
        expired = _override(
            operation_id="newer-expired",
            applied_at=_NOW - timedelta(minutes=5),
            expires_at=_NOW - timedelta(minutes=1),
        )
        futures = tuple(
            _override(
                operation_id=f"future-{index}",
                applied_at=_NOW + timedelta(minutes=5),
                expires_at=_NOW + timedelta(minutes=20),
            )
            for index in range(2)
        )
        other_subject = _override(operation_id="other-subject-due", subject_id="run-8")
        foreign = tuple(
            _override(
                operation_id=f"foreign-{index}",
                subject_id="run-8",
                scope=scope,
                applied_at=_NOW + timedelta(minutes=5),
                expires_at=_NOW + timedelta(minutes=20),
            )
            for index, scope in enumerate((RepositoryScope(303, 202), RepositoryScope(101, 404)))
        )
        try:
            for override in (old, expired, *futures, other_subject, *foreign):
                assert isinstance(
                    await store.apply(override, OverrideAuditEvent.applied(override)),
                    OverrideApplied,
                )
            before = await _stored_override_pairs(engine)
            async with PostgresOperatorOverrideUnitOfWork(engine) as transaction:
                assert (
                    await transaction.operator_overrides.resolve_active(
                        scope=_SCOPE, subject_id="run-7", now=_NOW
                    )
                    == OverrideLookupUnavailable()
                )
                unresolved = await resolve_active_overrides(
                    transaction.operator_overrides,
                    scope=_SCOPE,
                    subject_id="run-7",
                    now=_NOW,
                )
                assert unresolved == OverrideResolution(lookup_available=False)
                assert unresolved.force_full_ci is True
                await transaction.commit()
            assert (
                await store.resolve_active(scope=_SCOPE, subject_id="run-7", now=_NOW)
                == OverrideLookupUnavailable()
            )
            cases = (
                (
                    "run-7",
                    _NOW + timedelta(minutes=5),
                    max(futures, key=lambda row: row.override_id),
                ),
                ("run-7", _NOW + timedelta(minutes=20), old),
                ("run-7", _NOW + timedelta(hours=1), None),
                ("run-8", _NOW, other_subject),
                ("absent-subject", _NOW, None),
                (None, _NOW, None),
            )
            for subject, at, expected in cases:
                async with PostgresOperatorOverrideUnitOfWork(engine) as transaction:
                    assert await transaction.operator_overrides.resolve_active(
                        scope=_SCOPE, subject_id=subject, now=at
                    ) == ActiveOverrideRecords(force_full_ci=expected)
                assert await store.resolve_active(
                    scope=_SCOPE, subject_id=subject, now=at
                ) == ActiveOverrideRecords(force_full_ci=expected)
            retry = ActiveOverride.create(futures[0].command, _NOW + timedelta(minutes=6))
            assert await store.apply(retry, OverrideAuditEvent.applied(retry)) == OverrideDuplicate(
                futures[0]
            )
            assert await _stored_override_pairs(engine) == before
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_future_force_is_decoded_before_it_can_become_unavailable_knowledge(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        future = _override(
            operation_id="malformed-future",
            applied_at=_NOW + timedelta(minutes=5),
            expires_at=_NOW + timedelta(minutes=20),
        )
        try:
            for override in (_override(operation_id="older-valid"), future):
                assert isinstance(
                    await store.apply(override, OverrideAuditEvent.applied(override)),
                    OverrideApplied,
                )
            assert (
                await store.resolve_active(scope=_SCOPE, subject_id="run-7", now=_NOW)
                == OverrideLookupUnavailable()
            )
            async with admin.begin() as connection:
                stored_hash = await connection.scalar(
                    select(operator_overrides.c.semantic_hash).where(
                        operator_overrides.c.override_id == future.override_id
                    )
                )
                assert type(stored_hash) is str and len(stored_hash) == 64
                changed_hash = ("0" if stored_hash[0] != "0" else "1") + stored_hash[1:]
                await connection.execute(text("SET LOCAL session_replication_role = replica"))
                await connection.execute(
                    update(operator_overrides)
                    .where(operator_overrides.c.override_id == future.override_id)
                    .values(semantic_hash=changed_hash)
                )
            async with admin.connect() as connection:
                assert await connection.scalar(text("SHOW session_replication_role")) == "origin"
                assert await operator_override_state_schema_matches_contract(connection)
            before = await _stored_override_pairs(engine)
            with pytest.raises(StoreUnavailable, match="resolution failed") as failure:
                async with PostgresOperatorOverrideUnitOfWork(engine) as transaction:
                    await transaction.operator_overrides.resolve_active(
                        scope=_SCOPE, subject_id="run-7", now=_NOW
                    )
            assert isinstance(failure.value.__cause__, CanonicalRowCodecError)
            assert (
                await store.resolve_active(scope=_SCOPE, subject_id="run-7", now=_NOW)
                == OverrideLookupUnavailable()
            )
            assert await _stored_override_pairs(engine) == before
        finally:
            await engine.dispose()
            await admin.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_latched_omission_control_requires_an_exact_audited_release(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        disabled = _override(
            kind="disable_omission",
            subject_id=None,
            operation_id="disable-latched",
        )
        wrong_release = _override(
            kind="enable_omission",
            subject_id="override_" + "f" * 32,
            operation_id="release-wrong",
            applied_at=_NOW + timedelta(minutes=1),
        )
        release = _override(
            kind="enable_omission",
            subject_id=disabled.override_id,
            operation_id="release-exact",
            applied_at=_NOW + timedelta(minutes=1),
        )
        try:
            assert isinstance(
                await store.apply(disabled, OverrideAuditEvent.applied(disabled)),
                OverrideApplied,
            )
            assert isinstance(
                await store.apply(wrong_release, OverrideAuditEvent.applied(wrong_release)),
                OverrideConflict,
            )
            active = await resolve_active_overrides(
                store,
                scope=_SCOPE,
                subject_id="run-7",
                now=_NOW + timedelta(minutes=2),
            )
            assert active.disable_dynamic_override == disabled

            assert isinstance(
                await store.apply(release, OverrideAuditEvent.applied(release)),
                OverrideApplied,
            )
            enabled = await resolve_active_overrides(
                store,
                scope=_SCOPE,
                subject_id="run-7",
                now=_NOW + timedelta(minutes=2),
            )
            assert enabled.lookup_available is True
            assert enabled.disable_dynamic_override is None
            assert enabled.dynamic_ci_disabled is False
            assert await _row_count(postgres_database_url, "operator_overrides") == 2
            assert await _row_count(postgres_database_url, "audit_events") == 2
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_state_insert_failure_rolls_back_the_preceding_audit_append(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        invalid_for_postgres = _override(operation_id="state\0failure")
        try:
            outcome = await store.apply(
                invalid_for_postgres,
                OverrideAuditEvent.applied(invalid_for_postgres),
            )

            assert isinstance(outcome, OverrideUnavailable)
            assert await _row_count(postgres_database_url, "operator_overrides") == 0
            assert await _row_count(postgres_database_url, "audit_events") == 0
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_repository_rejects_an_audit_event_that_does_not_match_override_state(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        override = _override(operation_id="mismatched-pair")
        mismatched = prepare_audit_event(
            AuditEventInput(
                idempotency_key="operator-override:101:202:mismatched-pair",
                subject_type="policy-decision",
                subject_id="operator-override:101:202",
                event_type=OPERATOR_OVERRIDE_APPLIED_AUDIT_EVENT_TYPE,
                created_at="2026-07-15T10:00:00.000Z",
                actor="operator",
                payload={"invalidPair": True},
            )
        )
        try:
            with pytest.raises(PersistenceInvariantViolation, match="does not match"):
                async with PostgresOperatorOverrideUnitOfWork(engine) as transaction:
                    await transaction.operator_overrides.apply(override, mismatched)
            assert await _row_count(postgres_database_url, "operator_overrides") == 0
            assert await _row_count(postgres_database_url, "audit_events") == 0
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_generic_audit_repository_rejects_pair_owned_override_events(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        prepared = prepare_audit_event(
            AuditEventInput(
                idempotency_key="forbidden-generic-override-append",
                subject_type="policy-decision",
                subject_id="operator-override:101:202",
                event_type=OPERATOR_OVERRIDE_APPLIED_AUDIT_EVENT_TYPE,
                created_at="2026-07-15T10:00:00.000Z",
                actor="operator",
                payload={"invalidOwner": "generic-audit-repository"},
            )
        )
        try:
            with pytest.raises(PersistenceInvariantViolation, match="pair-owned"):
                async with PostgresUnitOfWork(engine) as transaction:
                    await transaction.audit_events.append(prepared)
            assert await _row_count(postgres_database_url, "operator_overrides") == 0
            assert await _row_count(postgres_database_url, "audit_events") == 0
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_rejection_audit_retains_the_complete_immutable_command_projection(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        command = OverrideCommand(
            kind="disable_omission",
            scope=_SCOPE,
            subject_id=None,
            operation_id="rejected-operation",
            actor="operator",
            reason="unapproved emergency action",
            expires_at=None,
        )
        event = OverrideAuditEvent.rejected(command, "unauthorized", _NOW)
        try:
            assert await store.record_rejection(event) is True
            async with PostgresUnitOfWork(engine) as transaction:
                records = await load_test_audit_records(transaction.audit_events)
            assert len(records) == 1
            record = records[0]
            assert (
                record.idempotency_key == "operator-override-rejection:101:202:rejected-operation"
            )
            assert record.subject_id == "operator-override:101:202"
            assert (record.installation_id, record.repository_id) == (101, 202)
            assert record.actor == "operator"
            assert record.payload == {
                "schemaVersion": "operator-override-audit/v1",
                "outcome": "unauthorized",
                "command": {
                    "kind": "disable_omission",
                    "scope": {"installationId": 101, "repositoryId": 202},
                    "subjectId": None,
                    "operationId": "rejected-operation",
                    "actor": "operator",
                    "reason": "unapproved emergency action",
                    "expiresAt": None,
                },
            }
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def test_catalog_attestation_rejects_a_weakened_same_name_constraint(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await operator_override_state_schema_matches_contract(connection)
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.operator_overrides DROP CONSTRAINT "
                            "ck_operator_overrides_record_byte_limit"
                        )
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.operator_overrides ADD CONSTRAINT "
                            "ck_operator_overrides_record_byte_limit CHECK "
                            "(octet_length(record_canonical_json) <= 8192)"
                        )
                    )
                    assert not await operator_override_state_schema_matches_contract(connection)
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_non_utc_command_offsets_round_trip_without_identity_drift(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        await _grant_runtime_columns(postgres_database_url, runtime_postgres_database_url)
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        offset = timezone(timedelta(hours=2))
        applied_at = datetime(2026, 7, 15, 12, tzinfo=offset)
        override = _override(
            operation_id="offset-preserved",
            applied_at=applied_at,
            expires_at=applied_at + timedelta(minutes=30),
        )
        try:
            assert isinstance(
                await store.apply(override, OverrideAuditEvent.applied(override)),
                OverrideApplied,
            )
            resolved = await resolve_active_overrides(
                store,
                scope=_SCOPE,
                subject_id="run-7",
                now=_NOW + timedelta(minutes=1),
            )

            assert resolved.lookup_available is True
            assert resolved.force_full_ci_override == override
            assert resolved.force_full_ci_override is not None
            expires_at = resolved.force_full_ci_override.command.expires_at
            assert expires_at is not None
            assert expires_at.utcoffset() == timedelta(hours=2)
        finally:
            await engine.dispose()
            await _clear_override_state(postgres_database_url)

    asyncio.run(scenario())


def _override(
    *,
    operation_id: str,
    kind: OverrideKind = "force_full_ci",
    subject_id: str | None = "run-7",
    scope: RepositoryScope = _SCOPE,
    reason: str = "safety investigation",
    applied_at: datetime = _NOW,
    expires_at: datetime | None = None,
) -> ActiveOverride:
    expiry = (
        expires_at
        if expires_at is not None
        else (_NOW + timedelta(minutes=45) if kind == "force_full_ci" else None)
    )
    return ActiveOverride.create(
        OverrideCommand(
            kind,
            scope,
            subject_id,
            operation_id,
            "operator",
            reason,
            expiry,
        ),
        applied_at,
    )


async def _stored_override_pairs(
    engine: AsyncEngine,
) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]]:
    async with engine.connect() as connection:
        states = await connection.execute(
            select(operator_overrides).order_by(operator_overrides.c.override_id)
        )
        events = await connection.execute(select(audit_events).order_by(audit_events.c.sequence))
        return tuple(tuple(row) for row in states), tuple(tuple(row) for row in events)


async def _grant_runtime_columns(database_url: str, runtime_database_url: str) -> None:
    engine = create_postgres_engine(database_url)
    columns = ", ".join(_COLUMNS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(f"REVOKE ALL ON ci_coordinator.operator_overrides FROM {RUNTIME_ROLE}")
            )
            await connection.execute(
                text(
                    f"GRANT SELECT ({columns}), INSERT ({columns}) ON "
                    f"ci_coordinator.operator_overrides TO {RUNTIME_ROLE}"
                )
            )
    finally:
        await engine.dispose()

    runtime_engine = create_postgres_engine(runtime_database_url)
    try:
        async with runtime_engine.connect() as connection:
            assert await operator_override_runtime_principal_is_restricted(connection)
    finally:
        await runtime_engine.dispose()


async def _clear_override_state(database_url: str) -> None:
    engine = create_postgres_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE ci_coordinator.production_scope_states, "
                    "ci_coordinator.operator_overrides"
                )
            )
    finally:
        await engine.dispose()


async def _row_count(database_url: str, relation: str) -> int:
    if relation not in {"operator_overrides", "audit_events"}:
        raise AssertionError("test relation is not admitted")
    engine = create_postgres_engine(database_url)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(text(f"SELECT count(*) FROM ci_coordinator.{relation}"))
    finally:
        await engine.dispose()
    assert type(count) is int
    return count
