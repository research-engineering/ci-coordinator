from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

import ci_coordinator.persistence.compatibility_fence as fence_module
import ci_coordinator.persistence.workbench_repository as workbench_module
from ci_coordinator.audit_replay import verify_audit_event_integrity
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import (
    ConfigEpochActivationApplied,
    ConfigEpochActivationCommand,
    ConfigEpochRegistrationCreated,
    prepare_config_epoch_activation,
)
from ci_coordinator.kernel import FixedClock
from ci_coordinator.operator_controls import (
    ActiveOverride,
    OverrideApplied,
    OverrideAuditEvent,
    OverrideCommand,
)
from ci_coordinator.persistence import (
    PostgresConfigEpochUnitOfWork,
    PostgresIngressIssuanceUnitOfWork,
    PostgresShadowReconciliationUnitOfWork,
    PostgresWorkbenchRepository,
    TransactionalReconciliationStore,
)
from ci_coordinator.persistence.audit_codec import row_to_record
from ci_coordinator.persistence.compatibility_admission import admit_schema_dependent_operation
from ci_coordinator.persistence.compatibility_contracts import CapabilityDeclaration
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityProfile,
    CompatibilityTimeouts,
    load_bundled_profile,
)
from ci_coordinator.persistence.compatibility_repository import CurrentCompatibility
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.operator_override_repository import (
    DurableOperatorOverrideStore,
    PostgresOperatorOverrideUnitOfWork,
)
from ci_coordinator.persistence.schema import (
    audit_events,
    issued_plan_envelopes,
    reconciliation_results,
)
from ci_coordinator.persistence.workbench_queries import workbench_snapshot_statement
from ci_coordinator.plan_issuance import (
    AuthenticatedRunBinding,
    FullCiExecution,
    IssuedPlanRecord,
    PlanRequest,
    RepositoryBinding,
    SignedPlanEnvelope,
    SignedPlanPayload,
)
from ci_coordinator.reconciliation import (
    ReconciliationFinding,
    ReconciliationResult,
    ReconciliationSubject,
    ResultRecorded,
)
from ci_coordinator.workbench_read_models import MAX_WORKBENCH_SECTION_ITEMS, WorkbenchReadError

from ._config_epoch_support import config_epoch_draft
from ._reconciliation_support import POLICY, claim, contract, database_time, register

pytestmark = pytest.mark.persistence

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
PRIMARY_SCOPE = RepositoryScope(100, 200)
OTHER_SCOPE = RepositoryScope(100, 201)


@pytest.mark.parametrize("limit", [1, 10, MAX_WORKBENCH_SECTION_ITEMS])
def test_empty_workbench_padding_never_becomes_a_domain_record(
    runtime_postgres_database_url: str, limit: int
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            snapshot = await PostgresWorkbenchRepository(engine).load(PRIMARY_SCOPE, limit=limit)
            assert snapshot.ledger_revision == 0
            assert not any(
                (
                    snapshot.plans,
                    snapshot.runs,
                    snapshot.overrides,
                    snapshot.config_epochs,
                    snapshot.audit_events,
                )
            )
            assert not any(
                (
                    snapshot.truncated.plans,
                    snapshot.truncated.runs,
                    snapshot.truncated.overrides,
                    snapshot.truncated.config_epochs,
                    snapshot.truncated.audit_events,
                )
            )
            async with engine.connect() as connection:
                rows = (
                    (await connection.execute(workbench_snapshot_statement(PRIMARY_SCOPE, limit)))
                    .mappings()
                    .all()
                )
            assert len(rows) == limit + 1
            assert tuple(row["position"] for row in rows) == tuple(range(1, limit + 2))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_workbench_snapshot_sees_a_transaction_committed_before_fence_admission(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        migration = create_postgres_engine(postgres_database_url)
        profile = load_bundled_profile()
        configured = asyncio.Event()
        configure = fence_module.configure_transaction_timeouts
        admit = admit_schema_dependent_operation
        observed_visibility: list[bool] = []
        admission_times: list[datetime] = []

        async def observe_configured(
            connection: AsyncConnection, timeouts: CompatibilityTimeouts
        ) -> None:
            await configure(connection, timeouts)
            configured.set()

        try:
            async with migration.connect() as connection:
                transaction = await connection.begin()
                task = None
                try:
                    await acquire_compatibility_fence(
                        connection, profile, CompatibilityFenceMode.MIGRATION
                    )
                    xid = str(await connection.scalar(text("SELECT pg_current_xact_id()")))

                    async def observe_admission(
                        reader: AsyncConnection,
                        configured_profile: CompatibilityProfile,
                        required: tuple[CapabilityDeclaration, ...],
                    ) -> CurrentCompatibility:
                        visible = await reader.scalar(
                            text(
                                "SELECT pg_visible_in_snapshot("
                                "CAST(:xid AS xid8), pg_current_snapshot())"
                            ),
                            {"xid": xid},
                        )
                        observed_visibility.append(visible is True)
                        admitted_at = await reader.scalar(text("SELECT clock_timestamp()"))
                        assert isinstance(admitted_at, datetime)
                        admission_times.append(admitted_at)
                        return await admit(reader, configured_profile, required)

                    monkeypatch.setattr(
                        fence_module, "configure_transaction_timeouts", observe_configured
                    )
                    # noinspection PyUnresolvedReferences
                    monkeypatch.setattr(
                        workbench_module, "admit_schema_dependent_operation", observe_admission
                    )
                    task = asyncio.create_task(
                        PostgresWorkbenchRepository(runtime).load(PRIMARY_SCOPE, limit=1)
                    )
                    async with asyncio.timeout(5):
                        await configured.wait()
                        await transaction.commit()
                        snapshot = await task
                    assert snapshot.ledger_revision == 0
                    assert observed_visibility == [True]
                    assert snapshot.observed_at >= admission_times[0]
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    if transaction.is_active:
                        await transaction.rollback()
        finally:
            await runtime.dispose()
            await migration.dispose()

    asyncio.run(scenario())


def test_all_workbench_families_keep_scope_ties_types_and_independent_bounds(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        overrides = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(engine))
        primary_subjects: list[str] = []
        primary_overrides: list[str] = []
        primary_audit_ids: set[str] = set()
        try:
            for scope in (PRIMARY_SCOPE, OTHER_SCOPE, RepositoryScope(101, 200)):
                drafts = sorted(
                    (
                        config_epoch_draft(
                            installation_id=scope.installation_id,
                            repository_id=scope.repository_id,
                            name=f"repository-{index}",
                        )
                        for index in range(3)
                    ),
                    key=lambda draft: draft.epoch_id,
                )
                async with PostgresConfigEpochUnitOfWork(engine) as transaction:
                    for draft in drafts:
                        assert isinstance(
                            await transaction.config_epochs.register(draft),
                            ConfigEpochRegistrationCreated,
                        )
                    command = ConfigEpochActivationCommand(
                        scope=scope,
                        target_epoch_id=drafts[0].epoch_id,
                        expected_revision=None,
                        operation_id="activate-workbench",
                        actor="operator:123",
                        occurred_at="2026-07-14T12:00:00.000Z",
                        proposal_manifest_id="proposal:" + "f" * 32,
                        authority_evidence_hash="a" * 64,
                        authority_observed_at="2026-07-14T11:59:59.000Z",
                    )
                    assert isinstance(
                        await transaction.config_epochs.activate(
                            prepare_config_epoch_activation(command)
                        ),
                        ConfigEpochActivationApplied,
                    )
                    await transaction.commit()
                for index in range(3):
                    run_id = scope.installation_id * 1_000_000 + scope.repository_id * 100 + index
                    record = _record(scope, run_id=run_id)
                    record = IssuedPlanRecord.create(
                        record.idempotency_key,
                        record.envelope.payload.request,
                        replace(
                            record.envelope, issued_at=NOW, expires_at=NOW + timedelta(minutes=5)
                        ),
                    )
                    async with PostgresIngressIssuanceUnitOfWork(engine) as transaction:
                        assert await transaction.issuance.save(record) is None
                        await transaction.commit()
                    subject = ReconciliationSubject.create(
                        installation_id=scope.installation_id,
                        repository_id=scope.repository_id,
                        event_name="pull_request",
                        ref="refs/pull/42/merge",
                        base_sha="a" * 40,
                        head_sha="b" * 40,
                        workflow_run_id=run_id,
                        run_attempt=1,
                    )
                    await register(engine, subject, created_at=NOW)
                    override = ActiveOverride.create(
                        OverrideCommand(
                            "force_full_ci",
                            scope,
                            subject.subject_id,
                            f"override-{index}",
                            "operator",
                            "snapshot witness",
                            NOW + timedelta(hours=1),
                        ),
                        NOW,
                    )
                    assert isinstance(
                        await overrides.apply(override, OverrideAuditEvent.applied(override)),
                        OverrideApplied,
                    )
                    if scope == PRIMARY_SCOPE:
                        primary_subjects.append(subject.subject_id)
                        primary_overrides.append(override.override_id)
                if scope == PRIMARY_SCOPE:
                    async with engine.connect() as connection:
                        primary_audit_ids.update(
                            await connection.scalars(select(audit_events.c.audit_event_id))
                        )

            repository = PostgresWorkbenchRepository(engine)
            complete = await repository.load(PRIMARY_SCOPE, limit=MAX_WORKBENCH_SECTION_ITEMS)
            assert tuple(plan.record_id for plan in complete.plans) == tuple(
                sorted((plan.record_id for plan in complete.plans), reverse=True)
            )
            assert {plan.workflow_run_id for plan in complete.plans} == {
                100020000,
                100020001,
                100020002,
            }
            assert all(plan.issued_at == NOW for plan in complete.plans)
            assert tuple(run.subject_id for run in complete.runs) == tuple(
                sorted(primary_subjects, reverse=True)
            )
            assert all(run.state == "pending" and run.created_at == NOW for run in complete.runs)
            assert tuple(item.override_id for item in complete.overrides) == tuple(
                sorted(primary_overrides, reverse=True)
            )
            assert all(item.applied_at == NOW for item in complete.overrides)
            assert tuple(item.active_revision for item in complete.config_epochs) == (1, None, None)
            assert complete.config_epochs[0].epoch_id < complete.config_epochs[2].epoch_id
            assert complete.config_epochs[1].epoch_id > complete.config_epochs[2].epoch_id
            assert tuple(event.sequence for event in complete.audit_events) == tuple(
                sorted((event.sequence for event in complete.audit_events), reverse=True)
            )
            assert primary_audit_ids
            assert {event.audit_event_id for event in complete.audit_events} == primary_audit_ids
            for limit in (1, 2):
                bounded = await repository.load(PRIMARY_SCOPE, limit=limit)
                assert bounded.ledger_revision == complete.ledger_revision
                for name in ("plans", "runs", "overrides", "config_epochs", "audit_events"):
                    assert getattr(bounded, name) == getattr(complete, name)[:limit]
                    assert getattr(bounded.truncated, name) is (
                        len(getattr(complete, name)) > limit
                    )
            statement = workbench_snapshot_statement(PRIMARY_SCOPE, 1).compile(
                dialect=engine.dialect, compile_kwargs={"literal_binds": True}
            )
            async with engine.connect() as connection:
                explanation = await connection.scalar(
                    text("EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) " + str(statement))
                )
            assert isinstance(explanation, list) and len(explanation) == 1
            assert isinstance(explanation[0], dict)
            nodes = [explanation[0]["Plan"]]
            windows = 0
            while nodes:
                node = nodes.pop()
                assert isinstance(node, dict)
                children = node.get("Plans", [])
                assert isinstance(children, list)
                nodes.extend(children)
                if node.get("Node Type") != "WindowAgg":
                    continue
                windows += 1
                assert len(children) == 1
                child = children[0]
                while child["Node Type"] in {"Sort", "Subquery Scan", "Materialize"}:
                    assert len(child["Plans"]) == 1
                    child = child["Plans"][0]
                assert child["Node Type"] == "Limit"
                assert child["Actual Rows"] <= 2
                assert node["Actual Rows"] <= 2
            assert windows == 5
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_nonempty_workbench_result_is_preserved_and_corruption_rejected(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        subject = ReconciliationSubject.create(
            installation_id=PRIMARY_SCOPE.installation_id,
            repository_id=PRIMARY_SCOPE.repository_id,
            event_name="pull_request",
            ref="refs/pull/42/merge",
            base_sha="a" * 40,
            head_sha="b" * 40,
            workflow_run_id=7001,
            run_attempt=1,
        )
        finding = ReconciliationFinding("failed_expected_signal", "signal-a", (), "Check failed")
        result = ReconciliationResult(subject.subject_id, "failure", (finding,))
        try:
            store = TransactionalReconciliationStore(
                lambda: PostgresShadowReconciliationUnitOfWork(engine),
                FixedClock(await database_time(engine)),
                POLICY,
            )
            await store.register_subject(subject, contract())
            acquired = await claim(engine, "1" * 64)
            assert acquired is not None
            assert isinstance(await store.record_result(acquired, 0, result), ResultRecorded)
            snapshot = await PostgresWorkbenchRepository(engine).load(PRIMARY_SCOPE, limit=1)
            assert len(snapshot.runs) == 1
            run = snapshot.runs[0]
            assert (run.subject_id, run.revision, run.state) == (subject.subject_id, 0, "failure")
            assert tuple((item.kind, item.signal_id, item.message) for item in run.findings) == (
                (finding.kind, finding.signal_id, finding.message),
            )
            async with admin.begin() as connection:
                await connection.execute(
                    update(reconciliation_results)
                    .where(reconciliation_results.c.subject_id == subject.subject_id)
                    .values(semantic_hash="0" * 64)
                )
            with pytest.raises(WorkbenchReadError):
                await PostgresWorkbenchRepository(engine).load(PRIMARY_SCOPE, limit=1)
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_workbench_snapshot_is_scoped_bounded_redacted_and_repeatable(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        repository = PostgresWorkbenchRepository(engine)
        first = _record(PRIMARY_SCOPE, run_id=7001)
        second = _record(PRIMARY_SCOPE, run_id=7002)
        other = _record(OTHER_SCOPE, run_id=8001)
        try:
            for record in (first, second, other):
                async with PostgresIngressIssuanceUnitOfWork(engine) as unit_of_work:
                    assert await unit_of_work.issuance.save(record) is None
                    await unit_of_work.commit()

            snapshot = await repository.load(PRIMARY_SCOPE, limit=1)

            assert snapshot.scope == PRIMARY_SCOPE
            assert snapshot.ledger_revision == 3
            assert tuple(plan.record_id for plan in snapshot.plans) == (second.record_id,)
            assert snapshot.plans[0].execution_mode == "full-ci"
            assert snapshot.truncated.plans
            assert snapshot.truncated.audit_events
            assert tuple(event.subject_id for event in snapshot.audit_events) == (
                second.envelope.payload.plan_id,
            )
            assert all(
                forbidden not in repr(snapshot)
                for forbidden in ("test-signature", "lease_token", "bearer_token")
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_append_after_plan_read_cannot_mix_projection_and_ledger_versions(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        first, second = (_record(PRIMARY_SCOPE, run_id=run_id) for run_id in (7001, 7002))
        inserted = False

        async def save(record: IssuedPlanRecord) -> None:
            async with PostgresIngressIssuanceUnitOfWork(engine) as transaction:
                assert await transaction.issuance.save(record) is None
                await transaction.commit()

        def interleave[**P, T](execute: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
            async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
                nonlocal inserted
                result = await execute(*args, **kwargs)
                if not inserted and "FROM ci_coordinator.issued_plan_envelopes" in str(args[1]):
                    inserted = True
                    await save(second)
                return result

            return wrapped

        try:
            await save(first)
            monkeypatch.setattr(AsyncConnection, "execute", interleave(AsyncConnection.execute))
            async with asyncio.timeout(5):
                snapshot = await PostgresWorkbenchRepository(engine).load(PRIMARY_SCOPE, limit=10)
            assert inserted
            assert snapshot.ledger_revision == 1
            assert tuple(plan.record_id for plan in snapshot.plans) == (first.record_id,)
            assert tuple(event.subject_id for event in snapshot.audit_events) == (
                first.envelope.payload.plan_id,
            )
            successor = await PostgresWorkbenchRepository(engine).load(PRIMARY_SCOPE, limit=10)
            assert successor.ledger_revision == 2
            assert tuple(plan.record_id for plan in successor.plans) == (
                second.record_id,
                first.record_id,
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_workbench_fails_closed_when_projected_scope_disagrees_with_signed_state(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        migration_engine = create_postgres_engine(postgres_database_url)
        record = _record(PRIMARY_SCOPE, run_id=7001)
        try:
            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                assert await unit_of_work.issuance.save(record) is None
                await unit_of_work.commit()
            async with migration_engine.begin() as connection:
                await connection.execute(
                    update(issued_plan_envelopes)
                    .where(issued_plan_envelopes.c.record_id == record.record_id)
                    .values(repository_id=OTHER_SCOPE.repository_id)
                )

            with pytest.raises(WorkbenchReadError):
                await PostgresWorkbenchRepository(runtime_engine).load(OTHER_SCOPE, limit=10)
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_workbench_fails_closed_when_audit_scope_disagrees_with_its_hash(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        migration_engine = create_postgres_engine(postgres_database_url)
        record = _record(PRIMARY_SCOPE, run_id=7001)
        try:
            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                assert await unit_of_work.issuance.save(record) is None
                await unit_of_work.commit()
            async with migration_engine.begin() as connection:
                updated = await connection.execute(
                    update(audit_events)
                    .where(audit_events.c.installation_id == PRIMARY_SCOPE.installation_id)
                    .values(repository_id=OTHER_SCOPE.repository_id)
                )
                assert updated.rowcount == 1
                corrupted = (await connection.execute(select(audit_events))).mappings().one()
                assert corrupted["repository_id"] == OTHER_SCOPE.repository_id
                invalid = verify_audit_event_integrity(row_to_record(dict(corrupted)))
                assert invalid is not None
                assert invalid.reason == "input hash does not match audit event input"

            with pytest.raises(WorkbenchReadError):
                await PostgresWorkbenchRepository(runtime_engine).load(OTHER_SCOPE, limit=10)
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def _record(scope: RepositoryScope, *, run_id: int) -> IssuedPlanRecord:
    repository = RepositoryBinding(
        scope.installation_id,
        scope.repository_id,
        "example-org",
        f"repository-{scope.repository_id}",
    )
    request = PlanRequest(
        schema_version="dynamic-ci-plan-request/v2",
        request_id=f"request-{run_id}",
        installation_id=scope.installation_id,
        repository_id=scope.repository_id,
        owner=repository.owner,
        repository=repository.repository,
        event_name="pull_request",
        ref=f"refs/pull/{run_id}/merge",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=run_id,
        run_attempt=1,
        pull_request_number=run_id,
        execution_sha="c" * 40,
    )
    authenticated_run = AuthenticatedRunBinding(
        issuer="https://token.actions.githubusercontent.com",
        audience="ci-coordinator",
        repository=f"{repository.owner}/{repository.repository}",
        repository_id=scope.repository_id,
        ref=request.ref,
        run_id=run_id,
        run_attempt=1,
        event_name=request.event_name,
        workflow_ref=(
            f"{repository.owner}/{repository.repository}/.github/workflows/ci.yml@refs/heads/main"
        ),
        workflow_sha=None,
        job_workflow_ref=None,
        job_workflow_sha=None,
        check_run_id=None,
        verified_at=NOW,
        verifier_version="test-verifier/v1",
        claim_hash=None,
        execution_sha=request.execution_sha,
    )
    payload = SignedPlanPayload(
        schema_version="dynamic-ci-signed-plan-payload/v2",
        plan_id=f"fallback-{run_id}",
        repository=repository,
        request=request,
        authenticated_run=authenticated_run,
        verified_plan_id=None,
        production_admission_receipt_id=None,
        execution=FullCiExecution(mode="full-ci", reason="test_fallback"),
        verifier_version=None,
        fallback_reason="test_fallback",
    )
    envelope = SignedPlanEnvelope(
        schema_version="dynamic-ci-signed-plan-envelope/v1",
        key_id="test-key",
        algorithm="Ed25519",
        issued_at=NOW + timedelta(seconds=run_id),
        expires_at=NOW + timedelta(seconds=run_id, minutes=5),
        payload=payload,
        signature="test-signature",
    )
    return IssuedPlanRecord.create(f"run:{run_id}:selected", request, envelope)
