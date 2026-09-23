from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, insert, update
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, AuditAppendDuplicate
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.operator_controls.override import (
    ActiveOverride,
    OverrideAuditEvent,
    OverrideCommand,
)
from ci_coordinator.operator_controls.use_cases import OverrideApplied, OverrideDuplicate
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.operator_override_codec import _prepare_applied_event
from ci_coordinator.persistence.operator_override_repository import (
    PostgresOperatorOverrideRepository,
)
from ci_coordinator.persistence.production_admission_repository import (
    _PostgresProductionAdmissionRepository,
)
from ci_coordinator.persistence.production_authority_read import (
    load_production_evidence,
    load_retained_production_authority,
)
from ci_coordinator.persistence.production_cutover_audit import (
    prepare_cutover_audit,
    replay_cutover_audit,
)
from ci_coordinator.persistence.production_cutover_drain import production_local_drain_complete
from ci_coordinator.persistence.production_cutover_state import (
    load_production_scope_state,
    production_database_now,
)
from ci_coordinator.persistence.production_evidence_repository import (
    lock_production_evidence_capacity,
    retain_production_evidence,
    retain_production_stage,
    staged_activation_matches,
)
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.persistence.schema import production_scope_states
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.production_admission import ProductionAdmissionGrant
from ci_coordinator.production_admission.current_evidence import (
    CURRENT_PRODUCTION_OBSERVATION_SECONDS,
    CurrentActivationEvidence,
)
from ci_coordinator.production_admission.cutover_commands import (
    PRODUCTION_CUTOVER_EVENT_TYPE,
    ProductionCutoverApplied,
    ProductionCutoverCommand,
    ProductionCutoverRejected,
    ProductionCutoverResult,
    production_stage_input_digest,
)
from ci_coordinator.production_admission.cutover_drain import AdmittedProductionDrain
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.ports import RetainedProductionAuthority
from ci_coordinator.production_admission.relation_admission import StagedProductionEvidence


class PostgresProductionCutoverRepository:
    """One transaction owns state, immutable evidence and paired audit effects."""

    def __init__(
        self,
        connection: AsyncConnection,
        audit: _PostgresAuditEventRepository,
        authorities: _PostgresProductionAdmissionRepository,
        epochs: _PostgresConfigEpochRepository,
        overrides: PostgresOperatorOverrideRepository,
        reconciliation_profile: ShadowReconciliationStateProfile,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit = audit
        self._authorities = authorities
        self._epochs = epochs
        self._overrides = overrides
        self._reconciliation_profile = reconciliation_profile
        self._ensure_active = ensure_active
        self._rollback = mark_rollback_required

    async def inspect(self, scope: RepositoryScope) -> ProductionScopeState | None:
        self._ensure_active()
        return await load_production_scope_state(self._connection, scope)

    async def resolve(self, command: ProductionCutoverCommand) -> ProductionCutoverResult | None:
        self._ensure_active()
        return await self._replay(command)

    async def load_evidence(self, scope: RepositoryScope, authority_id: str) -> bytes | None:
        self._ensure_active()
        return await load_production_evidence(self._connection, scope, authority_id)

    async def load_authority(
        self, scope: RepositoryScope, *, purpose: Literal["active", "staged"]
    ) -> RetainedProductionAuthority | None:
        self._ensure_active()
        return await load_retained_production_authority(self._connection, scope, purpose=purpose)

    async def stage(
        self,
        command: ProductionCutoverCommand,
        grant: ProductionAdmissionGrant,
        evidence: StagedProductionEvidence,
    ) -> ProductionCutoverResult:
        async with self._mutation():
            return await self._stage(command, grant, evidence)

    async def begin(self, command: ProductionCutoverCommand) -> ProductionCutoverResult:
        async with self._mutation():
            return await self._begin(command)

    async def activate(
        self,
        command: ProductionCutoverCommand,
        grant: ProductionAdmissionGrant,
        drain: AdmittedProductionDrain,
        current: CurrentActivationEvidence,
    ) -> ProductionCutoverResult:
        async with self._mutation():
            return await self._activate(command, grant, drain, current)

    @asynccontextmanager
    async def _mutation(self) -> AsyncIterator[None]:
        self._ensure_active()
        try:
            yield
        except BaseException:
            self._rollback()
            raise

    async def _stage(
        self,
        command: ProductionCutoverCommand,
        grant: ProductionAdmissionGrant,
        evidence: StagedProductionEvidence,
    ) -> ProductionCutoverResult:
        self._ensure_active()
        if (
            type(command) is not ProductionCutoverCommand
            or command.kind != "stage"
            or type(grant) is not ProductionAdmissionGrant
            or type(evidence) is not StagedProductionEvidence
            or command.scope != evidence.scope_grant.subject.scope
            or command.authority_id != grant.authority_id
            or grant.scope_grant(command.scope) != evidence.scope_grant
            or command.input_digest
            != production_stage_input_digest(
                grant.registration.envelope_canonical_json,
                evidence.canonical_bytes,
                evidence.lookup.provider_paths,
            )
        ):
            raise ValueError("production staging input is inconsistent")
        await lock_production_evidence_capacity(self._connection)
        await lock_repository_scope(self._connection, command.scope)
        duplicate = await self._replay(command)
        if duplicate is not None:
            return duplicate
        state = await self.inspect(command.scope)
        if command.expected_revision != (0 if state is None else state.revision):
            return ProductionCutoverRejected("revision_changed")
        generation = 0 if state is None else state.generation
        if evidence.relation.generation != generation + 1:
            return ProductionCutoverRejected("generation_changed")
        if not await self._policy_current(
            command.scope, evidence.scope_grant.subject.config_epoch_id
        ):
            return ProductionCutoverRejected("config_changed")
        if await production_database_now(self._connection) >= grant.not_after:
            return ProductionCutoverRejected("authority_expired")
        if not await retain_production_evidence(self._connection, evidence):
            return ProductionCutoverRejected("capacity_exhausted")
        await self._authorities.register(grant.registration)
        await retain_production_stage(self._connection, grant=grant, evidence=evidence)
        after = (
            ProductionScopeState(command.scope, 1, staged_authority_id=grant.authority_id)
            if state is None
            else replace(state, revision=state.revision + 1, staged_authority_id=grant.authority_id)
        )
        return await self._apply(command, after, not_after=grant.not_after)

    async def _begin(self, command: ProductionCutoverCommand) -> ProductionCutoverResult:
        self._ensure_active()
        if (
            type(command) is not ProductionCutoverCommand
            or command.kind != "begin"
            or command.input_digest != hash_object({})
        ):
            raise ValueError("production cutover begin input is inconsistent")
        await lock_repository_scope(self._connection, command.scope)
        duplicate = await self._replay(command)
        if duplicate is not None:
            return duplicate
        state = await self.inspect(command.scope)
        if state is None or state.revision != command.expected_revision:
            return ProductionCutoverRejected("revision_changed")
        if command.authority_id not in (state.active_authority_id, state.staged_authority_id):
            return ProductionCutoverRejected("stage_changed")
        if state.latch_override_id is not None:
            return ProductionCutoverRejected("override_conflict")
        at = await production_database_now(self._connection)
        active = await self._overrides.resolve_active(scope=command.scope, subject_id=None, now=at)
        disable = active.disable_dynamic
        if disable is None:
            disable = await self._override(command, "disable_omission", None, at)
            if disable is None:
                self._rollback()
                return ProductionCutoverRejected("override_conflict")
        after = replace(
            state,
            revision=state.revision + 1,
            revoked_through_generation=state.generation,
            latch_override_id=disable.override_id,
            latch_applied_at=disable.applied_at,
        )
        return await self._apply(command, after)

    async def _activate(
        self,
        command: ProductionCutoverCommand,
        grant: ProductionAdmissionGrant,
        drain: AdmittedProductionDrain,
        current: CurrentActivationEvidence,
    ) -> ProductionCutoverResult:
        self._ensure_active()
        if (
            type(command) is not ProductionCutoverCommand
            or command.kind != "activate"
            or type(grant) is not ProductionAdmissionGrant
            or type(drain) is not AdmittedProductionDrain
            or type(current) is not CurrentActivationEvidence
            or command.authority_id != grant.authority_id
            or command.input_digest != drain.envelope_digest
            or grant.scope_grant(command.scope) != current.scope_grant
        ):
            raise ValueError("production activation input is inconsistent")
        await lock_repository_scope(self._connection, command.scope)
        duplicate = await self._replay(command)
        if duplicate is not None:
            return duplicate
        state = _require_staged_revision(await self.inspect(command.scope), command)
        if isinstance(state, ProductionCutoverRejected):
            return state
        at = await production_database_now(self._connection)
        if current.scope_revision != state.revision or not current.is_current_at(at):
            return ProductionCutoverRejected("current_evidence_invalid")
        if not drain.matches(state, current.scope_grant, database_now=at):
            return ProductionCutoverRejected("drain_incomplete")
        if not await staged_activation_matches(self._connection, grant.authority_id, current):
            return ProductionCutoverRejected("current_evidence_invalid")
        if not await self._policy_current(
            command.scope, current.scope_grant.subject.config_epoch_id
        ):
            return ProductionCutoverRejected("config_changed")
        active = await self._overrides.resolve_active(scope=command.scope, subject_id=None, now=at)
        if (
            active.disable_dynamic is None
            or active.disable_dynamic.override_id != state.latch_override_id
        ):
            return ProductionCutoverRejected("override_conflict")
        not_after = min(
            grant.not_after,
            drain.statement.expires_at,
            current.database_started_at + timedelta(seconds=CURRENT_PRODUCTION_OBSERVATION_SECONDS),
        )
        if not await production_local_drain_complete(
            self._connection,
            scope=command.scope,
            database_now=at,
            profile=self._reconciliation_profile,
            not_after=not_after,
        ):
            return ProductionCutoverRejected("drain_incomplete")
        at = await production_database_now(self._connection)
        if not current.is_current_at(at) or not drain.matches(
            state, current.scope_grant, database_now=at
        ):
            return ProductionCutoverRejected("current_evidence_invalid")
        if at >= not_after:
            return ProductionCutoverRejected("authority_expired")
        after = replace(
            state,
            revision=state.revision + 1,
            generation=current.scope_grant.relation.generation,
            active_authority_id=grant.authority_id,
            active_subject_digest=current.scope_grant.admission_subject_digest,
            staged_authority_id=None,
            latch_override_id=None,
            latch_applied_at=None,
        )
        # Clearing the cutover latch is visible only with the matching enable and audit commit.
        if not await self._save_state(command, after, not_after=not_after):
            self._rollback()
            return ProductionCutoverRejected("authority_expired")
        enable = await self._override(command, "enable_omission", state.latch_override_id, at)
        if enable is None:
            self._rollback()
            return ProductionCutoverRejected("override_conflict")
        await self._record(command, after, at)
        completed_at = await production_database_now(self._connection)
        if completed_at >= not_after:
            self._rollback()
            return ProductionCutoverRejected("authority_expired")
        if not current.is_current_at(completed_at) or not drain.matches(
            state, current.scope_grant, database_now=completed_at
        ):
            self._rollback()
            return ProductionCutoverRejected("current_evidence_invalid")
        return ProductionCutoverApplied(after)

    async def _policy_current(self, scope: RepositoryScope, epoch_id: str) -> bool:
        snapshot = await self._epochs.load_active(scope)
        return snapshot is not None and snapshot.active.epoch_id == epoch_id

    async def _replay(self, command: ProductionCutoverCommand) -> ProductionCutoverResult | None:
        event = await self._audit._find_pair_owned(
            command.audit_key, PRODUCTION_CUTOVER_EVENT_TYPE, command.scope
        )
        return None if event is None else replay_cutover_audit(event, command)

    async def _apply(
        self,
        command: ProductionCutoverCommand,
        after: ProductionScopeState,
        *,
        not_after: datetime | None = None,
    ) -> ProductionCutoverResult:
        if not await self._save_state(command, after, not_after=not_after):
            self._rollback()
            return ProductionCutoverRejected("authority_expired")
        await self._record(command, after, await production_database_now(self._connection))
        if not_after is not None and await production_database_now(self._connection) >= not_after:
            self._rollback()
            return ProductionCutoverRejected("authority_expired")
        return ProductionCutoverApplied(after)

    async def _save_state(
        self,
        command: ProductionCutoverCommand,
        after: ProductionScopeState,
        *,
        not_after: datetime | None,
    ) -> bool:
        table = production_scope_states
        values = {
            "revision": after.revision,
            "generation": after.generation,
            "revoked_through_generation": after.revoked_through_generation,
            "active_authority_id": after.active_authority_id,
            "staged_authority_id": after.staged_authority_id,
            "latch_override_id": after.latch_override_id,
            "latch_applied_at": after.latch_applied_at,
        }
        if command.expected_revision == 0:
            if (
                not_after is not None
                and await production_database_now(self._connection) >= not_after
            ):
                return False
            await self._connection.execute(
                insert(table).values(
                    **values,
                    installation_id=after.scope.installation_id,
                    repository_id=after.scope.repository_id,
                )
            )
            return True
        statement = (
            update(table)
            .where(
                table.c.installation_id == command.scope.installation_id,
                table.c.repository_id == command.scope.repository_id,
                table.c.revision == command.expected_revision,
            )
            .values(values)
        )
        if not_after is not None:
            statement = statement.where(func.clock_timestamp() < not_after)
        won = await self._connection.scalar(statement.returning(table.c.revision))
        if won == after.revision:
            return True
        if not_after is not None and await production_database_now(self._connection) >= not_after:
            return False
        raise PersistenceInvariantViolation("production scope compare-and-set lost under its lock")

    async def _record(
        self, command: ProductionCutoverCommand, after: ProductionScopeState, at: datetime
    ) -> None:
        outcome = await self._audit._append_pair_owned(
            prepare_cutover_audit(command, after, at), command.scope
        )
        if not isinstance(outcome, AuditAppendAppended | AuditAppendDuplicate):
            self._rollback()
            raise PersistenceInvariantViolation("production transition conflicts with its audit")

    async def _override(
        self,
        command: ProductionCutoverCommand,
        kind: Literal["disable_omission", "enable_omission"],
        target: str | None,
        at: datetime,
    ) -> ActiveOverride | None:
        if kind not in {"disable_omission", "enable_omission"}:
            raise ValueError("production cutover cannot create a subject override")
        override = ActiveOverride.create(
            OverrideCommand(
                kind=kind,
                scope=command.scope,
                subject_id=target,
                operation_id=command.audit_key + ":override",
                actor=command.actor,
                reason=command.reason,
                expires_at=None,
            ),
            at,
        )
        outcome = await self._overrides.apply(
            override, _prepare_applied_event(override, OverrideAuditEvent.applied(override))
        )
        return (
            outcome.override if isinstance(outcome, OverrideApplied | OverrideDuplicate) else None
        )


def _require_staged_revision(
    state: ProductionScopeState | None, command: ProductionCutoverCommand
) -> ProductionScopeState | ProductionCutoverRejected:
    if state is None or state.revision != command.expected_revision:
        return ProductionCutoverRejected("revision_changed")
    if state.staged_authority_id != command.authority_id:
        return ProductionCutoverRejected("stage_changed")
    return state
