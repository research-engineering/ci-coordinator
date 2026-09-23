from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import create_autospec

import pytest
from production_cutover_support import production_cutover_fixture
from sqlalchemy import select

from ci_coordinator.app.reconciliation_registration import reconciliation_subject
from ci_coordinator.persistence.issued_plan_codec import encode_envelope
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.persistence.production_registration_adapter import (
    TransactionalProductionRegistrationStore,
)
from ci_coordinator.persistence.runtime_adapters import TransactionalIssuanceStore
from ci_coordinator.persistence.runtime_state_profile import load_bundled_runtime_state_profile
from ci_coordinator.persistence.runtime_state_unit_of_work import PostgresIngressIssuanceUnitOfWork
from ci_coordinator.persistence.schema import issued_plan_envelopes, reconciliation_subjects
from ci_coordinator.persistence.shadow_reconciliation_unit_of_work import (
    PostgresShadowReconciliationUnitOfWork,
)
from ci_coordinator.plan_issuance import (
    FullCiExecution,
    IssuanceGuardRejected,
    IssuanceSaveResult,
    Issued,
    IssuedPlanRecord,
    SelectedExecution,
)
from ci_coordinator.plan_issuance.store import IssuanceStore
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.production_admission.current_evidence import (
    CURRENT_PRODUCTION_OBSERVATION_SECONDS,
)
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverApplied,
    ProductionCutoverRejected,
)
from ci_coordinator.production_admission.ports import ProductionRegistrationPersistence
from ci_coordinator.reconciliation import (
    ReconciliationContract,
    ReconciliationResult,
    ReconciliationSubject,
    ResultRecorded,
)

from ._production_cutover_support import cutover_database
from ._production_planning_support import production_planning
from ._reconciliation_support import POLICY, claim, database_time

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("stage", ("staged", "active", "latched", "provider-unavailable"))
def test_actual_dynamic_service_issues_selected_only_under_current_active_authority(
    postgres_database_url: str, runtime_postgres_database_url: str, stage: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(
            postgres_database_url, runtime_postgres_database_url, local_requester=True
        ) as db:
            state = await db.stage()
            if stage != "staged":
                activated = await db.activate(await db.begin(state))
                assert isinstance(activated, ProductionCutoverApplied)
                state = activated.state
            if stage == "latched":
                state = await db.begin(state)
            planning = production_planning(db, provider_available=stage != "provider-unavailable")
            result = await planning.service.request_dynamic_plan(planning.command)
            assert isinstance(result, Issued), result
            selected = stage == "active"
            payload = result.record.envelope.payload
            assert isinstance(payload.execution, SelectedExecution if selected else FullCiExecution)
            assert (payload.verified_plan_id is not None) == selected
            async with db.runtime.connect() as connection:
                persisted = (
                    (await connection.execute(select(issued_plan_envelopes))).mappings().one()
                )
                assert persisted["expires_at"] is not None
                assert persisted["envelope_canonical_json"] == encode_envelope(
                    result.record.envelope, load_bundled_runtime_state_profile()
                )
                row = (
                    (
                        await connection.execute(
                            select(reconciliation_subjects).where(
                                reconciliation_subjects.c.subject_id
                                == reconciliation_subject(planning.command).subject_id
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if selected:
                    assert row is not None
                    assert row["execution_origin"] == "selected"
                    assert row["production_generation"] == state.generation == 1
                    assert row["production_authority_id"] == db.fixture.signed.grant.authority_id
                else:
                    assert row is None or row["execution_origin"] != "selected"

    asyncio.run(scenario())


def test_a_live_signed_plan_blocks_successor_activation_after_local_reconciliation_completes(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(
            postgres_database_url, runtime_postgres_database_url, local_requester=True
        ) as db:
            applied = await db.activate(await db.begin(await db.stage()))
            assert isinstance(applied, ProductionCutoverApplied)
            store = TransactionalIssuanceStore(
                lambda: PostgresIngressIssuanceUnitOfWork(db.runtime)
            )
            outcomes: list[IssuanceSaveResult] = []

            async def save(
                record: IssuedPlanRecord, guard: ProductionIssuanceGuard | None = None
            ) -> IssuanceSaveResult:
                result = await store.save(record, guard)
                outcomes.append(result)
                return result

            observed_store = create_autospec(IssuanceStore, instance=True)
            observed_store.save.side_effect = save
            planning = production_planning(db, plan_ttl_seconds=30, issuer_store=observed_store)
            issued = await planning.service.request_dynamic_plan(planning.command)
            assert isinstance(issued, Issued)
            assert isinstance(issued.record.envelope.payload.execution, SelectedExecution), (
                outcomes,
                issued.record.envelope.issued_at,
                issued.record.envelope.expires_at,
                await database_time(db.runtime),
            )
            acquired = await claim(db.runtime, "1" * 64)
            assert acquired is not None
            assert acquired.subject == reconciliation_subject(planning.command)
            async with PostgresShadowReconciliationUnitOfWork(db.runtime) as transaction:
                result = await transaction.reconciliation.record_result(
                    acquired,
                    acquired.revision,
                    ReconciliationResult(acquired.subject.subject_id, "success", ()),
                )
                assert isinstance(result, ResultRecorded)
                await transaction.commit()
            successor = production_cutover_fixture(
                now=db.fixture.now,
                generation=2,
                signing_key=db.fixture.signing_key,
                local_requester=True,
            )
            staged = await db.stage(applied.state.revision, fixture=successor)
            latched = await db.begin(staged, fixture=successor)
            assert await database_time(db.runtime) < issued.record.envelope.expires_at
            assert await db.activate(latched, fixture=successor) == ProductionCutoverRejected(
                "drain_incomplete"
            )
            assert await db.store.inspect(db.fixture.draft.scope) == latched
            async with asyncio.timeout(31):
                remaining = (
                    issued.record.envelope.expires_at - await database_time(db.runtime)
                ).total_seconds()
                await asyncio.sleep(max(0, remaining) + 0.02)
            assert await database_time(db.runtime) >= issued.record.envelope.expires_at
            activated = await db.activate(latched, fixture=successor)
            assert isinstance(activated, ProductionCutoverApplied)
            assert activated.state.generation == 2
            async with db.runtime.connect() as connection:
                saved = (await connection.execute(select(issued_plan_envelopes))).mappings().one()
                assert (
                    saved["production_admission_authority_id"]
                    == db.fixture.signed.grant.authority_id
                )
                assert saved["envelope_canonical_json"] == encode_envelope(
                    issued.record.envelope, load_bundled_runtime_state_profile()
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ("registration", "issuance", "successor-generation"))
def test_cutover_between_preparation_and_durable_effect_cannot_publish_selected(
    postgres_database_url: str, runtime_postgres_database_url: str, boundary: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(
            postgres_database_url, runtime_postgres_database_url, local_requester=True
        ) as db:
            activated = await db.activate(await db.begin(await db.stage()))
            assert isinstance(activated, ProductionCutoverApplied)
            active = activated.state
            store = TransactionalIssuanceStore(
                lambda: PostgresIngressIssuanceUnitOfWork(db.runtime)
            )
            registrations = TransactionalProductionRegistrationStore(
                lambda: PostgresProductionCutoverUnitOfWork(db.runtime), POLICY
            )
            gated_store = create_autospec(IssuanceStore, instance=True)
            gated_registration = create_autospec(ProductionRegistrationPersistence, instance=True)
            cutover_count = 0
            registration_outcomes: list[bool] = []
            issuance_outcomes: list[IssuanceSaveResult] = []

            async def assert_fresh(guard: ProductionIssuanceGuard) -> None:
                current = guard.current_evidence
                assert current is not None
                now = await database_time(db.runtime)
                assert (
                    current.database_started_at
                    <= now
                    < current.database_started_at
                    + timedelta(seconds=CURRENT_PRODUCTION_OBSERVATION_SECONDS)
                ), (current.database_started_at, now)
                assert now < guard.not_after

            async def save(
                record: IssuedPlanRecord, guard: ProductionIssuanceGuard | None = None
            ) -> IssuanceSaveResult:
                nonlocal cutover_count
                if guard is not None and boundary == "issuance":
                    await db.begin(active)
                    cutover_count += 1
                if guard is not None:
                    await assert_fresh(guard)
                outcome = await store.save(record, guard)
                if guard is not None:
                    await assert_fresh(guard)
                    assert outcome == IssuanceGuardRejected("authority_generation_changed")
                    issuance_outcomes.append(outcome)
                return outcome

            async def register(
                subject: ReconciliationSubject,
                contract: ReconciliationContract,
                guard: ProductionIssuanceGuard,
            ) -> bool:
                nonlocal cutover_count
                await assert_fresh(guard)
                async with PostgresProductionCutoverUnitOfWork(db.runtime) as transaction:
                    assert await transaction.registrations.register(
                        subject, contract, POLICY, guard
                    )
                await assert_fresh(guard)
                if boundary == "registration":
                    await db.begin(active)
                    cutover_count += 1
                elif boundary == "successor-generation":
                    successor = production_cutover_fixture(
                        now=db.fixture.now,
                        generation=2,
                        signing_key=db.fixture.signing_key,
                        local_requester=True,
                    )
                    staged = await db.stage(active.revision, fixture=successor)
                    latched = await db.begin(staged, fixture=successor)
                    applied = await db.activate(latched, fixture=successor)
                    assert isinstance(applied, ProductionCutoverApplied)
                    assert applied.state.generation == 2
                    cutover_count += 1
                await assert_fresh(guard)
                outcome = await registrations.register_selected(subject, contract, guard)
                await assert_fresh(guard)
                assert outcome is (boundary == "issuance")
                registration_outcomes.append(outcome)
                return outcome

            gated_store.save.side_effect = save
            gated_registration.register_selected.side_effect = register
            gated_registration.register_full_ci.side_effect = registrations.register_full_ci
            planning = production_planning(
                db, issuer_store=gated_store, registration=gated_registration
            )
            result = await planning.service.request_dynamic_plan(planning.command)
            assert isinstance(result, Issued), result
            assert cutover_count == 1
            assert registration_outcomes == [boundary == "issuance"]
            assert issuance_outcomes == (
                [IssuanceGuardRejected("authority_generation_changed")]
                if boundary == "issuance"
                else []
            )
            assert isinstance(result.record.envelope.payload.execution, FullCiExecution)
            state = await db.store.inspect(db.fixture.draft.scope)
            assert state is not None
            if boundary == "successor-generation":
                assert state.generation == 2 and state.latch_override_id is None
            else:
                assert state.generation == 1 and state.latch_override_id is not None
            async with db.runtime.connect() as connection:
                rows = (await connection.execute(select(issued_plan_envelopes))).mappings().all()
                assert len(rows) == 1
                assert rows[0]["production_admission_authority_id"] is None
                registrations_rows = (
                    (await connection.execute(select(reconciliation_subjects))).mappings().all()
                )
                assert len(registrations_rows) == (1 if boundary == "issuance" else 0)

    asyncio.run(scenario())
