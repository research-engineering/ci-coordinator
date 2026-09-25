"""Transaction-scoped adapters for long-lived runtime ports."""

from __future__ import annotations

from collections.abc import Callable

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import (
    ActiveConfigEpochSnapshot,
    ConfigEpochActivationResult,
    ConfigEpochOperationResolution,
    ConfigEpochRegistrationOperationResult,
    ConfigEpochRegistrationResult,
    ConfigEpochReplayCommand,
    ConfigEpochStatus,
    ConfigEpochStoreUnavailable,
    PreparedConfigEpochActivation,
    PreparedConfigEpochRegistration,
)
from ci_coordinator.github_ingestion import (
    DeliveryIdempotencyResult,
    DeliveryStoreUnavailable,
    DurableWorkflowObservation,
    PreparedDeliveryClaim,
)
from ci_coordinator.kernel import Clock
from ci_coordinator.persistence.config_epoch_unit_of_work import PostgresConfigEpochUnitOfWork
from ci_coordinator.persistence.errors import PersistenceError, PersistenceInvariantViolation
from ci_coordinator.persistence.runtime_state_unit_of_work import (
    PostgresIngressIssuanceUnitOfWork,
)
from ci_coordinator.persistence.shadow_reconciliation_unit_of_work import (
    PostgresShadowReconciliationUnitOfWork,
)
from ci_coordinator.persistence.webhook_ingestion_unit_of_work import (
    PostgresWebhookIngestionUnitOfWork,
)
from ci_coordinator.plan_issuance import (
    IssuanceGuardRejected,
    IssuanceSaveResult,
    IssuanceStoreUnavailable,
    IssuedPlanRecord,
)
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.reconciliation import (
    ObservationAppend,
    ReconciliationAttemptClaim,
    ReconciliationClaimLost,
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationConvergenceState,
    ReconciliationPersistence,
    ReconciliationResult,
    ReconciliationSnapshot,
    ReconciliationSubject,
    ReconciliationTerminalRequired,
    ResultDuplicate,
    ResultRecord,
    ResultRecorded,
    SubjectRegistration,
    initial_convergence_state,
)
from ci_coordinator.reconciliation.observation import SignalObservation
from ci_coordinator.shadow_mode import (
    ShadowEvidenceConflict,
    ShadowEvidenceRecord,
    ShadowEvidenceStored,
    ShadowEvidenceWrite,
)

type ConfigEpochUnitOfWorkFactory = Callable[[], PostgresConfigEpochUnitOfWork]
type IngressUnitOfWorkFactory = Callable[[], PostgresIngressIssuanceUnitOfWork]
type WebhookUnitOfWorkFactory = Callable[[], PostgresWebhookIngestionUnitOfWork]
type ShadowUnitOfWorkFactory = Callable[[], PostgresShadowReconciliationUnitOfWork]


class TransactionalConfigEpochResolver:
    """Resolve one active epoch from one compatibility-admitted snapshot."""

    def __init__(self, unit_of_work: ConfigEpochUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        async with self._unit_of_work() as transaction:
            return await transaction.config_epochs.load_active(scope)


class TransactionalConfigEpochStore:
    """Give config registration and activation one transaction per command."""

    def __init__(self, unit_of_work: ConfigEpochUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def register(self, draft: ValidatedEpochDraft) -> ConfigEpochRegistrationResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.config_epochs.register(draft)
                await transaction.commit()
                return result
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable("config epoch store is unavailable") from error

    async def register_operation(
        self,
        prepared: PreparedConfigEpochRegistration,
    ) -> ConfigEpochRegistrationOperationResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.config_epochs.register_operation(prepared)
                await transaction.commit()
                return result
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable("config epoch store is unavailable") from error

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.config_epochs.load_active(scope)
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable("config epoch store is unavailable") from error

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.config_epochs.load_epoch(scope, epoch_id)
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable("config epoch store is unavailable") from error

    async def read_status(
        self,
        scope: RepositoryScope,
        *,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigEpochStatus:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.config_epochs.read_status(
                    scope,
                    after_epoch_id=after_epoch_id,
                    limit=limit,
                )
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable("config epoch store is unavailable") from error

    async def resolve_operation(
        self,
        command: ConfigEpochReplayCommand,
    ) -> ConfigEpochOperationResolution:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.config_epochs.resolve_operation(command)
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable("config epoch store is unavailable") from error

    async def activate(
        self,
        prepared: PreparedConfigEpochActivation,
    ) -> ConfigEpochActivationResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.config_epochs.activate(prepared)
                await transaction.commit()
                return result
        except PersistenceError as error:
            raise ConfigEpochStoreUnavailable("config epoch store is unavailable") from error


class TransactionalWebhookIngestionStore:
    """Atomically commit a delivery claim and its required workflow fact."""

    def __init__(self, unit_of_work: WebhookUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def commit(
        self,
        key: PreparedDeliveryClaim,
        observation: DurableWorkflowObservation | None,
    ) -> DeliveryIdempotencyResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.webhook_ingestion.commit(key, observation)
                if isinstance(result, DeliveryStoreUnavailable):
                    return result
                await transaction.commit()
                return result
        except PersistenceError:
            return DeliveryStoreUnavailable()


class TransactionalIssuanceStore:
    """Persist one immutable signed envelope in its own transaction."""

    def __init__(self, unit_of_work: IngressUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def save(
        self,
        attempted: IssuedPlanRecord,
        guard: ProductionIssuanceGuard | None = None,
    ) -> IssuanceSaveResult:
        try:
            async with self._unit_of_work() as transaction:
                existing = await transaction.issuance.save(attempted, guard)
                if isinstance(existing, IssuanceGuardRejected):
                    await transaction.rollback()
                else:
                    await transaction.commit()
                return existing
        except PersistenceError as error:
            raise IssuanceStoreUnavailable("issued plan persistence is unavailable") from error


class TransactionalShadowEvidenceStore:
    """Persist or read shadow evidence without leaking a transaction lifetime."""

    def __init__(self, unit_of_work: ShadowUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def record(self, record: ShadowEvidenceRecord) -> ShadowEvidenceWrite:
        async with self._unit_of_work() as transaction:
            result = await transaction.shadow_evidence.record(record)
            await transaction.commit()
            return result

    async def list_records(self, profile_id: str) -> tuple[ShadowEvidenceRecord, ...]:
        async with self._unit_of_work() as transaction:
            return await transaction.shadow_evidence.list_records(profile_id)


class TransactionalReconciliationStore(ReconciliationPersistence):
    """Expose reconciliation CAS operations as isolated durable transactions."""

    def __init__(
        self,
        unit_of_work: ShadowUnitOfWorkFactory,
        clock: Clock,
        convergence_policy: ReconciliationConvergencePolicy,
    ) -> None:
        if type(convergence_policy) is not ReconciliationConvergencePolicy:
            raise TypeError("reconciliation convergence policy must be exact")
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._convergence_policy = convergence_policy

    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
    ) -> SubjectRegistration:
        occurred_at = self._clock.now()
        async with self._unit_of_work() as transaction:
            result = await transaction.reconciliation_pairs.register_subject(
                subject,
                contract,
                initial_convergence_state(occurred_at, self._convergence_policy),
                occurred_at=occurred_at,
            )
            await transaction.commit()
            return result

    async def load_snapshot(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> ReconciliationSnapshot | ReconciliationClaimLost:
        async with self._unit_of_work() as transaction:
            return await transaction.reconciliation.load_snapshot(claim)

    async def append_observation(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        observation: SignalObservation,
    ) -> ObservationAppend | ReconciliationClaimLost:
        async with self._unit_of_work() as transaction:
            result = await transaction.reconciliation.append_observation(
                claim,
                expected_revision,
                observation,
            )
            await transaction.commit()
            return result

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
    ) -> ResultRecord | ReconciliationClaimLost:
        occurred_at = self._clock.now()
        async with self._unit_of_work() as transaction:
            recorded = await transaction.reconciliation_pairs.record_result(
                claim,
                expected_revision,
                result,
                occurred_at=occurred_at,
            )
            if isinstance(recorded, ReconciliationClaimLost):
                return recorded
            await transaction.commit()
            return recorded

    async def record_result_with_evidence(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
        evidence: tuple[ShadowEvidenceRecord, ...],
    ) -> ResultRecord | ReconciliationClaimLost:
        if type(evidence) is not tuple or any(
            type(record) is not ShadowEvidenceRecord for record in evidence
        ):
            raise TypeError("shadow evidence must be an exact tuple of records")
        if any(record.key.event != claim.subject.subject_id for record in evidence):
            raise ValueError("shadow evidence belongs to another reconciliation subject")
        occurred_at = self._clock.now()
        async with self._unit_of_work() as transaction:
            recorded = await transaction.reconciliation_pairs.record_result(
                claim,
                expected_revision,
                result,
                occurred_at=occurred_at,
            )
            if isinstance(recorded, ReconciliationClaimLost):
                return recorded
            if isinstance(recorded, ResultRecorded | ResultDuplicate):
                adds_evidence = False
                for record in evidence:
                    outcome = await transaction.shadow_evidence.record(record)
                    if isinstance(outcome, ShadowEvidenceConflict):
                        raise PersistenceInvariantViolation(
                            "shadow evidence conflicts with retained reconciliation semantics"
                        )
                    adds_evidence |= isinstance(outcome, ShadowEvidenceStored)
                if isinstance(recorded, ResultDuplicate) and adds_evidence:
                    return ReconciliationClaimLost(claim.subject.subject_id)
            await transaction.commit()
            return recorded

    async def defer_claim(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> ReconciliationConvergenceState | ReconciliationClaimLost | ReconciliationTerminalRequired:
        async with self._unit_of_work() as transaction:
            deferred = await transaction.reconciliation.defer_claim(claim)
            if isinstance(deferred, ReconciliationTerminalRequired):
                return deferred
            await transaction.commit()
            return deferred


class TransactionalReconciliationClaimSource:
    """Acquire one due subject through a short compatibility-admitted transaction."""

    def __init__(
        self,
        unit_of_work: ShadowUnitOfWorkFactory,
        *,
        worker_id: str,
        convergence_policy: ReconciliationConvergencePolicy,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._worker_id = worker_id
        self._convergence_policy = convergence_policy

    async def claim_next(self) -> ReconciliationAttemptClaim | None:
        async with self._unit_of_work() as transaction:
            claim = await transaction.reconciliation.claim_next(
                worker_id=self._worker_id,
                policy=self._convergence_policy,
            )
            if claim is not None:
                await transaction.commit()
            return claim
