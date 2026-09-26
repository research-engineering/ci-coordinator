from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select, text, update

from ci_coordinator.operator_controls import (
    ActiveOverride,
    OverrideApplied,
    OverrideAuditEvent,
    OverrideCommand,
)
from ci_coordinator.operator_controls.resolution import OverrideLookupUnavailable
from ci_coordinator.persistence import (
    PostgresIngressIssuanceUnitOfWork,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.operator_override_codec import _prepare_applied_event
from ci_coordinator.persistence.operator_override_repository import (
    DurableOperatorOverrideStore,
    PostgresOperatorOverrideUnitOfWork,
)
from ci_coordinator.persistence.operator_override_schema import operator_overrides
from ci_coordinator.persistence.schema import (
    active_config_epochs,
    audit_events,
    issued_plan_envelopes,
)
from ci_coordinator.plan_issuance import (
    IssuanceGuardRejected,
    IssuanceStoreUnavailable,
    SelectedExecution,
)
from ci_coordinator.production_admission.current_evidence import (
    CURRENT_PRODUCTION_OBSERVATION_SECONDS,
)

from ._reconciliation_support import database_time
from ._runtime_ingress_issuance_support import (
    _config_draft,
    _initialize_selected_state,
    _insert_epoch,
    _save_selected,
    _selected_record_and_guard,
)
from ._selected_generation_support import initialize_selected_generation
from ._temporal_lock_support import wait_for_blocked_operation

pytestmark = pytest.mark.persistence


def test_selected_issuance_is_linearized_with_the_active_config_epoch(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        active_draft = _config_draft("main")
        replacement_draft = _config_draft("develop")
        record, guard, registration = await _selected_record_and_guard(now, active_draft)
        admin_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with admin_engine.begin() as connection:
                await _insert_epoch(connection, active_draft)
                await connection.execute(
                    insert(active_config_epochs).values(
                        installation_id=guard.scope.installation_id,
                        repository_id=guard.scope.repository_id,
                        epoch_id=guard.config_epoch_id,
                        revision=1,
                    )
                )

            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                assert await unit_of_work.issuance.save(record, guard) == IssuanceGuardRejected(
                    "authority_generation_changed"
                )
                await unit_of_work.commit()

            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                await unit_of_work.production_admissions.register(registration)
                await unit_of_work.commit()

            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                await unit_of_work.production_admissions.register(registration)
                await unit_of_work.commit()

            await initialize_selected_generation(admin_engine, runtime_engine, guard)

            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                assert await unit_of_work.issuance.save(record, guard) is None
                await unit_of_work.commit()

            async with admin_engine.begin() as connection:
                await _insert_epoch(connection, replacement_draft)
                await connection.execute(
                    update(active_config_epochs)
                    .where(
                        active_config_epochs.c.installation_id == guard.scope.installation_id,
                        active_config_epochs.c.repository_id == guard.scope.repository_id,
                    )
                    .values(epoch_id=replacement_draft.epoch_id, revision=2)
                )

            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                rejection = await unit_of_work.issuance.save(record, guard)
                await unit_of_work.commit()
            assert rejection == IssuanceGuardRejected("config_epoch_changed")

            async with admin_engine.begin() as connection:
                await connection.execute(
                    update(active_config_epochs)
                    .where(
                        active_config_epochs.c.installation_id == guard.scope.installation_id,
                        active_config_epochs.c.repository_id == guard.scope.repository_id,
                    )
                    .values(epoch_id=guard.config_epoch_id, revision=3)
                )
            controls = DurableOperatorOverrideStore(
                lambda: PostgresOperatorOverrideUnitOfWork(runtime_engine)
            )
            control_time = datetime.now(UTC)
            disabled = ActiveOverride.create(
                OverrideCommand(
                    kind="disable_omission",
                    scope=guard.scope,
                    subject_id=None,
                    operation_id="selected-issuance-disable",
                    actor="integration-test",
                    reason="prove the issuance kill switch",
                    expires_at=None,
                ),
                control_time,
            )
            assert isinstance(
                await controls.apply(disabled, OverrideAuditEvent.applied(disabled)),
                OverrideApplied,
            )
            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                disabled_result = await unit_of_work.issuance.save(record, guard)
                await unit_of_work.commit()
            assert disabled_result == IssuanceGuardRejected("override_active")

            released = ActiveOverride.create(
                OverrideCommand(
                    kind="enable_omission",
                    scope=guard.scope,
                    subject_id=disabled.override_id,
                    operation_id="selected-issuance-enable",
                    actor="integration-test",
                    reason="prove exact audited release",
                    expires_at=None,
                ),
                control_time,
            )
            assert isinstance(
                await controls.apply(released, OverrideAuditEvent.applied(released)),
                OverrideApplied,
            )
            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                replay = await unit_of_work.issuance.save(record, guard)
                await unit_of_work.commit()
            assert replay == record
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("completion", ["commit", "rollback", "cancel"])
def test_future_force_is_observed_after_the_scope_holder_releases(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    completion: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        save = None
        try:
            draft = _config_draft("main")
            record, guard, registration = await _selected_record_and_guard(
                await database_time(admin), draft
            )
            await _initialize_selected_state(admin, runtime, draft, guard, registration)
            async with PostgresIngressIssuanceUnitOfWork(runtime) as positive:
                assert await positive.issuance.save(record, guard) is None
                await positive.rollback()
            async with admin.connect() as connection:
                assert await connection.scalar(select(issued_plan_envelopes.c.record_id)) is None
                audit_before = await connection.scalar(
                    select(func.count()).select_from(audit_events)
                )
            assert type(audit_before) is int
            applied_at = await database_time(admin) + timedelta(minutes=5)
            future = ActiveOverride.create(
                OverrideCommand(
                    "force_full_ci",
                    guard.scope,
                    guard.reconciliation_subject_id,
                    "future-force-during-selected-issuance",
                    "integration-operator",
                    "committed future force must withhold selected authority",
                    applied_at + timedelta(minutes=5),
                ),
                applied_at,
            )
            async with PostgresOperatorOverrideUnitOfWork(runtime) as writer:
                assert isinstance(
                    await writer.operator_overrides.apply(
                        future, _prepare_applied_event(future, OverrideAuditEvent.applied(future))
                    ),
                    OverrideApplied,
                )
                holder = await writer.operator_overrides._connection.scalar(
                    select(func.pg_backend_pid())
                )
                assert type(holder) is int
                before_release = await database_time(admin)
                save = asyncio.create_task(_save_selected(runtime, record, guard))
                await wait_for_blocked_operation(admin, holder, save)
                async with admin.connect() as observer:
                    assert (
                        await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE :holder = ANY(pg_blocking_pids(pid)) "
                                "AND wait_event_type = 'Lock' AND wait_event = 'advisory' "
                                "AND query LIKE '%pg_advisory_xact_lock(%')"
                            ),
                            {"holder": holder},
                        )
                        is True
                    )
                    assert await observer.scalar(select(issued_plan_envelopes.c.record_id)) is None
                if completion == "cancel":
                    save.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        async with asyncio.timeout(5):
                            await save
                    await writer.rollback()
                elif completion == "commit":
                    await writer.commit()
                else:
                    await writer.rollback()
            if completion != "cancel":
                async with asyncio.timeout(5):
                    result = await save
                expected = (
                    IssuanceGuardRejected("override_active") if completion == "commit" else None
                )
                assert result == expected
            after_release = await database_time(admin)
            assert future.command.expires_at is not None
            assert before_release <= after_release < future.applied_at < future.command.expires_at
            current = guard.current_evidence
            assert current is not None
            assert after_release < min(
                record.envelope.expires_at,
                guard.not_after,
                current.database_started_at
                + timedelta(seconds=CURRENT_PRODUCTION_OBSERVATION_SECONDS),
            )
            async with admin.connect() as observer:
                stored = await observer.scalar(select(issued_plan_envelopes.c.record_id))
                assert stored == (record.record_id if completion == "rollback" else None)
                assert await observer.scalar(select(func.count()).select_from(audit_events)) == (
                    audit_before + (0 if completion == "cancel" else 1)
                )
                override_audit_id = await observer.scalar(
                    select(operator_overrides.c.audit_event_id).where(
                        operator_overrides.c.override_id == future.override_id
                    )
                )
                if completion == "commit":
                    assert type(override_audit_id) is str
                    assert (
                        await observer.scalar(
                            select(audit_events.c.event_type).where(
                                audit_events.c.audit_event_id == override_audit_id
                            )
                        )
                        == b"operator_override_applied"
                    )
                else:
                    assert override_audit_id is None
            if completion == "commit":
                controls = DurableOperatorOverrideStore(
                    lambda: PostgresOperatorOverrideUnitOfWork(runtime)
                )
                assert (
                    await controls.resolve_active(
                        scope=guard.scope,
                        subject_id=guard.reconciliation_subject_id,
                        now=after_release,
                    )
                    == OverrideLookupUnavailable()
                )
                async with PostgresOperatorOverrideUnitOfWork(runtime) as reader:
                    assert (
                        await reader.operator_overrides.resolve_active(
                            scope=guard.scope,
                            subject_id=guard.reconciliation_subject_id,
                            now=after_release,
                        )
                        == OverrideLookupUnavailable()
                    )
        finally:
            if save is not None and not save.done():
                save.cancel()
                await asyncio.gather(save, return_exceptions=True)
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_selected_issuance_rejects_a_noncanonical_active_config_row(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        draft = _config_draft("main")
        record, guard, registration = await _selected_record_and_guard(now, draft)
        admin_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with admin_engine.begin() as connection:
                await _insert_epoch(connection, draft, source_bytes=b"{}")
                await connection.execute(
                    insert(active_config_epochs).values(
                        installation_id=guard.scope.installation_id,
                        repository_id=guard.scope.repository_id,
                        epoch_id=guard.config_epoch_id,
                        revision=1,
                    )
                )
            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                await unit_of_work.production_admissions.register(registration)
                await unit_of_work.commit()

            await initialize_selected_generation(
                admin_engine, runtime_engine, guard, register_subject=False
            )
            with pytest.raises(IssuanceStoreUnavailable):
                await _save_selected(runtime_engine, record, guard)

            async with admin_engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(issued_plan_envelopes.c.record_id).where(
                            issued_plan_envelopes.c.record_id == record.record_id
                        )
                    )
                    is None
                )
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())


def test_native_selected_issuance_round_trips_exact_execution_authority(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        draft = _config_draft("main")
        record, guard, registration = await _selected_record_and_guard(
            now,
            draft,
            native_execution=True,
        )
        admin_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _initialize_selected_state(
                admin_engine,
                runtime_engine,
                draft,
                guard,
                registration,
            )
            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                assert await unit_of_work.issuance.save(record, guard) is None
                await unit_of_work.commit()
            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                replay = await unit_of_work.issuance.save(record, guard)
                await unit_of_work.rollback()

            assert replay == record
            assert replay is not None
            execution = replay.envelope.payload.execution
            assert isinstance(execution, SelectedExecution)
            assert execution.execution_kind == "native-job-set"
            assert execution.test_manifest_id is None
            assert execution.gate_provider_signal.kind == "declared-native"
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())


def test_selected_issuance_rejects_an_enable_without_its_exact_disable_predecessor(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        draft = _config_draft("main")
        record, guard, registration = await _selected_record_and_guard(now, draft)
        admin_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _initialize_selected_state(
                admin_engine,
                runtime_engine,
                draft,
                guard,
                registration,
            )
            controls = DurableOperatorOverrideStore(
                lambda: PostgresOperatorOverrideUnitOfWork(runtime_engine)
            )
            applied_at = await database_time(runtime_engine)
            disabled = ActiveOverride.create(
                OverrideCommand(
                    kind="disable_omission",
                    scope=guard.scope,
                    subject_id=None,
                    operation_id="orphaned-disable",
                    actor="integration-test",
                    reason="negative transition witness",
                    expires_at=None,
                ),
                applied_at,
            )
            enabled = ActiveOverride.create(
                OverrideCommand(
                    kind="enable_omission",
                    scope=guard.scope,
                    subject_id=disabled.override_id,
                    operation_id="enable-with-missing-predecessor",
                    actor="integration-test",
                    reason="negative transition witness",
                    expires_at=None,
                ),
                applied_at + timedelta(milliseconds=1),
            )
            assert isinstance(
                await controls.apply(disabled, OverrideAuditEvent.applied(disabled)),
                OverrideApplied,
            )
            assert isinstance(
                await controls.apply(enabled, OverrideAuditEvent.applied(enabled)),
                OverrideApplied,
            )
            async with admin_engine.begin() as connection:
                await connection.execute(
                    delete(operator_overrides).where(
                        operator_overrides.c.override_id == disabled.override_id
                    )
                )

            with pytest.raises(IssuanceStoreUnavailable):
                await _save_selected(runtime_engine, record, guard)
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())
