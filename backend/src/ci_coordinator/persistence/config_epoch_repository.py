"""PostgreSQL adapter for immutable config epochs and atomic activation pairs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Literal, cast

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditEventRecord,
    verify_audit_event_integrity,
)
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_control.contracts import PolicySourceFormat
from ci_coordinator.config_control.epoch_integrity import (
    EpochDraftIntegrityError,
    assert_admitted_epoch_draft,
)
from ci_coordinator.config_epochs import (
    MAX_CONFIG_EPOCH_PAGE_SIZE,
    ActiveConfigEpoch,
    ActiveConfigEpochSnapshot,
    ConfigEpochActivationApplied,
    ConfigEpochActivationCommand,
    ConfigEpochActivationDuplicate,
    ConfigEpochActivationOperationConflict,
    ConfigEpochActivationRecord,
    ConfigEpochActivationResult,
    ConfigEpochActivationRevisionConflict,
    ConfigEpochActivationTargetUnavailable,
    ConfigEpochOperationResolution,
    ConfigEpochPage,
    ConfigEpochRegistrationCommand,
    ConfigEpochRegistrationCommitted,
    ConfigEpochRegistrationConflict,
    ConfigEpochRegistrationCreated,
    ConfigEpochRegistrationDuplicate,
    ConfigEpochRegistrationOperationConflict,
    ConfigEpochRegistrationOperationResult,
    ConfigEpochRegistrationRecord,
    ConfigEpochRegistrationReplay,
    ConfigEpochRegistrationResult,
    ConfigEpochReplayCommand,
    ConfigEpochStatus,
    ConfigEpochSummary,
    PreparedConfigEpochActivation,
    PreparedConfigEpochRegistration,
    prepare_config_epoch_activation,
    prepare_config_epoch_registration,
)
from ci_coordinator.persistence.audit_codec import row_to_record
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.compatibility_admission import admit_schema_dependent_operation
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.persistence.scalar_rows import optional_int as _optional_int
from ci_coordinator.persistence.scalar_rows import optional_string as _optional_string
from ci_coordinator.persistence.scalar_rows import required_bytes as _required_bytes
from ci_coordinator.persistence.scalar_rows import required_int as _required_int
from ci_coordinator.persistence.scalar_rows import required_string as _required_string
from ci_coordinator.persistence.schema import (
    active_config_epochs,
    audit_events,
    config_epoch_activations,
    config_epoch_registrations,
    config_epochs,
)
from ci_coordinator.persistence.schema_capabilities import config_epoch_lifecycle_requirements


async def admit_config_epoch_lifecycle(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
) -> None:
    """Admit config-owner reads used from a broader transaction boundary."""

    await admit_schema_dependent_operation(
        connection,
        profile,
        config_epoch_lifecycle_requirements(profile),
    )


class _PostgresConfigEpochRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        audit_events: _PostgresAuditEventRepository,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit_events = audit_events
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def register(self, draft: ValidatedEpochDraft) -> ConfigEpochRegistrationResult:
        self._ensure_active()
        try:
            admitted = assert_admitted_epoch_draft(draft)
        except EpochDraftIntegrityError:
            raise
        try:
            return await self._register_draft(admitted)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceInvariantViolation, EpochDraftIntegrityError):
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("config epoch registration failed") from error

    async def register_operation(
        self,
        prepared: PreparedConfigEpochRegistration,
    ) -> ConfigEpochRegistrationOperationResult:
        self._ensure_active()
        if type(prepared) is not PreparedConfigEpochRegistration:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("config registration must be exactly prepared")
        try:
            command = prepared.command
            draft = assert_admitted_epoch_draft(command.draft)
            await self._lock_scope(draft.scope)
            existing = await self._load_registration(draft.scope, command.operation_id)
            if existing is not None:
                if await self._same_registration(existing, command):
                    return ConfigEpochRegistrationReplay(existing)
                return ConfigEpochRegistrationOperationConflict(existing)
            content = await self._register_draft(draft)
            if isinstance(content, ConfigEpochRegistrationConflict):
                raise PersistenceInvariantViolation("config epoch content identity collided")
            appended = await self._audit_events._append_pair_owned(
                prepared._take_audit_event(),
                draft.scope,
            )
            if not isinstance(appended, AuditAppendAppended):
                raise PersistenceInvariantViolation(
                    "config registration audit idempotency conflicts with its operation"
                )
            record = ConfigEpochRegistrationRecord(
                scope=draft.scope,
                operation_id=command.operation_id,
                epoch_id=draft.epoch_id,
                audit_event_id=appended.record.audit_event_id,
                audit_input_hash=appended.record.input_hash,
            )
            await self._connection.execute(
                insert(config_epoch_registrations).values(_registration_to_row(record))
            )
            return ConfigEpochRegistrationCommitted(record)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceInvariantViolation, EpochDraftIntegrityError):
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("config epoch operation registration failed") from error

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self._ensure_active()
        _require_scope(scope)
        try:
            result = await self._connection.execute(
                select(
                    active_config_epochs.c.epoch_id.label("active_epoch_id"),
                    active_config_epochs.c.revision,
                    *config_epochs.c,
                )
                .join(
                    config_epochs,
                    (active_config_epochs.c.installation_id == config_epochs.c.installation_id)
                    & (active_config_epochs.c.repository_id == config_epochs.c.repository_id)
                    & (active_config_epochs.c.epoch_id == config_epochs.c.epoch_id),
                )
                .where(
                    active_config_epochs.c.installation_id == scope.installation_id,
                    active_config_epochs.c.repository_id == scope.repository_id,
                )
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            active = ActiveConfigEpoch(
                scope=scope,
                epoch_id=_required_string(dict(row), "active_epoch_id"),
                revision=_required_int(dict(row), "revision"),
            )
            draft = _draft_from_row(dict(row))
            return ActiveConfigEpochSnapshot(active=active, draft=draft)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceInvariantViolation, EpochDraftIntegrityError):
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("active config epoch read failed") from error

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None:
        self._ensure_active()
        _require_scope(scope)
        _require_epoch_id(epoch_id)
        try:
            return await self._load_draft(epoch_id, scope=scope)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceInvariantViolation, EpochDraftIntegrityError):
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("config epoch read failed") from error

    async def read_status(
        self,
        scope: RepositoryScope,
        *,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigEpochStatus:
        self._ensure_active()
        _require_scope(scope)
        if after_epoch_id is not None:
            _require_epoch_id(after_epoch_id)
        if type(limit) is not int or not 1 <= limit <= MAX_CONFIG_EPOCH_PAGE_SIZE:
            raise ValueError("config epoch page limit is outside its admitted bound")
        try:
            active_row = (
                (
                    await self._connection.execute(
                        select(active_config_epochs).where(
                            active_config_epochs.c.installation_id == scope.installation_id,
                            active_config_epochs.c.repository_id == scope.repository_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            statement = select(
                config_epochs.c.epoch_id,
                config_epochs.c.source_format,
                config_epochs.c.source_hash,
                config_epochs.c.document_hash,
                config_epochs.c.epoch_hash,
                func.octet_length(config_epochs.c.source_bytes).label("source_byte_count"),
            ).where(
                config_epochs.c.installation_id == scope.installation_id,
                config_epochs.c.repository_id == scope.repository_id,
            )
            if after_epoch_id is not None:
                statement = statement.where(config_epochs.c.epoch_id > after_epoch_id)
            rows = tuple(
                (
                    await self._connection.execute(
                        statement.order_by(config_epochs.c.epoch_id).limit(limit + 1)
                    )
                ).mappings()
            )
            visible = rows[:limit]
            items = tuple(_summary_from_row(dict(row)) for row in visible)
            active = None
            if active_row is not None:
                active_fields = dict(active_row)
                active = ActiveConfigEpoch(
                    scope=scope,
                    epoch_id=_required_string(active_fields, "epoch_id"),
                    revision=_required_int(active_fields, "revision"),
                )
            return ConfigEpochStatus(
                scope=scope,
                active=active,
                epochs=ConfigEpochPage(
                    items=items,
                    next_cursor=items[-1].epoch_id if len(rows) > limit else None,
                ),
            )
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceInvariantViolation, ValueError, TypeError):
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("config epoch status read failed") from error

    async def activate(
        self,
        prepared: PreparedConfigEpochActivation,
    ) -> ConfigEpochActivationResult:
        self._ensure_active()
        if type(prepared) is not PreparedConfigEpochActivation:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("config epoch activation must be exactly prepared")
        try:
            command = prepared.command
            await self._lock_scope(command.scope)
            existing = await self._load_activation(command.scope, command.operation_id)
            if existing is not None:
                if await self._same_operation(
                    existing,
                    ConfigEpochReplayCommand.from_activation(command),
                ):
                    return ConfigEpochActivationDuplicate(record=existing)
                return ConfigEpochActivationOperationConflict(existing=existing)

            target = await self._load_draft(command.target_epoch_id, scope=command.scope)
            if target is None:
                return ConfigEpochActivationTargetUnavailable()
            previous = await self._load_active_for_update(command.scope)
            if not _revision_matches(previous, command.expected_revision):
                return ConfigEpochActivationRevisionConflict(active=previous)
            if (
                command.mutation_kind == "rollback"
                and previous is not None
                and previous.epoch_id != command.expected_active_epoch_id
            ):
                return ConfigEpochActivationRevisionConflict(active=previous)

            appended = await self._audit_events._append_pair_owned(
                prepared._take_audit_event(),
                command.scope,
            )
            if not isinstance(appended, AuditAppendAppended):
                raise PersistenceInvariantViolation(
                    "config activation audit idempotency is inconsistent with its operation"
                )
            active = ActiveConfigEpoch(
                scope=command.scope,
                epoch_id=target.epoch_id,
                revision=1 if previous is None else previous.revision + 1,
            )
            record = ConfigEpochActivationRecord(
                scope=command.scope,
                operation_id=command.operation_id,
                expected_revision=command.expected_revision,
                previous=previous,
                active=active,
                audit_event_id=appended.record.audit_event_id,
                audit_input_hash=appended.record.input_hash,
            )
            await self._write_active(previous, active)
            await self._connection.execute(
                insert(config_epoch_activations).values(_activation_to_row(record))
            )
            return ConfigEpochActivationApplied(record=record)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("config epoch activation failed") from error

    async def resolve_operation(
        self,
        command: ConfigEpochReplayCommand,
    ) -> ConfigEpochOperationResolution:
        self._ensure_active()
        if type(command) is not ConfigEpochReplayCommand:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("config epoch replay command must be exact")
        try:
            await self._lock_scope(command.scope)
            existing = await self._load_activation(command.scope, command.operation_id)
            if existing is None:
                return None
            if await self._same_operation(existing, command):
                return ConfigEpochActivationDuplicate(record=existing)
            return ConfigEpochActivationOperationConflict(existing=existing)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("config epoch operation lookup failed") from error

    async def _register_draft(
        self,
        admitted: ValidatedEpochDraft,
    ) -> ConfigEpochRegistrationResult:
        inserted = await self._connection.scalar(
            postgres_insert(config_epochs)
            .values(_draft_to_row(admitted))
            .on_conflict_do_nothing(index_elements=[config_epochs.c.epoch_id])
            .returning(config_epochs.c.epoch_id)
        )
        if inserted == admitted.epoch_id:
            return ConfigEpochRegistrationCreated(epoch_id=admitted.epoch_id)
        stored = await self._load_draft(admitted.epoch_id)
        if stored is None:
            raise PersistenceInvariantViolation("config epoch insert outcome is indeterminate")
        if stored == admitted:
            return ConfigEpochRegistrationDuplicate(epoch_id=admitted.epoch_id)
        return ConfigEpochRegistrationConflict(epoch_id=admitted.epoch_id)

    async def _load_draft(
        self,
        epoch_id: str,
        *,
        scope: RepositoryScope | None = None,
    ) -> ValidatedEpochDraft | None:
        statement = select(config_epochs).where(config_epochs.c.epoch_id == epoch_id)
        if scope is not None:
            statement = statement.where(
                config_epochs.c.installation_id == scope.installation_id,
                config_epochs.c.repository_id == scope.repository_id,
            )
        result = await self._connection.execute(statement)
        row = result.mappings().one_or_none()
        return None if row is None else _draft_from_row(dict(row))

    async def _load_registration(
        self,
        scope: RepositoryScope,
        operation_id: str,
    ) -> ConfigEpochRegistrationRecord | None:
        result = await self._connection.execute(
            select(config_epoch_registrations).where(
                config_epoch_registrations.c.installation_id == scope.installation_id,
                config_epoch_registrations.c.repository_id == scope.repository_id,
                config_epoch_registrations.c.operation_id == operation_id,
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else _registration_from_row(dict(row))

    async def _same_registration(
        self,
        record: ConfigEpochRegistrationRecord,
        command: ConfigEpochRegistrationCommand,
    ) -> bool:
        draft = command.draft
        if record.epoch_id != draft.epoch_id:
            return False
        retained = await self._load_draft(record.epoch_id, scope=record.scope)
        if retained is None:
            raise PersistenceInvariantViolation("config registration epoch is missing")
        if retained != draft:
            return False
        event = await self._load_audit_event(record.audit_event_id)
        if verify_audit_event_integrity(event) is not None:
            raise PersistenceInvariantViolation(
                "config registration audit event integrity disagrees"
            )
        if record.audit_input_hash != event.input_hash:
            raise PersistenceInvariantViolation(
                "config registration receipt and audit input hash disagree"
            )
        if event.actor != command.actor:
            return False
        try:
            expected = prepare_config_epoch_registration(
                ConfigEpochRegistrationCommand(
                    draft=draft,
                    operation_id=command.operation_id,
                    actor=command.actor,
                    occurred_at=event.created_at,
                )
            )
        except (TypeError, ValueError):
            return False
        return record.audit_input_hash == expected.audit_input_hash

    async def _lock_scope(self, scope: RepositoryScope) -> None:
        await lock_repository_scope(self._connection, scope)

    async def _load_active_for_update(self, scope: RepositoryScope) -> ActiveConfigEpoch | None:
        result = await self._connection.execute(
            select(active_config_epochs)
            .where(
                active_config_epochs.c.installation_id == scope.installation_id,
                active_config_epochs.c.repository_id == scope.repository_id,
            )
            .with_for_update()
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        fields = dict(row)
        return ActiveConfigEpoch(
            scope=scope,
            epoch_id=_required_string(fields, "epoch_id"),
            revision=_required_int(fields, "revision"),
        )

    async def _load_activation(
        self,
        scope: RepositoryScope,
        operation_id: str,
    ) -> ConfigEpochActivationRecord | None:
        result = await self._connection.execute(
            select(config_epoch_activations).where(
                config_epoch_activations.c.installation_id == scope.installation_id,
                config_epoch_activations.c.repository_id == scope.repository_id,
                config_epoch_activations.c.operation_id == operation_id,
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else _activation_from_row(dict(row))

    async def _same_operation(
        self,
        record: ConfigEpochActivationRecord,
        replay: ConfigEpochReplayCommand,
    ) -> bool:
        if (
            record.expected_revision != replay.expected_revision
            or record.active.epoch_id != replay.target_epoch_id
        ):
            return False
        event = await self._load_audit_event(record.audit_event_id)
        if event.actor != replay.actor:
            return False
        retained_active_epoch_id: str | None = None
        retained_coverage_relation = None
        retained_proposal_manifest_id: str | None = None
        retained_authority_evidence_hash: str | None = None
        retained_authority_observed_at: str | None = None
        payload = event.payload
        if type(payload) is not dict:
            return False
        if replay.mutation_kind == "activation":
            proposal_manifest_id = payload.get("proposalManifestId")
            authority_evidence_hash = payload.get("authorityEvidenceHash")
            authority_observed_at = payload.get("authorityObservedAt")
            if (
                type(proposal_manifest_id) is not str
                or type(authority_evidence_hash) is not str
                or type(authority_observed_at) is not str
                or proposal_manifest_id != replay.proposal_manifest_id
            ):
                return False
            retained_proposal_manifest_id = proposal_manifest_id
            retained_authority_evidence_hash = authority_evidence_hash
            retained_authority_observed_at = authority_observed_at
        else:
            active_epoch_id = payload.get("activeEpochId")
            retained_coverage_relation = _coverage_relation(payload.get("coverageRelation"))
            if type(active_epoch_id) is not str or retained_coverage_relation is None:
                return False
            retained_active_epoch_id = active_epoch_id
        try:
            retained_prepared = prepare_config_epoch_activation(
                ConfigEpochActivationCommand(
                    scope=replay.scope,
                    target_epoch_id=replay.target_epoch_id,
                    expected_revision=replay.expected_revision,
                    operation_id=replay.operation_id,
                    actor=replay.actor,
                    occurred_at=event.created_at,
                    proposal_manifest_id=retained_proposal_manifest_id,
                    authority_evidence_hash=retained_authority_evidence_hash,
                    authority_observed_at=retained_authority_observed_at,
                    mutation_kind=replay.mutation_kind,
                    expected_active_epoch_id=retained_active_epoch_id,
                    coverage_relation=retained_coverage_relation,
                    reason=replay.reason,
                )
            )
        except (TypeError, ValueError):
            return False
        return record.audit_input_hash == retained_prepared.audit_input_hash

    async def _load_audit_event(self, audit_event_id: str) -> AuditEventRecord:
        result = await self._connection.execute(
            select(audit_events).where(audit_events.c.audit_event_id == audit_event_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise PersistenceInvariantViolation("config activation audit event is missing")
        event = row_to_record(dict(row))
        if event.audit_event_id != audit_event_id:
            raise PersistenceInvariantViolation("config activation audit event is inconsistent")
        return event

    async def _write_active(
        self,
        previous: ActiveConfigEpoch | None,
        active: ActiveConfigEpoch,
    ) -> None:
        values = {
            "installation_id": active.scope.installation_id,
            "repository_id": active.scope.repository_id,
            "epoch_id": active.epoch_id,
            "revision": active.revision,
        }
        if previous is None:
            await self._connection.execute(insert(active_config_epochs).values(values))
            return
        updated = await self._connection.execute(
            update(active_config_epochs)
            .where(
                active_config_epochs.c.installation_id == active.scope.installation_id,
                active_config_epochs.c.repository_id == active.scope.repository_id,
                active_config_epochs.c.revision == previous.revision,
            )
            .values(epoch_id=active.epoch_id, revision=active.revision)
        )
        if updated.rowcount != 1:
            raise PersistenceInvariantViolation(
                "config epoch active pointer compare-and-set failed"
            )


def _draft_to_row(draft: ValidatedEpochDraft) -> dict[str, object]:
    return {
        "epoch_id": draft.epoch_id,
        "installation_id": draft.scope.installation_id,
        "repository_id": draft.scope.repository_id,
        "source_format": draft.source_format,
        "source_bytes": draft.source_bytes,
        "normalized_document_bytes": draft.normalized_document_bytes,
        "compiled_policy_bytes": draft.compiled_policy_bytes,
        "document_schema_id": draft.document_schema_id,
        "document_profile_id": draft.document_profile_id,
        "semantic_profile_id": draft.semantic_profile_id,
        "compiled_schema_id": draft.compiled_schema_id,
        "producer_resource_profile_id": draft.producer_resource_profile_id,
        "producer_byte_profile_id": draft.producer_byte_profile_id,
        "producer_feasibility_profile_id": draft.producer_feasibility_profile_id,
        "source_hash": draft.source_hash,
        "document_hash": draft.document_hash,
        "epoch_hash": draft.epoch_hash,
    }


def _draft_from_row(row: Mapping[str, object]) -> ValidatedEpochDraft:
    try:
        draft = ValidatedEpochDraft(
            source_format=_required_source_format(row),
            source_bytes=_required_bytes(row, "source_bytes"),
            scope=RepositoryScope(
                installation_id=_required_int(row, "installation_id"),
                repository_id=_required_int(row, "repository_id"),
            ),
            normalized_document_bytes=_required_bytes(row, "normalized_document_bytes"),
            compiled_policy_bytes=_required_bytes(row, "compiled_policy_bytes"),
            document_schema_id=_required_string(row, "document_schema_id"),
            document_profile_id=_required_string(row, "document_profile_id"),
            semantic_profile_id=_required_string(row, "semantic_profile_id"),
            compiled_schema_id=_required_string(row, "compiled_schema_id"),
            producer_resource_profile_id=_required_string(row, "producer_resource_profile_id"),
            producer_byte_profile_id=_required_string(row, "producer_byte_profile_id"),
            producer_feasibility_profile_id=_required_string(
                row, "producer_feasibility_profile_id"
            ),
            source_hash=_required_string(row, "source_hash"),
            document_hash=_required_string(row, "document_hash"),
            epoch_hash=_required_string(row, "epoch_hash"),
            epoch_id=_required_string(row, "epoch_id"),
        )
        return assert_admitted_epoch_draft(draft)
    except (EpochDraftIntegrityError, ValueError, TypeError) as error:
        raise PersistenceInvariantViolation("stored config epoch is invalid") from error


def _summary_from_row(row: Mapping[str, object]) -> ConfigEpochSummary:
    try:
        return ConfigEpochSummary(
            epoch_id=_required_string(row, "epoch_id"),
            source_format=_required_source_format(row),
            source_hash=_required_string(row, "source_hash"),
            document_hash=_required_string(row, "document_hash"),
            epoch_hash=_required_string(row, "epoch_hash"),
            source_byte_count=_required_int(row, "source_byte_count"),
        )
    except (ValueError, TypeError) as error:
        raise PersistenceInvariantViolation("stored config epoch summary is invalid") from error


def _registration_to_row(record: ConfigEpochRegistrationRecord) -> dict[str, object]:
    return {
        "installation_id": record.scope.installation_id,
        "repository_id": record.scope.repository_id,
        "operation_id": record.operation_id,
        "epoch_id": record.epoch_id,
        "audit_event_id": record.audit_event_id,
        "audit_input_hash": bytes.fromhex(record.audit_input_hash),
    }


def _registration_from_row(row: Mapping[str, object]) -> ConfigEpochRegistrationRecord:
    try:
        return ConfigEpochRegistrationRecord(
            scope=RepositoryScope(
                installation_id=_required_int(row, "installation_id"),
                repository_id=_required_int(row, "repository_id"),
            ),
            operation_id=_required_string(row, "operation_id"),
            epoch_id=_required_string(row, "epoch_id"),
            audit_event_id=_required_string(row, "audit_event_id"),
            audit_input_hash=_required_bytes(row, "audit_input_hash").hex(),
        )
    except (ValueError, TypeError) as error:
        raise PersistenceInvariantViolation(
            "stored config epoch registration is invalid"
        ) from error


def _activation_to_row(record: ConfigEpochActivationRecord) -> dict[str, object]:
    return {
        "installation_id": record.scope.installation_id,
        "repository_id": record.scope.repository_id,
        "operation_id": record.operation_id,
        "expected_revision": record.expected_revision,
        "previous_epoch_id": None if record.previous is None else record.previous.epoch_id,
        "previous_revision": None if record.previous is None else record.previous.revision,
        "target_epoch_id": record.active.epoch_id,
        "result_revision": record.active.revision,
        "audit_event_id": record.audit_event_id,
        "audit_input_hash": bytes.fromhex(record.audit_input_hash),
    }


def _activation_from_row(row: Mapping[str, object]) -> ConfigEpochActivationRecord:
    try:
        scope = RepositoryScope(
            installation_id=_required_int(row, "installation_id"),
            repository_id=_required_int(row, "repository_id"),
        )
        previous_revision = _optional_int(row, "previous_revision")
        previous_epoch_id = _optional_string(row, "previous_epoch_id")
        previous = (
            None
            if previous_revision is None or previous_epoch_id is None
            else ActiveConfigEpoch(
                scope=scope,
                epoch_id=previous_epoch_id,
                revision=previous_revision,
            )
        )
        return ConfigEpochActivationRecord(
            scope=scope,
            operation_id=_required_string(row, "operation_id"),
            expected_revision=_optional_int(row, "expected_revision"),
            previous=previous,
            active=ActiveConfigEpoch(
                scope=scope,
                epoch_id=_required_string(row, "target_epoch_id"),
                revision=_required_int(row, "result_revision"),
            ),
            audit_event_id=_required_string(row, "audit_event_id"),
            audit_input_hash=_required_bytes(row, "audit_input_hash").hex(),
        )
    except (ValueError, TypeError) as error:
        raise PersistenceInvariantViolation("stored config epoch activation is invalid") from error


def _revision_matches(active: ActiveConfigEpoch | None, expected: int | None) -> bool:
    return (active is None and expected is None) or (
        active is not None and active.revision == expected
    )


def _require_scope(value: object) -> None:
    if type(value) is not RepositoryScope:
        raise ValueError("config epoch scope must be an exact RepositoryScope")


def _require_epoch_id(value: object) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("config epoch identity must be lowercase SHA-256 hexadecimal")


def _coverage_relation(value: object) -> Literal["equal", "greater"] | None:
    if value == "equal" or value == "greater":
        return value
    return None


def _required_source_format(row: Mapping[str, object]) -> PolicySourceFormat:
    value = _required_string(row, "source_format")
    if value not in {"json", "yaml-1.2"}:
        raise TypeError("source_format is invalid")
    return cast(PolicySourceFormat, value)
