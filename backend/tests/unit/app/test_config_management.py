from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import pytest
from config_epoch_support import CONFIG_SOURCE, admitted_config_epoch
from repository_activation_support import ACTOR, NOW, REVIEWER, SCOPE, review_record

from ci_coordinator.app import (
    ActivateConfigEpoch,
    ConfigManagementService,
    RegisterConfigEpoch,
    RollbackConfigEpoch,
)
from ci_coordinator.app.config_admission import (
    ConfigAdmissionService,
    PolicyAdmission,
    PolicyAdmissionUnavailable,
)
from ci_coordinator.app.config_management import (
    ConfigActivationOutcome,
    ConfigActivationState,
    ConfigEpochStore,
    ConfigRegistrationOutcome,
    ConfigRegistrationState,
)
from ci_coordinator.config_control import (
    PolicyAdmissionResult,
    PolicyDiagnostic,
    PolicySourceFormat,
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ActiveConfigEpochSnapshot,
    ConfigEpochActivationDuplicate,
    ConfigEpochActivationRecord,
    ConfigEpochActivationResult,
    ConfigEpochActivationRevisionConflict,
    ConfigEpochActivationTargetUnavailable,
    ConfigEpochOperationResolution,
    ConfigEpochRegistrationCommitted,
    ConfigEpochRegistrationCreated,
    ConfigEpochRegistrationOperationConflict,
    ConfigEpochRegistrationOperationResult,
    ConfigEpochRegistrationRecord,
    ConfigEpochRegistrationReplay,
    ConfigEpochReplayCommand,
    ConfigEpochStoreUnavailable,
    CoverageRelation,
    EpochCoverageComparator,
    PreparedConfigEpochActivation,
    PreparedConfigEpochRegistration,
)
from ci_coordinator.kernel import FixedClock
from ci_coordinator.proposal_review import (
    AttestedConfigActivationResult,
    RepositoryActivationAuthority,
    RepositoryActivationAuthorityConflict,
    RepositoryActivationAuthorization,
    RepositoryActivationGranted,
    RepositoryActivationRejected,
    RepositoryActivationUnavailable,
)

_AUTHORITY = RepositoryActivationAuthority(
    review_record(),
    REVIEWER,
    NOW + timedelta(seconds=1),
)
_ACTIVATION_TARGET = _AUTHORITY.review.target_epoch_id
_ACTIVATION_MANIFEST = _AUTHORITY.review.command.expected_manifest_id


class _Authorizer:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        return self.allowed and actor == ACTOR and scope == SCOPE


class _Store:
    def __init__(
        self,
        *,
        active: ActiveConfigEpochSnapshot | None = None,
        target: ValidatedEpochDraft | None = None,
        activation_result: ConfigEpochActivationResult | None = None,
        attested_result: AttestedConfigActivationResult | None = None,
        operation_resolution: ConfigEpochOperationResolution = None,
        registration_state: Literal["committed", "replay", "conflict"] = "committed",
    ) -> None:
        self.registered: ValidatedEpochDraft | None = None
        self.registration: PreparedConfigEpochRegistration | None = None
        self.prepared: PreparedConfigEpochActivation | None = None
        self.active = active
        self.target = target
        self.activation_result = (
            ConfigEpochActivationTargetUnavailable()
            if activation_result is None
            else activation_result
        )
        self.attested_result = (
            self.activation_result if attested_result is None else attested_result
        )
        self.operation_resolution = operation_resolution
        self.registration_state = registration_state
        self.calls: list[str] = []

    async def register(self, draft: ValidatedEpochDraft) -> ConfigEpochRegistrationCreated:
        self.calls.append("register")
        self.registered = draft
        return ConfigEpochRegistrationCreated(draft.epoch_id)

    async def register_operation(
        self,
        prepared: PreparedConfigEpochRegistration,
    ) -> ConfigEpochRegistrationOperationResult:
        self.calls.append("register_operation")
        self.registration = prepared
        self.registered = prepared.command.draft
        record = ConfigEpochRegistrationRecord(
            scope=prepared.command.draft.scope,
            operation_id=prepared.command.operation_id,
            epoch_id=prepared.command.draft.epoch_id,
            audit_event_id="audit_" + "1" * 32,
            audit_input_hash=prepared.audit_input_hash,
        )
        if self.registration_state == "replay":
            return ConfigEpochRegistrationReplay(record)
        if self.registration_state == "conflict":
            return ConfigEpochRegistrationOperationConflict(record)
        return ConfigEpochRegistrationCommitted(record)

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self.calls.append("load_active")
        assert scope == RepositoryScope(1, 2)
        return self.active

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None:
        self.calls.append("load_epoch")
        assert scope == RepositoryScope(1, 2)
        assert self.target is None or epoch_id == self.target.epoch_id
        return self.target

    async def resolve_operation(
        self,
        command: ConfigEpochReplayCommand,
    ) -> ConfigEpochOperationResolution:
        self.calls.append("resolve_operation")
        assert command.scope == RepositoryScope(1, 2)
        return self.operation_resolution

    async def activate(
        self,
        prepared: PreparedConfigEpochActivation,
    ) -> ConfigEpochActivationResult:
        self.calls.append("activate")
        self.prepared = prepared
        return self.activation_result

    async def activate_config(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RepositoryActivationAuthority,
    ) -> AttestedConfigActivationResult:
        self.calls.append("activate_config")
        assert authority == _AUTHORITY
        self.prepared = prepared
        return self.attested_result


class _UnavailableStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def register(self, draft: ValidatedEpochDraft) -> ConfigEpochRegistrationCreated:
        del draft
        self.calls.append("register")
        raise ConfigEpochStoreUnavailable

    async def register_operation(
        self,
        prepared: PreparedConfigEpochRegistration,
    ) -> ConfigEpochRegistrationOperationResult:
        del prepared
        self.calls.append("register_operation")
        raise ConfigEpochStoreUnavailable

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        del scope
        self.calls.append("load_active")
        raise ConfigEpochStoreUnavailable

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None:
        del scope, epoch_id
        self.calls.append("load_epoch")
        raise ConfigEpochStoreUnavailable

    async def resolve_operation(
        self,
        command: ConfigEpochReplayCommand,
    ) -> ConfigEpochOperationResolution:
        del command
        self.calls.append("resolve_operation")
        raise ConfigEpochStoreUnavailable

    async def activate(
        self,
        prepared: PreparedConfigEpochActivation,
    ) -> ConfigEpochActivationResult:
        del prepared
        self.calls.append("activate")
        raise ConfigEpochStoreUnavailable


class _Comparator(EpochCoverageComparator):
    def __init__(self, relation: CoverageRelation) -> None:
        self.relation = relation

    async def compare(
        self,
        *,
        scope: RepositoryScope,
        active_epoch_id: str,
        target_epoch_id: str,
    ) -> CoverageRelation:
        assert scope == RepositoryScope(1, 2)
        assert active_epoch_id != target_epoch_id
        return self.relation


class _ActivationAuthorizer:
    def __init__(self, result: RepositoryActivationAuthorization) -> None:
        self.result = result
        self.calls = 0

    async def authorize(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        target_epoch_id: str,
        proposal_manifest_id: str,
    ) -> RepositoryActivationAuthorization:
        assert actor == ACTOR
        assert scope == SCOPE
        assert target_epoch_id == _ACTIVATION_TARGET
        assert proposal_manifest_id == _ACTIVATION_MANIFEST
        self.calls += 1
        return self.result


def test_config_outcomes_reject_states_outside_their_closed_algebras() -> None:
    with pytest.raises(ValueError, match="unsupported config registration outcome"):
        ConfigRegistrationOutcome(cast(ConfigRegistrationState, "future_state"))
    with pytest.raises(ValueError, match="unsupported config activation outcome"):
        ConfigActivationOutcome(cast(ConfigActivationState, "future_state"))
    with pytest.raises(TypeError, match="exact policy diagnostics"):
        ConfigRegistrationOutcome(
            "invalid",
            diagnostics=cast(tuple[PolicyDiagnostic, ...], (object(),)),
        )


def test_registration_admits_authorizes_and_persists_an_immutable_epoch() -> None:
    store = _Store()
    service = _service(store, allowed=True)

    result = asyncio.run(
        service.register(RegisterConfigEpoch(ACTOR, CONFIG_SOURCE, "json", "register-1"))
    )

    assert result.state == "created"
    assert result.epoch_id is not None
    assert store.registered is not None
    assert store.registered.scope == RepositoryScope(1, 2)


@pytest.mark.parametrize(
    ("registration_state", "expected_state"),
    (("replay", "duplicate"), ("conflict", "conflict")),
)
def test_registration_projects_persistence_replay_algebra(
    registration_state: Literal["replay", "conflict"],
    expected_state: Literal["duplicate", "conflict"],
) -> None:
    store = _Store(registration_state=registration_state)

    result = asyncio.run(
        _service(store, allowed=True).register(
            RegisterConfigEpoch(ACTOR, CONFIG_SOURCE, "json", "register-1")
        )
    )

    assert store.registered is not None
    assert result.state == expected_state
    assert result.epoch_id == (store.registered.epoch_id if expected_state == "duplicate" else None)
    assert store.calls == ["register_operation"]


def test_invalid_and_forbidden_registration_never_reach_storage() -> None:
    invalid_store = _Store()
    invalid = asyncio.run(
        _service(invalid_store, allowed=True).register(
            RegisterConfigEpoch(ACTOR, b"{}", "json", "register-invalid")
        )
    )
    forbidden_store = _Store()
    forbidden = asyncio.run(
        _service(forbidden_store, allowed=False).register(
            RegisterConfigEpoch(ACTOR, CONFIG_SOURCE, "json", "register-forbidden")
        )
    )

    assert invalid.state == "invalid" and invalid.diagnostics
    assert forbidden.state == "forbidden"
    assert invalid_store.registered is None
    assert forbidden_store.registered is None


def test_unavailable_policy_admission_never_reaches_storage() -> None:
    async def unavailable(_: bytes, __: PolicySourceFormat) -> PolicyAdmissionResult:
        raise PolicyAdmissionUnavailable

    store = _Store()
    result = asyncio.run(
        _service(store, allowed=True, policy_admission=unavailable).register(
            RegisterConfigEpoch(ACTOR, CONFIG_SOURCE, "json", "register-unavailable")
        )
    )

    assert result.state == "unavailable"
    assert store.registered is None


def test_activation_is_scope_authorized_and_uses_the_service_clock() -> None:
    store = _Store()
    service = _service(store, allowed=True)
    result = asyncio.run(service.activate(_activation_command()))

    assert result.state == "target_unavailable"
    assert store.calls == ["resolve_operation", "activate_config"]
    assert isinstance(store.prepared, PreparedConfigEpochActivation)
    assert store.prepared.command.occurred_at == "2026-07-15T10:00:00.000Z"
    event = store.prepared._take_audit_event()
    assert json.loads(event.payload_canonical_bytes) == {
        "authorityEvidenceHash": _AUTHORITY.evidence_hash,
        "authorityObservedAt": "2026-09-02T10:00:01.000Z",
        "expectedRevision": None,
        "operationId": "activate-1",
        "proposalManifestId": _ACTIVATION_MANIFEST,
        "schemaVersion": "config-epoch-activation-audit/v1",
        "targetEpochId": _ACTIVATION_TARGET,
    }


def test_forbidden_activation_reaches_neither_replay_nor_repository_authority() -> None:
    store = _Store()
    activation_authorizer = _ActivationAuthorizer(RepositoryActivationGranted(_AUTHORITY))

    result = asyncio.run(
        _service(
            store,
            allowed=False,
            activation_authorizer=activation_authorizer,
        ).activate(_activation_command())
    )

    assert result.state == "forbidden"
    assert store.calls == []
    assert activation_authorizer.calls == 0


@pytest.mark.parametrize(
    ("authorization", "expected_state"),
    [
        (RepositoryActivationRejected(), "attestation_invalid"),
        (RepositoryActivationUnavailable(), "unavailable"),
    ],
    ids=("rejected", "unavailable"),
)
def test_activation_authority_failure_never_reaches_the_attested_store(
    authorization: RepositoryActivationAuthorization,
    expected_state: Literal["attestation_invalid", "unavailable"],
) -> None:
    store = _Store()
    result = asyncio.run(
        _service(store, allowed=True, activation_authorization=authorization).activate(
            _activation_command()
        )
    )

    assert result.state == expected_state
    assert store.calls == ["resolve_operation"]
    assert store.prepared is None


def test_exact_activation_replay_precedes_repository_reauthorization() -> None:
    duplicate = _activation_duplicate()
    store = _Store(operation_resolution=duplicate)
    activation_authorizer = _ActivationAuthorizer(RepositoryActivationRejected())

    result = asyncio.run(
        _service(
            store,
            allowed=True,
            activation_authorizer=activation_authorizer,
        ).activate(_activation_command())
    )

    assert result.state == "duplicate"
    assert store.calls == ["resolve_operation"]
    assert activation_authorizer.calls == 0


def test_lock_time_authority_conflict_is_not_reported_as_activation() -> None:
    store = _Store(attested_result=RepositoryActivationAuthorityConflict())

    result = asyncio.run(_service(store, allowed=True).activate(_activation_command()))

    assert result.state == "attestation_invalid"
    assert store.calls == ["resolve_operation", "activate_config"]


def test_alternate_store_reports_app_owned_unavailability() -> None:
    store = _UnavailableStore()
    service = _service(store, allowed=True)

    registration = asyncio.run(
        service.register(RegisterConfigEpoch(ACTOR, CONFIG_SOURCE, "json", "register-unavailable"))
    )
    activation = asyncio.run(service.activate(_activation_command()))
    rollback = asyncio.run(service.rollback(_rollback_command()))

    assert registration.state == "unavailable"
    assert activation.state == "unavailable"
    assert rollback.state == "unavailable"
    assert store.calls == ["register_operation", "resolve_operation", "resolve_operation"]


def test_rollback_uses_public_admission_and_binds_governed_audit_evidence() -> None:
    store = _rollback_store()
    result = asyncio.run(_service(store, allowed=True).rollback(_rollback_command()))

    assert result.state == "revision_conflict"
    assert store.calls == ["resolve_operation", "load_active", "load_epoch", "activate"]
    assert isinstance(store.prepared, PreparedConfigEpochActivation)
    assert store.prepared.command.scope == RepositoryScope(1, 2)
    assert store.target is not None
    assert store.active is not None
    assert store.prepared.command.target_epoch_id == store.target.epoch_id
    assert store.prepared.command.expected_revision == 7
    assert store.prepared.command.operation_id == "rollback-1"
    assert store.prepared.command.actor == ACTOR
    assert store.prepared.command.mutation_kind == "rollback"
    assert store.prepared.command.expected_active_epoch_id == store.active.active.epoch_id
    assert store.prepared.command.coverage_relation == "equal"
    event = store.prepared._take_audit_event()
    assert event.event_type == "config-epoch-rollback/v1"
    assert json.loads(event.payload_canonical_bytes) == {
        "activeEpochId": store.active.active.epoch_id,
        "coverageRelation": "equal",
        "expectedRevision": 7,
        "operationId": "rollback-1",
        "reason": "restore conservative validation",
        "schemaVersion": "config-epoch-rollback-audit/v1",
        "targetEpochId": store.target.epoch_id,
    }


def test_forbidden_rollback_never_reaches_the_activation_store() -> None:
    store = _Store()

    result = asyncio.run(
        _service(store, allowed=False).rollback(
            RollbackConfigEpoch(
                actor=ACTOR,
                scope=RepositoryScope(1, 2),
                target_epoch_id="b" * 64,
                expected_revision=7,
                operation_id="rollback-1",
                reason="restore conservative validation",
            )
        )
    )

    assert result.state == "forbidden"
    assert store.prepared is None
    assert store.calls == []


def test_exact_rollback_replay_short_circuits_live_state_and_coverage_reads() -> None:
    duplicate = _rollback_duplicate()
    store = _Store(operation_resolution=duplicate)

    result = asyncio.run(_service(store, allowed=True).rollback(_rollback_command()))

    assert result.state == "duplicate"
    assert result.epoch_id == duplicate.record.active.epoch_id
    assert result.revision == duplicate.record.active.revision
    assert store.calls == ["resolve_operation"]


def test_rollback_requires_an_active_epoch_and_same_scope_admitted_target() -> None:
    no_active_store = _Store()
    target_missing_store = _rollback_store(target_available=False)

    no_active = asyncio.run(_service(no_active_store, allowed=True).rollback(_rollback_command()))
    target_missing = asyncio.run(
        _service(target_missing_store, allowed=True).rollback(_rollback_command())
    )

    assert no_active.state == "revision_conflict"
    assert no_active_store.calls == ["resolve_operation", "load_active"]
    assert target_missing.state == "target_unavailable"
    assert target_missing_store.calls == ["resolve_operation", "load_active", "load_epoch"]
    assert target_missing_store.prepared is None


@pytest.mark.parametrize(
    ("relation", "state"),
    [
        ("less", "coverage_reducing"),
        ("incomparable", "coverage_unproven"),
        ("unknown", "coverage_unproven"),
        ("unavailable", "unavailable"),
    ],
)
def test_rollback_rejects_every_relation_without_a_positive_proof(
    relation: CoverageRelation,
    state: Literal["coverage_reducing", "coverage_unproven", "unavailable"],
) -> None:
    store = _rollback_store()

    result = asyncio.run(
        _service(store, allowed=True, comparator=_Comparator(relation)).rollback(
            _rollback_command()
        )
    )

    assert result.state == state
    assert store.calls == ["resolve_operation", "load_active", "load_epoch"]
    assert store.prepared is None


def test_rollback_cas_rejects_an_active_epoch_change_after_comparison() -> None:
    active, target = _rollback_epochs()
    changed = ActiveConfigEpoch(active.active.scope, target.epoch_id, active.active.revision + 1)
    store = _Store(
        active=active,
        target=target,
        activation_result=ConfigEpochActivationRevisionConflict(changed),
    )

    result = asyncio.run(_service(store, allowed=True).rollback(_rollback_command()))

    assert result.state == "revision_conflict"
    assert isinstance(store.prepared, PreparedConfigEpochActivation)
    assert store.prepared.command.expected_revision == 7
    assert store.prepared.command.expected_active_epoch_id == active.active.epoch_id


def _service(
    store: ConfigEpochStore,
    *,
    allowed: bool,
    comparator: EpochCoverageComparator | None = None,
    policy_admission: PolicyAdmission | None = None,
    activation_authorization: RepositoryActivationAuthorization | None = None,
    activation_authorizer: _ActivationAuthorizer | None = None,
) -> ConfigManagementService:
    resolved_authorizer = activation_authorizer or _ActivationAuthorizer(
        activation_authorization or RepositoryActivationGranted(_AUTHORITY)
    )
    scope_authorizer = _Authorizer(allowed)
    return ConfigManagementService(
        authorizer=scope_authorizer,
        admission=ConfigAdmissionService(
            authorizer=scope_authorizer,
            policy_admission=policy_admission or _admit_policy,
        ),
        store=store,
        clock=FixedClock(datetime(2026, 7, 15, 10, tzinfo=UTC)),
        activation_authorizer=resolved_authorizer,
        attested_activation_store=store,  # type: ignore[arg-type]
        rollback_comparator=comparator,
    )


async def _admit_policy(
    source: bytes,
    source_format: PolicySourceFormat,
) -> PolicyAdmissionResult:
    return admit_policy_document(source, source_format)


def _rollback_command() -> RollbackConfigEpoch:
    _, target = _rollback_epochs()
    return RollbackConfigEpoch(
        actor=ACTOR,
        scope=RepositoryScope(1, 2),
        target_epoch_id=target.epoch_id,
        expected_revision=7,
        operation_id="rollback-1",
        reason="restore conservative validation",
    )


def _activation_command() -> ActivateConfigEpoch:
    return ActivateConfigEpoch(
        actor=ACTOR,
        scope=SCOPE,
        target_epoch_id=_ACTIVATION_TARGET,
        proposal_manifest_id=_ACTIVATION_MANIFEST,
        expected_revision=None,
        operation_id="activate-1",
    )


def _activation_duplicate() -> ConfigEpochActivationDuplicate:
    return ConfigEpochActivationDuplicate(
        ConfigEpochActivationRecord(
            scope=SCOPE,
            operation_id="activate-1",
            expected_revision=None,
            previous=None,
            active=ActiveConfigEpoch(SCOPE, _ACTIVATION_TARGET, 1),
            audit_event_id="audit_" + "7" * 32,
            audit_input_hash="8" * 64,
        )
    )


def _rollback_store(*, target_available: bool = True) -> _Store:
    active, target = _rollback_epochs()
    return _Store(
        active=active,
        target=target if target_available else None,
        activation_result=ConfigEpochActivationRevisionConflict(active.active),
    )


def _rollback_duplicate() -> ConfigEpochActivationDuplicate:
    previous, target = _rollback_epochs()
    return ConfigEpochActivationDuplicate(
        ConfigEpochActivationRecord(
            scope=previous.active.scope,
            operation_id="rollback-1",
            expected_revision=previous.active.revision,
            previous=previous.active,
            active=ActiveConfigEpoch(
                scope=previous.active.scope,
                epoch_id=target.epoch_id,
                revision=previous.active.revision + 1,
            ),
            audit_event_id="audit_" + "c" * 32,
            audit_input_hash="d" * 64,
        )
    )


def _rollback_epochs() -> tuple[ActiveConfigEpochSnapshot, ValidatedEpochDraft]:
    active_draft = admitted_config_epoch("json")
    target = admitted_config_epoch("yaml-1.2")
    active = ActiveConfigEpochSnapshot(
        ActiveConfigEpoch(active_draft.scope, active_draft.epoch_id, 7),
        active_draft,
    )
    assert active_draft.epoch_id != target.epoch_id
    assert active_draft.compiled_policy_bytes == target.compiled_policy_bytes
    return active, target
