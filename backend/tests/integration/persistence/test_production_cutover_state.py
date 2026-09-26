from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from production_cutover_support import production_cutover_fixture
from sqlalchemy import func, select

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.operator_controls import (
    ActiveOverride,
    OverrideApplied,
    OverrideAuditEvent,
    OverrideCommand,
    OverrideConflict,
)
from ci_coordinator.operator_controls.resolution import (
    ActiveOverrideRecords,
    OverrideLookupUnavailable,
)
from ci_coordinator.persistence.operator_override_repository import (
    DurableOperatorOverrideStore,
    PostgresOperatorOverrideRepository,
    PostgresOperatorOverrideUnitOfWork,
)
from ci_coordinator.persistence.production_cutover_adapter import (
    TransactionalProductionCutoverStore,
)
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.persistence.schema import (
    audit_events,
    production_evidence_bundles,
    production_scope_states,
)
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverApplied,
    ProductionCutoverRejected,
)

from ._production_cutover_support import cutover_database
from ._reconciliation_support import database_time

pytestmark = pytest.mark.persistence


def test_existing_disable_is_adopted_but_ordinary_enable_cannot_release_the_cutover_latch(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            controls = DurableOperatorOverrideStore(
                lambda: PostgresOperatorOverrideUnitOfWork(db.runtime)
            )
            disabled = ActiveOverride.create(
                OverrideCommand(
                    kind="disable_omission",
                    scope=db.fixture.draft.scope,
                    subject_id=None,
                    operation_id="operator-disable-before-cutover",
                    actor="integration-operator",
                    reason="maintenance window",
                    expires_at=None,
                ),
                await database_time(db.runtime),
            )
            assert isinstance(
                await controls.apply(disabled, OverrideAuditEvent.applied(disabled)),
                OverrideApplied,
            )
            latched = await db.begin(await db.stage())
            assert latched.latch_override_id == disabled.override_id
            enable = ActiveOverride.create(
                OverrideCommand(
                    kind="enable_omission",
                    scope=db.fixture.draft.scope,
                    subject_id=disabled.override_id,
                    operation_id="ordinary-enable-during-cutover",
                    actor="integration-operator",
                    reason="cannot bypass activation",
                    expires_at=None,
                ),
                await database_time(db.runtime),
            )
            assert isinstance(
                await controls.apply(enable, OverrideAuditEvent.applied(enable)), OverrideConflict
            )
            assert await db.store.inspect(db.fixture.draft.scope) == latched
            applied = await db.activate(latched)
            assert isinstance(applied, ProductionCutoverApplied), applied
            assert applied.state.latch_override_id is None

    asyncio.run(scenario())


def test_real_evidence_stage_latch_activation_restart_and_successor(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            fixture, scope = db.fixture, db.fixture.draft.scope
            assert await db.store.load_authority(scope, purpose="active") is None
            staged = await db.stage()
            assert (staged.revision, staged.generation, staged.active_authority_id) == (1, 0, None)
            assert (
                await db.store.load_evidence(scope, fixture.signed.grant.authority_id)
                == fixture.staged.canonical_bytes
            )
            assert (
                await db.store.load_evidence(
                    RepositoryScope(33, 44), fixture.signed.grant.authority_id
                )
                is None
            )
            assert await db.store.load_authority(scope, purpose="active") is None
            latched = await db.begin(staged)
            assert latched.latch_override_id is not None
            restarted = TransactionalProductionCutoverStore(
                lambda: PostgresProductionCutoverUnitOfWork(db.runtime)
            )
            assert await restarted.inspect(scope) == latched
            applied = await db.activate(latched)
            assert isinstance(applied, ProductionCutoverApplied), applied
            active = applied.state
            assert (active.generation, active.revoked_through_generation, active.revision) == (
                1,
                0,
                3,
            )
            assert active.latch_override_id is None
            assert await restarted.inspect(scope) == active
            retained = await restarted.load_authority(scope, purpose="active")
            assert retained is not None
            assert retained.envelope_canonical_json == fixture.signed.content
            assert retained.lookup == fixture.staged.lookup
            async with PostgresOperatorOverrideUnitOfWork(db.runtime) as transaction:
                overrides = await transaction.operator_overrides.resolve_active(
                    scope=scope, subject_id=None, now=await database_time(db.runtime)
                )
                assert isinstance(overrides, ActiveOverrideRecords)
                assert not overrides.force_full_ci

            successor = production_cutover_fixture(
                now=fixture.now, generation=2, signing_key=fixture.signing_key
            )
            next_staged = await db.stage(active.revision, fixture=successor)
            assert next_staged.active_authority_id == fixture.signed.grant.authority_id
            next_latched = await db.begin(next_staged, fixture=successor)
            assert next_latched.revoked_through_generation == 1
            assert next_latched.active_authority_id == fixture.signed.grant.authority_id
            assert await db.store.load_authority(scope, purpose="active") is None
            next_active = await db.activate(next_latched, fixture=successor)
            assert isinstance(next_active, ProductionCutoverApplied), next_active
            assert (next_active.state.generation, next_active.state.revoked_through_generation) == (
                2,
                1,
            )
            assert next_active.state.active_authority_id == successor.signed.grant.authority_id
            assert await restarted.inspect(scope) == next_active.state
            async with db.admin.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(production_evidence_bundles)
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(audit_events)
                        .where(audit_events.c.event_type == b"production_cutover_applied")
                    )
                    == 6
                )

    asyncio.run(scenario())


def test_future_subject_force_does_not_change_repository_latch_transitions(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            controls = DurableOperatorOverrideStore(
                lambda: PostgresOperatorOverrideUnitOfWork(db.runtime)
            )
            at = await database_time(db.runtime)
            future = ActiveOverride.create(
                OverrideCommand(
                    "force_full_ci",
                    db.fixture.draft.scope,
                    "future-subject",
                    "future-subject-before-cutover",
                    "integration-operator",
                    "subject uncertainty must not become a repository latch",
                    at + timedelta(minutes=10),
                ),
                at + timedelta(minutes=5),
            )
            assert isinstance(
                await controls.apply(future, OverrideAuditEvent.applied(future)), OverrideApplied
            )
            assert (
                await controls.resolve_active(scope=future.command.scope, subject_id=None, now=at)
                == ActiveOverrideRecords()
            )
            latched = await db.begin(await db.stage())
            assert latched.latch_override_id is not None
            applied = await db.activate(latched)
            assert isinstance(applied, ProductionCutoverApplied), applied
            assert applied.state.latch_override_id is None
            observed = await database_time(db.runtime)
            assert observed < future.applied_at
            assert (
                await controls.resolve_active(
                    scope=future.command.scope, subject_id="future-subject", now=observed
                )
                == OverrideLookupUnavailable()
            )
            assert (
                await controls.resolve_active(
                    scope=future.command.scope, subject_id=None, now=observed
                )
                == ActiveOverrideRecords()
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["begin", "activate"])
def test_cutover_narrows_unavailable_override_knowledge_before_mutation(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            state = await db.stage()
            if phase == "activate":
                state = await db.begin(state)
            calls: list[tuple[RepositoryScope, str | None]] = []

            async def unavailable(
                self: PostgresOperatorOverrideRepository,
                *,
                scope: RepositoryScope,
                subject_id: str | None,
                now: datetime,
            ) -> OverrideLookupUnavailable:
                del self, now
                calls.append((scope, subject_id))
                return OverrideLookupUnavailable()

            async with db.admin.connect() as connection:
                audit_count = await connection.scalar(
                    select(func.count()).select_from(audit_events)
                )
            with monkeypatch.context() as patch:
                patch.setattr(PostgresOperatorOverrideRepository, "resolve_active", unavailable)
                if phase == "begin":
                    command = db.command("begin", state.revision, hash_object({}))
                    result = await db.store.begin(command)
                else:
                    command, drain, current = await db.activation_inputs(state)
                    result = await db.store.activate(
                        command, grant=db.fixture.signed.grant, current=current, drain=drain
                    )
            assert calls == [(state.scope, None)]
            assert result == ProductionCutoverRejected("override_conflict")
            assert await db.store.inspect(state.scope) == state
            assert await db.store.resolve(command) is None
            async with db.admin.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(audit_events))
                    == audit_count
                )

    asyncio.run(scenario())


def test_command_replay_is_exact_and_remains_historical_after_later_transitions(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            staged = await db.stage()
            command = db.stage_command()
            replay = await db.store.stage(
                command, grant=db.fixture.signed.grant, evidence=db.fixture.staged
            )
            assert replay == ProductionCutoverApplied(staged, duplicate=True)
            for conflicting in (
                replace(command, actor="another-operator"),
                replace(command, reason="other intent"),
                replace(command, expected_revision=1),
                replace(command, kind="begin", input_digest=hash_object({})),
            ):
                assert await db.store.resolve(conflicting) == ProductionCutoverRejected(
                    "command_conflict"
                )
            latched = await db.begin(staged)
            assert isinstance(await db.activate(latched), ProductionCutoverApplied)
            assert await db.store.resolve(command) == ProductionCutoverApplied(
                staged, duplicate=True
            )
            assert await db.store.inspect(db.fixture.draft.scope) != staged

    asyncio.run(scenario())


def test_two_activators_share_one_scope_revision_and_only_one_commits(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            latched = await db.begin(await db.stage())
            command, drain, current = await db.activation_inputs(latched)
            results = await asyncio.gather(
                *(
                    db.store.activate(
                        replace(command, operation_id=operation),
                        grant=db.fixture.signed.grant,
                        current=current,
                        drain=drain,
                    )
                    for operation in ("activation-a", "activation-b")
                )
            )
            assert sum(isinstance(result, ProductionCutoverApplied) for result in results) == 1
            assert sum(isinstance(result, ProductionCutoverRejected) for result in results) == 1
            async with db.admin.connect() as connection:
                state = (await connection.execute(select(production_scope_states))).mappings().one()
                assert (state["generation"], state["revision"]) == (1, 3)
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(audit_events)
                        .where(audit_events.c.event_type == b"production_cutover_applied")
                    )
                    == 3
                )

    asyncio.run(scenario())


def test_active_generation_can_be_revoked_before_staging_its_successor(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            applied = await db.activate(await db.begin(await db.stage()))
            assert isinstance(applied, ProductionCutoverApplied), applied
            active = applied.state
            assert active.staged_authority_id is None
            command = db.command("begin", active.revision, hash_object({}))
            for invalid, rejection in (
                (
                    replace(command, expected_revision=active.revision + 1),
                    ProductionCutoverRejected("revision_changed"),
                ),
                (
                    replace(command, authority_id="production_admission_" + "f" * 32),
                    ProductionCutoverRejected("stage_changed"),
                ),
                (
                    replace(command, scope=RepositoryScope(33, 44)),
                    ProductionCutoverRejected("revision_changed"),
                ),
            ):
                assert await db.store.begin(invalid) == rejection
                assert await db.store.inspect(active.scope) == active
                assert await db.store.resolve(invalid) is None
            latched = await db.begin(active)
            assert latched.generation == latched.revoked_through_generation == 1
            assert latched.active_authority_id == active.active_authority_id
            assert latched.staged_authority_id is None
            assert latched.latch_override_id is not None
            assert await db.activate(latched) == ProductionCutoverRejected("stage_changed")
            assert await db.store.inspect(active.scope) == latched

    asyncio.run(scenario())


def test_rollback_discards_stage_evidence_registration_state_and_audit(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            async with PostgresProductionCutoverUnitOfWork(db.runtime) as transaction:
                result = await transaction.cutover.stage(
                    db.stage_command(), db.fixture.signed.grant, db.fixture.staged
                )
                assert isinstance(result, ProductionCutoverApplied)
                await transaction.rollback()
            assert await db.store.inspect(db.fixture.draft.scope) is None
            assert (
                await db.store.load_evidence(
                    db.fixture.draft.scope, db.fixture.signed.grant.authority_id
                )
                is None
            )
            assert await db.store.resolve(db.stage_command()) is None
            async with db.admin.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(production_evidence_bundles)
                    )
                    == 0
                )
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 0

    asyncio.run(scenario())
