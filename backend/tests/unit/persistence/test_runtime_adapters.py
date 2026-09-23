from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

import pytest

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import (
    ConfigEpochReplayCommand,
    ConfigEpochStoreUnavailable,
    PreparedConfigEpochActivation,
    PreparedConfigEpochRegistration,
)
from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryStoreUnavailable,
    DurableWorkflowObservation,
    PreparedDeliveryClaim,
)
from ci_coordinator.github_ingestion.ports import prepare_delivery_claim
from ci_coordinator.github_ingestion.provenance import WebhookProvenance
from ci_coordinator.kernel import FixedClock
from ci_coordinator.persistence import (
    PersistenceInvariantViolation,
    PostgresShadowReconciliationUnitOfWork,
    TransactionalConfigEpochStore,
    TransactionalReconciliationStore,
    TransactionalWebhookIngestionStore,
)
from ci_coordinator.persistence.errors import StoreUnavailable
from ci_coordinator.reconciliation import (
    PlanningEvidenceContext,
    ReconciliationAttemptClaim,
    ReconciliationClaimAcquired,
    ReconciliationClaimLost,
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationConvergenceState,
    ReconciliationResult,
    ReconciliationSnapshot,
    ReconciliationSubject,
    ResultDuplicate,
    ResultRecorded,
    SubjectRegistered,
    acquire_reconciliation_claim,
    initial_convergence_state,
)
from ci_coordinator.shadow_mode import (
    CoverageRelation,
    FullCiObservation,
    FullCiResult,
    ShadowCandidate,
    ShadowEvidenceConflict,
    ShadowEvidenceDuplicate,
    ShadowEvidenceRecord,
    ShadowEvidenceStored,
    compare_full_ci,
)

_NOW = datetime(2026, 7, 15, tzinfo=UTC)
_POLICY = ReconciliationConvergencePolicy()
_SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)
type _ConfigStoreOperation = Literal[
    "register",
    "register_operation",
    "load_active",
    "load_epoch",
    "read_status",
    "resolve_operation",
    "activate",
]
_CONFIG_STORE_OPERATIONS: tuple[_ConfigStoreOperation, ...] = (
    "register",
    "register_operation",
    "load_active",
    "load_epoch",
    "read_status",
    "resolve_operation",
    "activate",
)
_CONFIG_STORE_MUTATIONS: tuple[_ConfigStoreOperation, ...] = (
    "register",
    "register_operation",
    "activate",
)


class _ConfigEpochRepository:
    def __init__(self, error: BaseException | None) -> None:
        self._error = error
        self.calls: list[str] = []

    async def register(self, draft: object) -> object:
        del draft
        return self._result("register")

    async def register_operation(self, prepared: object) -> object:
        del prepared
        return self._result("register_operation")

    async def load_active(self, scope: object) -> object:
        del scope
        return self._result("load_active")

    async def load_epoch(self, scope: object, epoch_id: object) -> object:
        del scope, epoch_id
        return self._result("load_epoch")

    async def read_status(
        self,
        scope: object,
        *,
        after_epoch_id: object,
        limit: object,
    ) -> object:
        del scope, after_epoch_id, limit
        return self._result("read_status")

    async def resolve_operation(self, command: object) -> object:
        del command
        return self._result("resolve_operation")

    async def activate(self, prepared: object) -> object:
        del prepared
        return self._result("activate")

    def _result(self, operation: str) -> object:
        self.calls.append(operation)
        if self._error is not None:
            raise self._error
        return object()


class _ConfigEpochTransaction:
    def __init__(
        self,
        operation_error: BaseException | None,
        *,
        commit_error: BaseException | None = None,
    ) -> None:
        self.config_epochs = _ConfigEpochRepository(operation_error)
        self._commit_error = commit_error
        self.commits = 0

    async def __aenter__(self) -> _ConfigEpochTransaction:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def commit(self) -> None:
        self.commits += 1
        if self._commit_error is not None:
            raise self._commit_error


@pytest.mark.parametrize("operation", _CONFIG_STORE_OPERATIONS)
def test_config_epoch_store_translates_persistence_errors_for_every_operation(
    operation: _ConfigStoreOperation,
) -> None:
    persistence_error = StoreUnavailable("database-dsn must stay private")
    transaction = _ConfigEpochTransaction(persistence_error)
    adapter = TransactionalConfigEpochStore(_factory(transaction))

    with pytest.raises(ConfigEpochStoreUnavailable) as caught:
        asyncio.run(_invoke_config_store_operation(adapter, operation))

    assert caught.value.__cause__ is persistence_error
    assert transaction.config_epochs.calls == [operation]
    assert "database-dsn" not in str(caught.value)


@pytest.mark.parametrize("operation", _CONFIG_STORE_OPERATIONS)
def test_config_epoch_store_preserves_cancellation_for_every_operation(
    operation: _ConfigStoreOperation,
) -> None:
    cancellation = asyncio.CancelledError(f"cancelled {operation}")
    transaction = _ConfigEpochTransaction(cancellation)
    adapter = TransactionalConfigEpochStore(_factory(transaction))

    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(_invoke_config_store_operation(adapter, operation))

    assert caught.value is cancellation
    assert transaction.config_epochs.calls == [operation]


@pytest.mark.parametrize("operation", _CONFIG_STORE_MUTATIONS)
def test_config_epoch_store_translates_commit_errors(
    operation: _ConfigStoreOperation,
) -> None:
    persistence_error = StoreUnavailable("commit failed")
    transaction = _ConfigEpochTransaction(None, commit_error=persistence_error)
    adapter = TransactionalConfigEpochStore(_factory(transaction))

    with pytest.raises(ConfigEpochStoreUnavailable) as caught:
        asyncio.run(_invoke_config_store_operation(adapter, operation))

    assert caught.value.__cause__ is persistence_error
    assert transaction.config_epochs.calls == [operation]
    assert transaction.commits == 1


@pytest.mark.parametrize("operation", _CONFIG_STORE_MUTATIONS)
def test_config_epoch_store_preserves_commit_cancellation(
    operation: _ConfigStoreOperation,
) -> None:
    cancellation = asyncio.CancelledError(f"cancelled {operation} commit")
    transaction = _ConfigEpochTransaction(None, commit_error=cancellation)
    adapter = TransactionalConfigEpochStore(_factory(transaction))

    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(_invoke_config_store_operation(adapter, operation))

    assert caught.value is cancellation
    assert transaction.config_epochs.calls == [operation]
    assert transaction.commits == 1


class _DeliveryRepository:
    def __init__(self, result: object) -> None:
        self._result = result

    async def commit(
        self,
        key: PreparedDeliveryClaim,
        observation: DurableWorkflowObservation | None,
    ) -> object:
        del key, observation
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _Transaction:
    def __init__(self, result: object) -> None:
        self.webhook_ingestion = _DeliveryRepository(result)
        self.committed = False

    async def __aenter__(self) -> _Transaction:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def commit(self) -> None:
        self.committed = True


class _ReconciliationRepository:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[tuple[ReconciliationAttemptClaim, int, ReconciliationResult]] = []
        self.registration_calls: list[
            tuple[
                ReconciliationSubject,
                ReconciliationContract,
                ReconciliationConvergenceState,
            ]
        ] = []

    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        convergence: ReconciliationConvergenceState,
    ) -> object:
        self.registration_calls.append((subject, contract, convergence))
        return self._result

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
    ) -> object:
        self.calls.append((claim, expected_revision, result))
        return self._result


class _ShadowRepository:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[ShadowEvidenceRecord] = []

    async def record(self, record: ShadowEvidenceRecord) -> object:
        self.calls.append(record)
        return self._result


class _ReconciliationPairRepository:
    def __init__(self, state: _ReconciliationRepository) -> None:
        self._state = state
        self.registration_calls: list[
            tuple[
                ReconciliationSubject,
                ReconciliationContract,
                ReconciliationConvergenceState,
                datetime,
            ]
        ] = []
        self.result_calls: list[
            tuple[ReconciliationAttemptClaim, int, ReconciliationResult, datetime]
        ] = []

    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        convergence: ReconciliationConvergenceState,
        *,
        occurred_at: datetime,
    ) -> object:
        self.registration_calls.append((subject, contract, convergence, occurred_at))
        return await self._state.register_subject(subject, contract, convergence)

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
        *,
        occurred_at: datetime,
    ) -> object:
        self.result_calls.append((claim, expected_revision, result, occurred_at))
        return await self._state.record_result(
            claim,
            expected_revision,
            result,
        )


class _ShadowTransaction:
    def __init__(self, reconciliation_result: object, shadow_result: object) -> None:
        self.reconciliation = _ReconciliationRepository(reconciliation_result)
        self.reconciliation_pairs = _ReconciliationPairRepository(self.reconciliation)
        self.shadow_evidence = _ShadowRepository(shadow_result)
        self.committed = False

    async def __aenter__(self) -> _ShadowTransaction:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def commit(self) -> None:
        self.committed = True


def test_delivery_adapter_commits_a_claim_before_returning() -> None:
    claim = _delivery_claim()
    transaction = _Transaction(DeliveryClaimed(claim.key))
    adapter = TransactionalWebhookIngestionStore(_factory(transaction))

    result = asyncio.run(adapter.commit(claim, None))

    assert result == DeliveryClaimed(claim.key)
    assert transaction.committed is True


def test_delivery_adapter_maps_storage_failure_without_committing() -> None:
    claim = _delivery_claim()
    transaction = _Transaction(StoreUnavailable("database-dsn must stay private"))
    adapter = TransactionalWebhookIngestionStore(_factory(transaction))

    result = asyncio.run(adapter.commit(claim, None))

    assert isinstance(result, DeliveryStoreUnavailable)
    assert transaction.committed is False
    assert "database-dsn" not in repr(result)


def _delivery_claim() -> PreparedDeliveryClaim:
    return prepare_delivery_claim(
        WebhookProvenance(
            delivery_id="delivery-1",
            event_name="push",
            body_sha256="a" * 64,
            verified_at=_NOW,
            verifier_version="test-verifier/v1",
        )
    )


@pytest.mark.parametrize("duplicate", [False, True])
def test_reconciliation_adapter_commits_terminal_result_and_evidence_atomically(
    duplicate: bool,
) -> None:
    subject = _subject()
    contract = ReconciliationContract((_SIGNAL,), ())
    claim = _claim(subject, contract)
    result = ReconciliationResult(subject.subject_id, "success", ())
    record = _shadow_record(subject)
    reconciliation_result = ResultDuplicate(1, result) if duplicate else ResultRecorded(1, result)
    transaction = _ShadowTransaction(
        reconciliation_result,
        ShadowEvidenceDuplicate(record) if duplicate else ShadowEvidenceStored(record),
    )
    adapter = TransactionalReconciliationStore(_factory(transaction), FixedClock(_NOW), _POLICY)

    outcome = asyncio.run(adapter.record_result_with_evidence(claim, 1, result, (record,)))

    assert outcome == reconciliation_result
    assert transaction.shadow_evidence.calls == [record]
    assert transaction.reconciliation_pairs.result_calls == [(claim, 1, result, _NOW)]
    assert transaction.committed is True


def test_reconciliation_duplicate_cannot_commit_a_new_shadow_record() -> None:
    subject = _subject()
    claim = _claim(subject, ReconciliationContract((_SIGNAL,), ()))
    result = ReconciliationResult(subject.subject_id, "success", ())
    record = _shadow_record(subject)
    transaction = _ShadowTransaction(ResultDuplicate(1, result), ShadowEvidenceStored(record))
    adapter = TransactionalReconciliationStore(_factory(transaction), FixedClock(_NOW), _POLICY)

    outcome = asyncio.run(adapter.record_result_with_evidence(claim, 1, result, (record,)))

    assert isinstance(outcome, ReconciliationClaimLost)
    assert transaction.shadow_evidence.calls == [record]
    assert transaction.committed is False


def test_reconciliation_adapter_commits_baseline_planning_evidence_and_audit_atomically() -> None:
    subject = _subject()
    contract = ReconciliationContract(
        (_SIGNAL,),
        (),
        planning_evidence=_planning_evidence(),
    )
    registration = SubjectRegistered(ReconciliationSnapshot(subject, contract, 0, ()))
    transaction = _ShadowTransaction(registration, object())
    adapter = TransactionalReconciliationStore(_factory(transaction), FixedClock(_NOW), _POLICY)

    outcome = asyncio.run(adapter.register_subject(subject, contract))

    assert outcome == registration
    convergence = initial_convergence_state(_NOW, _POLICY)
    assert transaction.reconciliation.registration_calls == [(subject, contract, convergence)]
    assert transaction.reconciliation_pairs.registration_calls == [
        (subject, contract, convergence, _NOW)
    ]
    assert transaction.committed is True


def test_shadow_unit_of_work_does_not_expose_a_pair_owned_audit_sink() -> None:
    assert not hasattr(PostgresShadowReconciliationUnitOfWork, "pair_owned_audit_events")


def test_reconciliation_adapter_rolls_back_a_result_when_evidence_conflicts() -> None:
    subject = _subject()
    contract = ReconciliationContract((_SIGNAL,), ())
    claim = _claim(subject, contract)
    result = ReconciliationResult(subject.subject_id, "success", ())
    record = _shadow_record(subject)
    transaction = _ShadowTransaction(
        ResultRecorded(1, result),
        ShadowEvidenceConflict(record),
    )
    adapter = TransactionalReconciliationStore(_factory(transaction), FixedClock(_NOW), _POLICY)

    with pytest.raises(PersistenceInvariantViolation, match="retained reconciliation"):
        asyncio.run(adapter.record_result_with_evidence(claim, 1, result, (record,)))

    assert transaction.committed is False


def _subject() -> ReconciliationSubject:
    return ReconciliationSubject.create(
        installation_id=100,
        repository_id=200,
        event_name="push",
        ref="refs/heads/main",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=7001,
        run_attempt=1,
    )


def _claim(
    subject: ReconciliationSubject,
    contract: ReconciliationContract,
) -> ReconciliationAttemptClaim:
    acquired = acquire_reconciliation_claim(
        initial_convergence_state(_NOW, _POLICY),
        subject,
        contract,
        0,
        worker_id="a" * 64,
        now=_NOW,
        policy=_POLICY,
    )
    assert isinstance(acquired, ReconciliationClaimAcquired)
    return acquired.claim


def _shadow_record(subject: ReconciliationSubject) -> ShadowEvidenceRecord:
    candidate = ShadowCandidate(
        repo="example-org/ci-coordinator",
        event=subject.subject_id,
        base_sha=subject.base_sha,
        head_sha=subject.head_sha,
        config_epoch="epoch-1",
        policy_hash="policy-1",
        diff_hash="diff-1",
        graph_hash="graph-1",
        baseline_plan="full-ci-1",
        candidate_plan="candidate-1",
        surface="backend-tests",
        coverage_relation=CoverageRelation.COVERED,
        actual_full_ci_result=FullCiResult.PASSED,
    )
    observation = FullCiObservation(
        repo=candidate.repo,
        event=candidate.event,
        base_sha=candidate.base_sha,
        head_sha=candidate.head_sha,
        config_epoch=candidate.config_epoch,
        policy_hash=candidate.policy_hash,
        diff_hash=candidate.diff_hash,
        graph_hash=candidate.graph_hash,
        baseline_plan=candidate.baseline_plan,
        candidate_plan=candidate.candidate_plan,
        actual_full_ci_result=FullCiResult.PASSED,
    )
    return ShadowEvidenceRecord(
        "profile-1",
        datetime(2026, 7, 15, tzinfo=UTC),
        compare_full_ci(candidate, observation),
    )


def _planning_evidence() -> PlanningEvidenceContext:
    return PlanningEvidenceContext(
        request_hash="1" * 64,
        input_hash="2" * 64,
        config_epoch_id="3" * 64,
        repo_epoch_hash="4" * 64,
        diff_hash="5" * 64,
        policy_hash="6" * 64,
        graph_hash="7" * 64,
        validation_catalog_hash="8" * 64,
        deterministic_plan_id="candidate-plan-1",
        verified_plan_id="verified-plan-1",
        verified_plan_hash="9" * 64,
        planner_version="candidate-planning-core/v1",
        verifier_version="verification-core/v1",
        fallback_reason=None,
    )


def _factory(transaction: object) -> Callable[[], Any]:
    return cast(Callable[[], Any], lambda: transaction)


async def _invoke_config_store_operation(
    adapter: TransactionalConfigEpochStore,
    operation: _ConfigStoreOperation,
) -> object:
    if operation == "register":
        return await adapter.register(cast(ValidatedEpochDraft, object()))
    if operation == "register_operation":
        return await adapter.register_operation(cast(PreparedConfigEpochRegistration, object()))
    if operation == "load_active":
        return await adapter.load_active(RepositoryScope(1, 2))
    if operation == "load_epoch":
        return await adapter.load_epoch(RepositoryScope(1, 2), "a" * 64)
    if operation == "read_status":
        return await adapter.read_status(
            RepositoryScope(1, 2),
            after_epoch_id=None,
            limit=1,
        )
    if operation == "resolve_operation":
        return await adapter.resolve_operation(cast(ConfigEpochReplayCommand, object()))
    return await adapter.activate(cast(PreparedConfigEpochActivation, object()))
