from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select, update

from ci_coordinator.operator_controls import (
    ActiveOverride,
    OverrideApplied,
    OverrideAuditEvent,
    OverrideCommand,
)
from ci_coordinator.persistence import (
    PostgresIngressIssuanceUnitOfWork,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.operator_override_repository import (
    DurableOperatorOverrideStore,
    PostgresOperatorOverrideUnitOfWork,
)
from ci_coordinator.persistence.operator_override_schema import operator_overrides
from ci_coordinator.persistence.schema import (
    active_config_epochs,
    issued_plan_envelopes,
)
from ci_coordinator.plan_issuance import (
    IssuanceGuardRejected,
    IssuanceStoreUnavailable,
    SelectedExecution,
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
