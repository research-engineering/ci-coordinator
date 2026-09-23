"""Application service for revision-safe repository policy hot reload."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ci_coordinator.app.config_admission import (
    ConfigAdmissionAccepted,
    ConfigAdmissionForbidden,
    ConfigAdmissionInvalid,
    ConfigAdmissionUnavailable,
    ConfigAdmissionUseCase,
    ConfigScopeAuthorizer,
)
from ci_coordinator.config_control import (
    PolicyDiagnostic,
    PolicySourceFormat,
    RepositoryScope,
    ValidatedEpochDraft,
)
from ci_coordinator.config_epochs import (
    ActiveConfigEpochSnapshot,
    ConfigEpochActivationApplied,
    ConfigEpochActivationCommand,
    ConfigEpochActivationDuplicate,
    ConfigEpochActivationOperationConflict,
    ConfigEpochActivationResult,
    ConfigEpochActivationRevisionConflict,
    ConfigEpochActivationTargetUnavailable,
    ConfigEpochOperationResolution,
    ConfigEpochRegistrationCommand,
    ConfigEpochRegistrationCommitted,
    ConfigEpochRegistrationOperationConflict,
    ConfigEpochRegistrationOperationResult,
    ConfigEpochRegistrationReplay,
    ConfigEpochReplayCommand,
    ConfigEpochStoreUnavailable,
    EpochCoverageComparator,
    PreparedConfigEpochActivation,
    PreparedConfigEpochRegistration,
    RollbackAllowed,
    RollbackCommand,
    RollbackRejected,
    admit_rollback,
    prepare_config_epoch_activation,
    prepare_config_epoch_registration,
    validate_rollback_reason,
)
from ci_coordinator.kernel import Clock
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.planning_core import ConservativeEpochCoverageComparator
from ci_coordinator.proposal_review import (
    RepositoryActivationAuthorityConflict,
    RepositoryActivationAuthorization,
    RepositoryActivationGranted,
    RepositoryActivationRejected,
    RepositoryActivationStore,
    RepositoryActivationUnavailable,
)


@dataclass(frozen=True, slots=True)
class RegisterConfigEpoch:
    actor: str
    source: bytes
    source_format: PolicySourceFormat
    operation_id: str


@dataclass(frozen=True, slots=True)
class ActivateConfigEpoch:
    actor: str
    scope: RepositoryScope
    target_epoch_id: str
    proposal_manifest_id: str
    expected_revision: int | None
    operation_id: str


@dataclass(frozen=True, slots=True)
class RollbackConfigEpoch:
    actor: str
    scope: RepositoryScope
    target_epoch_id: str
    expected_revision: int
    operation_id: str
    reason: str

    def __post_init__(self) -> None:
        validate_rollback_reason(self.reason)


type ConfigRegistrationState = Literal[
    "created", "duplicate", "invalid", "forbidden", "conflict", "unavailable"
]
_CONFIG_REGISTRATION_STATES: Final[frozenset[str]] = frozenset(
    {"created", "duplicate", "invalid", "forbidden", "conflict", "unavailable"}
)


@dataclass(frozen=True, slots=True)
class ConfigRegistrationOutcome:
    state: ConfigRegistrationState
    epoch_id: str | None = None
    diagnostics: tuple[PolicyDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if type(self.state) is not str or self.state not in _CONFIG_REGISTRATION_STATES:
            raise ValueError("unsupported config registration outcome")
        accepted = self.state in {"created", "duplicate"}
        if accepted != (self.epoch_id is not None):
            raise ValueError("accepted registration outcome requires exactly one epoch id")
        if type(self.diagnostics) is not tuple or any(
            type(diagnostic) is not PolicyDiagnostic for diagnostic in self.diagnostics
        ):
            raise TypeError("config registration diagnostics must be exact policy diagnostics")
        if (self.state == "invalid") != bool(self.diagnostics):
            raise ValueError("only invalid registration outcomes carry diagnostics")


type ConfigActivationState = Literal[
    "applied",
    "duplicate",
    "forbidden",
    "revision_conflict",
    "target_unavailable",
    "operation_conflict",
    "attestation_invalid",
    "coverage_reducing",
    "coverage_unproven",
    "unavailable",
]
_CONFIG_ACTIVATION_STATES: Final[frozenset[str]] = frozenset(
    {
        "applied",
        "duplicate",
        "forbidden",
        "revision_conflict",
        "target_unavailable",
        "operation_conflict",
        "attestation_invalid",
        "coverage_reducing",
        "coverage_unproven",
        "unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class ConfigActivationOutcome:
    state: ConfigActivationState
    epoch_id: str | None = None
    revision: int | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not str or self.state not in _CONFIG_ACTIVATION_STATES:
            raise ValueError("unsupported config activation outcome")
        accepted = self.state in {"applied", "duplicate"}
        if accepted != (self.epoch_id is not None and self.revision is not None):
            raise ValueError("accepted activation outcome requires epoch id and revision")


class ConfigManagementUseCase(Protocol):
    async def register(self, command: RegisterConfigEpoch) -> ConfigRegistrationOutcome: ...

    async def activate(self, command: ActivateConfigEpoch) -> ConfigActivationOutcome: ...

    async def rollback(self, command: RollbackConfigEpoch) -> ConfigActivationOutcome: ...


class RepositoryActivationAuthorizer(Protocol):
    async def authorize(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        target_epoch_id: str,
        proposal_manifest_id: str,
    ) -> RepositoryActivationAuthorization: ...


class ConfigEpochStore(Protocol):
    """Minimum config-epoch capability required by mutation orchestration."""

    async def register_operation(
        self,
        prepared: PreparedConfigEpochRegistration,
    ) -> ConfigEpochRegistrationOperationResult: ...

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None: ...

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None: ...

    async def resolve_operation(
        self,
        command: ConfigEpochReplayCommand,
    ) -> ConfigEpochOperationResolution: ...

    async def activate(
        self, prepared: PreparedConfigEpochActivation
    ) -> ConfigEpochActivationResult: ...


class ConfigManagementService:
    """Admit, authorize, persist, and atomically activate immutable policies."""

    def __init__(
        self,
        *,
        authorizer: ConfigScopeAuthorizer,
        admission: ConfigAdmissionUseCase,
        store: ConfigEpochStore,
        clock: Clock,
        activation_authorizer: RepositoryActivationAuthorizer,
        attested_activation_store: RepositoryActivationStore,
        runtime_metrics: RuntimeMetrics | None = None,
        rollback_comparator: EpochCoverageComparator | None = None,
    ) -> None:
        self._authorizer = authorizer
        self._admission = admission
        self._store = store
        self._clock = clock
        self._activation_authorizer = activation_authorizer
        self._attested_activation_store = attested_activation_store
        self._runtime_metrics = runtime_metrics
        self._rollback_comparator = rollback_comparator

    async def register(self, command: RegisterConfigEpoch) -> ConfigRegistrationOutcome:
        admitted = await self._admission.admit(
            actor=command.actor,
            source=command.source,
            source_format=command.source_format,
        )
        if isinstance(admitted, ConfigAdmissionUnavailable):
            return ConfigRegistrationOutcome("unavailable")
        if isinstance(admitted, ConfigAdmissionInvalid):
            return ConfigRegistrationOutcome("invalid", diagnostics=admitted.diagnostics)
        if isinstance(admitted, ConfigAdmissionForbidden):
            return ConfigRegistrationOutcome("forbidden")
        if not isinstance(admitted, ConfigAdmissionAccepted):
            raise RuntimeError("config admission result algebra is incomplete")
        occurred_at = self._clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prepared = prepare_config_epoch_registration(
            ConfigEpochRegistrationCommand(
                draft=admitted.draft,
                operation_id=command.operation_id,
                actor=command.actor,
                occurred_at=occurred_at,
            )
        )
        try:
            result = await self._store.register_operation(prepared)
        except ConfigEpochStoreUnavailable:
            return ConfigRegistrationOutcome("unavailable")
        if isinstance(result, ConfigEpochRegistrationCommitted):
            return ConfigRegistrationOutcome("created", epoch_id=result.record.epoch_id)
        if isinstance(result, ConfigEpochRegistrationReplay):
            return ConfigRegistrationOutcome("duplicate", epoch_id=result.record.epoch_id)
        if isinstance(result, ConfigEpochRegistrationOperationConflict):
            return ConfigRegistrationOutcome("conflict")
        raise RuntimeError("config registration result algebra is incomplete")

    async def activate(self, command: ActivateConfigEpoch) -> ConfigActivationOutcome:
        if not await self._authorizer.allows_scope(actor=command.actor, scope=command.scope):
            return self._observe_activation(ConfigActivationOutcome("forbidden"))
        try:
            replay = await self._store.resolve_operation(
                ConfigEpochReplayCommand(
                    scope=command.scope,
                    target_epoch_id=command.target_epoch_id,
                    expected_revision=command.expected_revision,
                    operation_id=command.operation_id,
                    actor=command.actor,
                    proposal_manifest_id=command.proposal_manifest_id,
                )
            )
        except ConfigEpochStoreUnavailable:
            return self._observe_activation(ConfigActivationOutcome("unavailable"))
        if replay is not None:
            return self._activation_outcome(replay)
        authorization = await self._activation_authorizer.authorize(
            actor=command.actor,
            scope=command.scope,
            target_epoch_id=command.target_epoch_id,
            proposal_manifest_id=command.proposal_manifest_id,
        )
        if isinstance(authorization, RepositoryActivationRejected):
            return self._observe_activation(ConfigActivationOutcome("attestation_invalid"))
        if isinstance(authorization, RepositoryActivationUnavailable):
            return self._observe_activation(ConfigActivationOutcome("unavailable"))
        if not isinstance(authorization, RepositoryActivationGranted):
            raise RuntimeError("repository activation authorization algebra is incomplete")
        authority = authorization.authority
        occurred_at = self._clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prepared = prepare_config_epoch_activation(
            ConfigEpochActivationCommand(
                scope=command.scope,
                target_epoch_id=command.target_epoch_id,
                expected_revision=command.expected_revision,
                operation_id=command.operation_id,
                actor=command.actor,
                occurred_at=occurred_at,
                proposal_manifest_id=command.proposal_manifest_id,
                authority_evidence_hash=authority.evidence_hash,
                authority_observed_at=(
                    authority.rechecked_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                ),
            )
        )
        try:
            result = await self._attested_activation_store.activate_config(prepared, authority)
        except ConfigEpochStoreUnavailable:
            return self._observe_activation(ConfigActivationOutcome("unavailable"))
        if isinstance(result, RepositoryActivationAuthorityConflict):
            return self._observe_activation(ConfigActivationOutcome("attestation_invalid"))
        return self._activation_outcome(result)

    async def rollback(self, command: RollbackConfigEpoch) -> ConfigActivationOutcome:
        """Reactivate only an admitted epoch whose coverage is proved non-reducing."""
        if not await self._authorizer.allows_scope(actor=command.actor, scope=command.scope):
            return self._observe_activation(ConfigActivationOutcome("forbidden"))
        try:
            replay = await self._store.resolve_operation(
                ConfigEpochReplayCommand(
                    scope=command.scope,
                    target_epoch_id=command.target_epoch_id,
                    expected_revision=command.expected_revision,
                    operation_id=command.operation_id,
                    actor=command.actor,
                    mutation_kind="rollback",
                    reason=command.reason,
                )
            )
            if replay is not None:
                return self._activation_outcome(replay)
            active = await self._store.load_active(command.scope)
            if active is None:
                return self._observe_activation(ConfigActivationOutcome("revision_conflict"))
            target = await self._store.load_epoch(command.scope, command.target_epoch_id)
            if (
                target is None
                or target.scope != command.scope
                or target.epoch_id != command.target_epoch_id
            ):
                return self._observe_activation(ConfigActivationOutcome("target_unavailable"))
            comparator = self._rollback_comparator or ConservativeEpochCoverageComparator(
                active.draft,
                target,
            )
            admission = await admit_rollback(
                RollbackCommand(
                    scope=command.scope,
                    active_epoch_id=active.active.epoch_id,
                    target_epoch_id=target.epoch_id,
                    actor=command.actor,
                    reason=command.reason,
                ),
                comparator,
            )
        except ConfigEpochStoreUnavailable:
            return self._observe_activation(ConfigActivationOutcome("unavailable"))

        if isinstance(admission, RollbackRejected):
            if admission.code == "coverage_reducing":
                state: ConfigActivationState = "coverage_reducing"
            elif admission.code == "coverage_unavailable":
                state = "unavailable"
            else:
                state = "coverage_unproven"
            return self._observe_activation(ConfigActivationOutcome(state))
        if not isinstance(admission, RollbackAllowed):
            raise RuntimeError("rollback admission algebra is incomplete")

        occurred_at = self._clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prepared = prepare_config_epoch_activation(
            ConfigEpochActivationCommand(
                scope=command.scope,
                target_epoch_id=target.epoch_id,
                expected_revision=command.expected_revision,
                operation_id=command.operation_id,
                actor=command.actor,
                occurred_at=occurred_at,
                mutation_kind="rollback",
                expected_active_epoch_id=active.active.epoch_id,
                coverage_relation=admission.relation,
                reason=command.reason,
            )
        )
        try:
            result = await self._store.activate(prepared)
        except ConfigEpochStoreUnavailable:
            return self._observe_activation(ConfigActivationOutcome("unavailable"))
        return self._activation_outcome(result)

    def _activation_outcome(
        self,
        result: ConfigEpochActivationResult,
    ) -> ConfigActivationOutcome:
        if isinstance(result, ConfigEpochActivationApplied | ConfigEpochActivationDuplicate):
            return self._observe_activation(
                ConfigActivationOutcome(
                    "applied" if isinstance(result, ConfigEpochActivationApplied) else "duplicate",
                    epoch_id=result.record.active.epoch_id,
                    revision=result.record.active.revision,
                )
            )
        if isinstance(result, ConfigEpochActivationRevisionConflict):
            return self._observe_activation(ConfigActivationOutcome("revision_conflict"))
        if isinstance(result, ConfigEpochActivationTargetUnavailable):
            return self._observe_activation(ConfigActivationOutcome("target_unavailable"))
        if isinstance(result, ConfigEpochActivationOperationConflict):
            return self._observe_activation(ConfigActivationOutcome("operation_conflict"))
        raise RuntimeError("config activation result algebra is incomplete")

    def _observe_activation(self, outcome: ConfigActivationOutcome) -> ConfigActivationOutcome:
        if self._runtime_metrics is not None:
            self._runtime_metrics.config_activation(outcome.state)
        return outcome
