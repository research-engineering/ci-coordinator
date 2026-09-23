"""Authorized production cutover joins replay, live observation and atomic effects."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol

from ci_coordinator.app.config_admission import ConfigScopeAuthorizer
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ConfigEpochRepository, ConfigEpochStoreUnavailable
from ci_coordinator.kernel import hash_object
from ci_coordinator.production_admission import ProductionAdmissionGrant
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverCommand,
    ProductionCutoverRejected,
    ProductionCutoverResult,
    production_stage_input_digest,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.ports import (
    ProductionActivationObservation,
    ProductionCutoverStore,
    ProductionCutoverUnavailable,
    ProductionDrainVerifier,
    ProductionEvidenceAdmission,
    ProductionEvidenceAdmissionUnavailable,
    ProductionReceiptVerifier,
)


@dataclass(frozen=True, slots=True)
class ProductionAdministrationError:
    reason: Literal["forbidden", "invalid_evidence", "not_found", "unavailable"]


type ProductionAdministrationResult = ProductionCutoverResult | ProductionAdministrationError


class ProductionCutoverUseCase(Protocol):
    async def inspect(
        self, *, actor: str, scope: RepositoryScope
    ) -> ProductionScopeState | ProductionAdministrationError: ...

    async def evidence(
        self, *, actor: str, scope: RepositoryScope, authority_id: str
    ) -> bytes | ProductionAdministrationError: ...

    async def stage(
        self,
        command: ProductionCutoverCommand,
        *,
        envelope: bytes,
        evidence: bytes,
        provider_paths: tuple[str, ...],
    ) -> ProductionAdministrationResult: ...

    async def begin(self, command: ProductionCutoverCommand) -> ProductionAdministrationResult: ...

    async def activate(
        self, command: ProductionCutoverCommand, *, drain_envelope: bytes
    ) -> ProductionAdministrationResult: ...


class ProductionCutoverService:
    def __init__(
        self,
        *,
        authorizer: ConfigScopeAuthorizer,
        store: ProductionCutoverStore,
        epochs: ConfigEpochRepository,
        verifier: ProductionReceiptVerifier,
        drain_verifier: ProductionDrainVerifier,
        evidence_admission: ProductionEvidenceAdmission,
        observe_activation: ProductionActivationObservation,
    ) -> None:
        self._authorizer = authorizer
        self._store = store
        self._epochs = epochs
        self._verifier = verifier
        self._drain_verifier = drain_verifier
        self._evidence_admission = evidence_admission
        self._observe_activation = observe_activation

    async def inspect(
        self, *, actor: str, scope: RepositoryScope
    ) -> ProductionScopeState | ProductionAdministrationError:
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return ProductionAdministrationError("forbidden")
        try:
            state = await self._store.inspect(scope)
        except ProductionCutoverUnavailable:
            return ProductionAdministrationError("unavailable")
        if state is not None and state.scope != scope:
            return ProductionAdministrationError("unavailable")
        return ProductionAdministrationError("not_found") if state is None else state

    async def evidence(
        self, *, actor: str, scope: RepositoryScope, authority_id: str
    ) -> bytes | ProductionAdministrationError:
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return ProductionAdministrationError("forbidden")
        try:
            content = await self._store.load_evidence(scope, authority_id)
        except ProductionCutoverUnavailable:
            return ProductionAdministrationError("unavailable")
        return ProductionAdministrationError("not_found") if content is None else content

    async def stage(
        self,
        command: ProductionCutoverCommand,
        *,
        envelope: bytes,
        evidence: bytes,
        provider_paths: tuple[str, ...],
    ) -> ProductionAdministrationResult:
        if command.kind != "stage" or command.input_digest != production_stage_input_digest(
            envelope, evidence, provider_paths
        ):
            return ProductionAdministrationError("invalid_evidence")
        try:
            resolved = await self._resolve(command)
            if resolved is not None:
                return resolved
            grant = self._verifier(envelope)
            if (
                type(grant) is not ProductionAdmissionGrant
                or grant.authority_id != command.authority_id
            ):
                return ProductionAdministrationError("invalid_evidence")
            scope_grant = grant.scope_grant(command.scope)
            if scope_grant is None:
                return ProductionAdministrationError("invalid_evidence")
            active = await self._epochs.load_active(command.scope)
            if active is None or active.active.epoch_id != scope_grant.subject.config_epoch_id:
                return ProductionCutoverRejected("config_changed")
            try:
                admitted = await self._evidence_admission(
                    evidence, scope_grant, active.draft, provider_paths
                )
            except (TypeError, ValueError):
                return ProductionAdministrationError("invalid_evidence")
            return await self._store.stage(command, grant=grant, evidence=admitted)
        except (
            ProductionCutoverUnavailable,
            ProductionEvidenceAdmissionUnavailable,
            ConfigEpochStoreUnavailable,
        ):
            return ProductionAdministrationError("unavailable")

    async def begin(self, command: ProductionCutoverCommand) -> ProductionAdministrationResult:
        if command.kind != "begin" or command.input_digest != hash_object({}):
            return ProductionAdministrationError("invalid_evidence")
        try:
            resolved = await self._resolve(command)
            return resolved if resolved is not None else await self._store.begin(command)
        except ProductionCutoverUnavailable:
            return ProductionAdministrationError("unavailable")

    async def activate(
        self, command: ProductionCutoverCommand, *, drain_envelope: bytes
    ) -> ProductionAdministrationResult:
        if command.kind != "activate" or command.input_digest != sha256(drain_envelope).hexdigest():
            return ProductionAdministrationError("invalid_evidence")
        try:
            resolved = await self._resolve(command)
            if resolved is not None:
                return resolved
            drain = self._drain_verifier(drain_envelope)
            if drain is None:
                return ProductionAdministrationError("invalid_evidence")
            retained = await self._store.load_authority(command.scope, purpose="staged")
            if retained is None or retained.state.staged_authority_id != command.authority_id:
                return ProductionCutoverRejected("stage_changed")
            grant = self._verifier(retained.envelope_canonical_json)
            if (
                type(grant) is not ProductionAdmissionGrant
                or grant.authority_id != command.authority_id
            ):
                return ProductionAdministrationError("invalid_evidence")
            try:
                current = await self._observe_activation(retained, grant)
            except (TypeError, ValueError):
                return ProductionCutoverRejected("current_evidence_invalid")
            if current is None:
                return ProductionCutoverRejected("current_evidence_invalid")
            return await self._store.activate(command, grant=grant, current=current, drain=drain)
        except (ProductionCutoverUnavailable, TimeoutError):
            return ProductionAdministrationError("unavailable")

    async def _resolve(
        self, command: ProductionCutoverCommand
    ) -> ProductionAdministrationResult | None:
        if not await self._authorizer.allows_scope(actor=command.actor, scope=command.scope):
            return ProductionAdministrationError("forbidden")
        return await self._store.resolve(command)
