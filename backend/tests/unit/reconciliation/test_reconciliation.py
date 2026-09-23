from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass, field

import pytest

from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.reconciliation import (
    DrainResult,
    ObservationAppended,
    ObservationConflict,
    ObservationDuplicate,
    ObservationRevisionConflict,
    OmittedSignal,
    ReconciliationContract,
    ReconciliationFinding,
    ReconciliationFindingKind,
    ReconciliationInput,
    ReconciliationResult,
    ReconciliationScheduler,
    ReconciliationSnapshot,
    ReconciliationState,
    ReconciliationSubject,
    ResultDuplicate,
    ResultRecorded,
    ResultRevisionConflict,
    RoundCompletion,
    SignalConclusion,
    SignalObservation,
    SubjectRegistered,
    SubjectRegistrationConflict,
    SubjectRegistrationDuplicate,
    TickResult,
    append_observation,
    reconcile,
    record_result,
    register_subject,
    summarize_check,
)

SUBJECT = ReconciliationSubject.create(
    installation_id=100,
    repository_id=200,
    event_name="pull_request",
    ref="refs/pull/42/merge",
    base_sha="a" * 40,
    head_sha="b" * 40,
    workflow_run_id=7001,
    run_attempt=1,
)
SUBJECT_ID = SUBJECT.subject_id
EXPECTED = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)
LINT = ProviderSignal.derive(
    execution_profile_id="lint",
    shard_id="ci_shard_fedcba9876543210fedcba9876543210",
)
OMITTED = OmittedSignal(signal_id="quick-check", name="Quick Check")


def completed(
    observation_id: str,
    signal_id: str,
    run_attempt: int,
    conclusion: SignalConclusion,
    *,
    workflow_run_id: int = SUBJECT.workflow_run_id,
    provider_job_id: int = 1,
) -> SignalObservation:
    return SignalObservation(
        observation_id=observation_id,
        subject_id=SUBJECT_ID,
        signal_id=signal_id,
        workflow_run_id=workflow_run_id,
        run_attempt=run_attempt,
        provider_job_id=provider_job_id,
        status="completed",
        conclusion=conclusion,
    )


def in_progress(
    observation_id: str,
    signal_id: str,
    run_attempt: int,
    *,
    workflow_run_id: int = SUBJECT.workflow_run_id,
    provider_job_id: int = 1,
) -> SignalObservation:
    return SignalObservation(
        observation_id=observation_id,
        subject_id=SUBJECT_ID,
        signal_id=signal_id,
        workflow_run_id=workflow_run_id,
        run_attempt=run_attempt,
        provider_job_id=provider_job_id,
        status="in_progress",
        conclusion=None,
    )


def reconciliation_input(
    observations: tuple[SignalObservation, ...],
    *,
    provider_signals: tuple[ProviderSignal, ...] = (EXPECTED,),
    omitted_signals: tuple[OmittedSignal, ...] = (),
) -> ReconciliationInput:
    return ReconciliationInput(
        ReconciliationSnapshot(
            subject=SUBJECT,
            contract=ReconciliationContract(
                provider_signals=provider_signals,
                omitted_signals=omitted_signals,
            ),
            revision=len(observations),
            observations=observations,
        )
    )


@dataclass(frozen=True)
class SafetyCase:
    name: str
    observations: tuple[SignalObservation, ...]
    expected_state: ReconciliationState
    expected_finding: ReconciliationFindingKind | None
    provider_signals: tuple[ProviderSignal, ...] = (EXPECTED,)
    omitted_signals: tuple[OmittedSignal, ...] = ()


SAFETY_CASES = (
    SafetyCase("missing", (), "pending", "missing_expected_signal"),
    SafetyCase(
        "skipped-without-proof",
        (completed("skip-1", EXPECTED.signal_id, 1, "skipped"),),
        "failure",
        "skipped_without_proof",
    ),
    SafetyCase(
        "neutral-without-policy",
        (completed("neutral-1", EXPECTED.signal_id, 1, "neutral"),),
        "failure",
        "neutral_without_policy",
    ),
    SafetyCase(
        "incomplete-state-precedes-success-in-the-same-attempt",
        (
            in_progress("running-1", EXPECTED.signal_id, 1),
            completed("success-1", EXPECTED.signal_id, 1, "success"),
        ),
        "success",
        None,
    ),
    SafetyCase(
        "omitted-execution",
        (
            completed("success-1", EXPECTED.signal_id, 1, "success"),
            in_progress("omitted-1", "quick-check", 1),
        ),
        "conflict",
        "omitted_execution",
        omitted_signals=(OMITTED,),
    ),
    SafetyCase(
        "contradictory-terminal-observations",
        (
            completed("success-1", EXPECTED.signal_id, 1, "success"),
            completed("failure-1", EXPECTED.signal_id, 1, "failure"),
        ),
        "conflict",
        "contradictory_observation",
    ),
    SafetyCase(
        "different-provider-jobs-for-one-signal",
        (
            in_progress("job-1", EXPECTED.signal_id, 1, provider_job_id=1),
            in_progress("job-2", EXPECTED.signal_id, 1, provider_job_id=2),
        ),
        "failure",
        "ambiguous_provider_signal",
    ),
    SafetyCase(
        "complete-required-evidence",
        (
            completed("full-success", EXPECTED.signal_id, 1, "success"),
            completed("lint-success", LINT.signal_id, 1, "success"),
        ),
        "success",
        None,
        provider_signals=tuple(sorted((EXPECTED, LINT), key=lambda signal: signal.signal_id)),
    ),
)


@pytest.mark.parametrize("case", SAFETY_CASES, ids=lambda case: case.name)
def test_reconciliation_safety_falsifiers(case: SafetyCase) -> None:
    result = reconcile(
        reconciliation_input(
            case.observations,
            provider_signals=case.provider_signals,
            omitted_signals=case.omitted_signals,
        )
    )

    assert result.state == case.expected_state
    assert result.is_success is (case.expected_state == "success")
    if case.expected_finding is not None:
        assert case.expected_finding in {finding.kind for finding in result.findings}


def test_empty_expected_contract_cannot_succeed() -> None:
    result = reconcile(reconciliation_input((), provider_signals=()))

    assert result.state == "failure"
    assert result.is_success is False
    assert result.findings[0].kind == "missing_expected_contract"


def test_check_summary_never_promotes_non_success_result() -> None:
    result = reconcile(reconciliation_input(()))
    summary = summarize_check(result)

    assert summary.conclusion == "pending"
    assert "not observed" in summary.summary


def test_observation_contracts_are_immutable() -> None:
    observation = completed("success-1", EXPECTED.signal_id, 1, "success")

    with pytest.raises(FrozenInstanceError):
        # noinspection PyDataclass
        observation.run_attempt = 2  # type: ignore[misc]


def test_reconciliation_runtime_literals_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="status is unsupported"):
        SignalObservation(
            "invalid-status",
            SUBJECT_ID,
            EXPECTED.signal_id,
            SUBJECT.workflow_run_id,
            SUBJECT.run_attempt,
            1,
            "unsupported",  # type: ignore[arg-type]
            None,
        )
    with pytest.raises(ValueError, match="conclusion is unsupported"):
        completed(
            "invalid-conclusion",
            EXPECTED.signal_id,
            1,
            "unsupported",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="finding kind is unsupported"):
        ReconciliationFinding(
            kind="unsupported",  # type: ignore[arg-type]
            signal_id=None,
            observation_ids=(),
            message="invalid",
        )
    with pytest.raises(ValueError, match="result state is unsupported"):
        ReconciliationResult(SUBJECT_ID, "unsupported", ())  # type: ignore[arg-type]


def test_subject_identity_rejects_an_opaque_alias_for_another_provider_decision() -> None:
    with pytest.raises(ValueError, match="does not match its canonical identity"):
        ReconciliationSubject(
            schema_version=SUBJECT.schema_version,
            installation_id=SUBJECT.installation_id,
            repository_id=SUBJECT.repository_id,
            event_name=SUBJECT.event_name,
            ref=SUBJECT.ref,
            base_sha=SUBJECT.base_sha,
            head_sha=SUBJECT.head_sha,
            workflow_run_id=SUBJECT.workflow_run_id,
            run_attempt=SUBJECT.run_attempt,
            subject_id="0" * 64,
        )


def test_snapshot_rejects_a_revision_that_is_not_explained_by_observations() -> None:
    with pytest.raises(ValueError, match="must equal its observation count"):
        ReconciliationSnapshot(
            SUBJECT,
            ReconciliationContract((EXPECTED,), ()),
            revision=1,
            observations=(),
        )


def test_snapshot_rejects_an_observation_from_another_run_attempt() -> None:
    with pytest.raises(ValueError, match="exact run attempt"):
        ReconciliationSnapshot(
            SUBJECT,
            ReconciliationContract((EXPECTED,), ()),
            revision=1,
            observations=(completed("foreign-attempt", EXPECTED.signal_id, 2, "success"),),
        )


def test_snapshot_rejects_an_observation_from_another_workflow_run() -> None:
    with pytest.raises(ValueError, match="exact workflow run"):
        ReconciliationSnapshot(
            SUBJECT,
            ReconciliationContract((EXPECTED,), ()),
            revision=1,
            observations=(
                completed(
                    "foreign-run",
                    EXPECTED.signal_id,
                    1,
                    "success",
                    workflow_run_id=SUBJECT.workflow_run_id + 1,
                ),
            ),
        )


def test_registration_is_exactly_idempotent_but_contract_changes_conflict() -> None:
    contract = ReconciliationContract((EXPECTED,), ())
    created = register_subject(None, SUBJECT, contract)

    assert isinstance(created, SubjectRegistered)
    assert isinstance(
        register_subject(created.snapshot, SUBJECT, contract), SubjectRegistrationDuplicate
    )
    changed_contract = ReconciliationContract((EXPECTED,), (OMITTED,))
    assert isinstance(
        register_subject(created.snapshot, SUBJECT, changed_contract), SubjectRegistrationConflict
    )


def test_contract_rejects_duplicate_provider_signal_identity() -> None:
    with pytest.raises(ValueError, match="strictly ordered by unique signal_id"):
        ReconciliationContract(provider_signals=(EXPECTED, EXPECTED), omitted_signals=())


def test_observation_cas_distinguishes_replay_conflict_and_stale_new_write() -> None:
    snapshot = ReconciliationSnapshot(
        SUBJECT,
        ReconciliationContract((EXPECTED,), ()),
        revision=0,
        observations=(),
    )
    first = completed("observation-1", EXPECTED.signal_id, 1, "success")
    appended = append_observation(snapshot, expected_revision=0, observation=first)

    assert isinstance(appended, ObservationAppended)
    assert appended.snapshot.revision == 1
    assert isinstance(
        append_observation(appended.snapshot, expected_revision=0, observation=first),
        ObservationDuplicate,
    )
    conflicting = completed("observation-1", EXPECTED.signal_id, 1, "failure")
    assert isinstance(
        append_observation(appended.snapshot, expected_revision=1, observation=conflicting),
        ObservationConflict,
    )
    new_observation = completed("observation-2", EXPECTED.signal_id, 1, "failure")
    assert isinstance(
        append_observation(appended.snapshot, expected_revision=0, observation=new_observation),
        ObservationRevisionConflict,
    )

    with pytest.raises(ValueError, match="another run attempt"):
        append_observation(
            appended.snapshot,
            expected_revision=1,
            observation=completed("foreign-attempt", EXPECTED.signal_id, 2, "success"),
        )

    with pytest.raises(ValueError, match="another workflow run"):
        append_observation(
            appended.snapshot,
            expected_revision=1,
            observation=completed(
                "foreign-run",
                EXPECTED.signal_id,
                1,
                "success",
                workflow_run_id=SUBJECT.workflow_run_id + 1,
            ),
        )


def test_result_cas_replays_an_uncertain_commit_but_rejects_a_stale_new_result() -> None:
    snapshot = reconciliation_input(
        (completed("observation-1", EXPECTED.signal_id, 1, "success"),)
    ).snapshot
    result = reconcile(ReconciliationInput(snapshot))
    recorded = record_result(snapshot, expected_revision=1, result=result, existing=None)

    assert isinstance(recorded, ResultRecorded)
    advanced = append_observation(
        snapshot,
        expected_revision=1,
        observation=completed("observation-2", EXPECTED.signal_id, 1, "success"),
    )
    assert isinstance(advanced, ObservationAppended)
    assert isinstance(
        record_result(advanced.snapshot, expected_revision=1, result=result, existing=result),
        ResultDuplicate,
    )
    assert isinstance(
        record_result(advanced.snapshot, expected_revision=1, result=result, existing=None),
        ResultRevisionConflict,
    )


def test_scheduler_is_single_flight_and_refuses_future_ticks_after_stop() -> None:
    runner = ControlledRound()

    async def scenario() -> tuple[TickResult, TickResult, TickResult, DrainResult, TickResult]:
        scheduler = ReconciliationScheduler(runner)
        first = await scheduler.tick()
        await runner.started.wait()
        second = await scheduler.tick()
        runner.release.set()
        await runner.completed.wait()
        third = await scheduler.tick()
        shutdown = await scheduler.stop_and_drain(1)
        after_stop = await scheduler.tick()
        return first, second, third, shutdown.result, after_stop

    assert asyncio.run(scenario()) == (
        TickResult.STARTED,
        TickResult.ALREADY_RUNNING,
        TickResult.STARTED,
        DrainResult.DRAINED,
        TickResult.STOPPED,
    )


def test_scheduler_aborts_and_keeps_persistence_open_when_drain_times_out() -> None:
    runner = ControlledRound(ignore_abort=True)

    async def scenario() -> tuple[DrainResult, bool, DrainResult]:
        scheduler = ReconciliationScheduler(runner)
        assert await scheduler.tick() is TickResult.STARTED
        await runner.started.wait()
        timed_out = await scheduler.stop_and_drain(0.001)
        runner.release.set()
        drained = await scheduler.stop_and_drain(1)
        return timed_out.result, timed_out.persistence_may_close, drained.result

    assert asyncio.run(scenario()) == (DrainResult.TIMED_OUT, False, DrainResult.DRAINED)
    assert runner.abort_signals and runner.abort_signals[0].is_set()


def test_scheduler_classifies_an_active_task_cancellation_as_failed_drain() -> None:
    async def cancelled_round(_: asyncio.Event, /) -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)

    async def scenario() -> DrainResult:
        scheduler = ReconciliationScheduler(cancelled_round)
        assert await scheduler.tick() is TickResult.STARTED
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return (await scheduler.stop_and_drain(1)).result

    assert asyncio.run(scenario()) is DrainResult.FAILED


def test_scheduler_consumes_a_failed_round_before_starting_the_next() -> None:
    class FailingThenCompletingRound:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, abort_signal: asyncio.Event, /) -> None:
            del abort_signal
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider poll failed")

    async def scenario() -> tuple[TickResult, TickResult, DrainResult]:
        runner = FailingThenCompletingRound()
        scheduler = ReconciliationScheduler(runner)
        first = await scheduler.tick()
        await asyncio.sleep(0)
        second = await scheduler.tick()
        await asyncio.sleep(0)
        shutdown = await scheduler.stop_and_drain(1)
        return first, second, shutdown.result

    assert asyncio.run(scenario()) == (
        TickResult.STARTED,
        TickResult.STARTED_AFTER_FAILURE,
        DrainResult.DRAINED,
    )


def test_scheduler_projects_each_active_round_terminal_outcome() -> None:
    class FailingThenCompletingRound:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, abort_signal: asyncio.Event, /) -> None:
            del abort_signal
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider poll failed")

    async def scenario() -> tuple[RoundCompletion, RoundCompletion, RoundCompletion]:
        scheduler = ReconciliationScheduler(FailingThenCompletingRound())
        absent = await scheduler.wait_for_active()
        assert await scheduler.tick() is TickResult.STARTED
        failed = await scheduler.wait_for_active()
        assert await scheduler.tick() is TickResult.STARTED_AFTER_FAILURE
        succeeded = await scheduler.wait_for_active()
        assert (await scheduler.stop_and_drain(1)).persistence_may_close is True
        return absent, failed, succeeded

    assert asyncio.run(scenario()) == (
        RoundCompletion.ABSENT,
        RoundCompletion.FAILED,
        RoundCompletion.SUCCEEDED,
    )


@dataclass
class ControlledRound:
    ignore_abort: bool = False
    started: asyncio.Event = field(default_factory=asyncio.Event)
    completed: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    abort_signals: list[asyncio.Event] = field(default_factory=list)

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        self.abort_signals.append(abort_signal)
        self.started.set()
        if self.ignore_abort:
            await self.release.wait()
        else:
            abort_waiter = asyncio.create_task(abort_signal.wait())
            release_waiter = asyncio.create_task(self.release.wait())
            await asyncio.wait((abort_waiter, release_waiter), return_when=asyncio.FIRST_COMPLETED)
            abort_waiter.cancel()
            release_waiter.cancel()
        self.completed.set()
