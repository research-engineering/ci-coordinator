"""Atomic PostgreSQL review and activation guard for one registered, retained epoch."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, insert, not_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventRecord
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_control.epoch_integrity import EpochDraftIntegrityError
from ci_coordinator.config_epochs import (
    CONFIG_EPOCH_REGISTRATION_EVENT_TYPE,
    ActiveConfigEpoch,
    ActiveConfigEpochSnapshot,
    ConfigEpochReplayCommand,
    PreparedConfigEpochActivation,
)
from ci_coordinator.control_plane_identity import (
    GitHubReviewerEvidence,
    GitHubReviewerPrincipal,
    RegisteredEpochStepUpBinding,
    ReviewerPermission,
)
from ci_coordinator.persistence.audit_codec import row_to_record
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.persistence.scalar_rows import optional_int as _optional_int
from ci_coordinator.persistence.scalar_rows import optional_string as _optional_string
from ci_coordinator.persistence.scalar_rows import required_bytes as _required_bytes
from ci_coordinator.persistence.scalar_rows import required_int as _required_int
from ci_coordinator.persistence.scalar_rows import required_string as _required_string
from ci_coordinator.persistence.schema import (
    audit_events,
    config_epoch_registrations,
    control_plane_sessions,
    registered_epoch_attestation_transactions,
    registered_epoch_reviews,
)
from ci_coordinator.proposal_review import (
    REGISTERED_EPOCH_REVIEW_ACCEPTED_EVENT_TYPE,
    AttestedConfigActivationResult,
    PreparedRegisteredEpochReview,
    RegisteredEpochActivationAuthority,
    RegisteredEpochAttestationEvidence,
    RegisteredEpochAttestationTransaction,
    RegisteredEpochLookup,
    RegisteredEpochRegistrant,
    RegisteredEpochReviewAttestationConflict,
    RegisteredEpochReviewBaselineConflict,
    RegisteredEpochReviewCommand,
    RegisteredEpochReviewCreated,
    RegisteredEpochReviewDraft,
    RegisteredEpochReviewDuplicate,
    RegisteredEpochReviewOperationConflict,
    RegisteredEpochReviewRecord,
    RegisteredEpochReviewResolution,
    RegisteredEpochReviewWriteResult,
    RegisteredEpochSubject,
    RegisteredEpochWithoutReceipt,
    RepositoryActivationAuthorityConflict,
    RepositoryAttestationCapacityExceeded,
    RepositoryAttestationRegistered,
    RepositoryAttestationRegistration,
    RepositoryAttestationRegistrationRejected,
    decode_policy_semantic_diff,
    prepare_registered_epoch_review,
)

_MAX_PENDING_ATTESTATIONS_PER_SESSION = 8
_EXPIRED_ATTESTATION_CLEANUP_LIMIT = 128
_ACTIVATION_RECHECK_LIFETIME = timedelta(seconds=30)
_PENDING = registered_epoch_attestation_transactions


class _PostgresRegisteredEpochReviewRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        audit_events: _PostgresAuditEventRepository,
        config_epochs: _PostgresConfigEpochRepository,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit_events = audit_events
        self._config_epochs = config_epochs
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def current_time(self) -> datetime:
        self._ensure_active()
        try:
            value = await self._connection.scalar(select(func.statement_timestamp()))
            return _aware_utc(value, "registered-epoch review database clock")
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch review database clock failed") from error

    async def register_attestation(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> RepositoryAttestationRegistration:
        self._ensure_active()
        if type(transaction) is not RegisteredEpochAttestationTransaction:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("registered-epoch attestation must be exact")
        binding = transaction.binding
        try:
            await lock_repository_scope(self._connection, binding.scope)
            now = await self.current_time()
            if not binding.issued_at <= now < binding.expires_at:
                return RepositoryAttestationRegistrationRejected()
            session_is_current = await self._connection.scalar(
                select(control_plane_sessions.c.handle_digest)
                .where(*_live_session_predicates(binding))
                .with_for_update(of=control_plane_sessions)
            )
            if session_is_current is None:
                return RepositoryAttestationRegistrationRejected()
            await self._cleanup_expired_attestations()
            pending_count = await self._connection.scalar(
                select(func.count())
                .select_from(_PENDING)
                .where(
                    _PENDING.c.session_handle_digest == binding.session_handle_digest,
                    _PENDING.c.expires_at > func.statement_timestamp(),
                    not_(_same_operation(binding)),
                )
            )
            if type(pending_count) is not int:
                raise PersistenceInvariantViolation(
                    "registered-epoch attestation count is not an integer"
                )
            if pending_count >= _MAX_PENDING_ATTESTATIONS_PER_SESSION:
                return RepositoryAttestationCapacityExceeded()
            await self._connection.execute(delete(_PENDING).where(_same_operation(binding)))
            await self._connection.execute(
                insert(_PENDING).values(_attestation_transaction_to_row(transaction))
            )
            return RepositoryAttestationRegistered()
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch attestation registration failed") from error

    async def attestation_is_pending(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> bool:
        self._ensure_active()
        if type(transaction) is not RegisteredEpochAttestationTransaction:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("registered-epoch attestation must be exact")
        try:
            row = await self._connection.execute(
                select(_PENDING).where(
                    *_attestation_predicates(transaction),
                    _PENDING.c.expires_at > func.statement_timestamp(),
                )
            )
            mapping = row.mappings().one_or_none()
            if mapping is None:
                return False
            return _attestation_transaction_from_row(dict(mapping)) == transaction
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except (SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch attestation lookup failed") from error

    async def retire_attestation(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> None:
        self._ensure_active()
        if type(transaction) is not RegisteredEpochAttestationTransaction:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("registered-epoch attestation must be exact")
        try:
            await lock_repository_scope(self._connection, transaction.binding.scope)
            await self._connection.execute(
                delete(_PENDING).where(*_attestation_predicates(transaction))
            )
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch attestation retirement failed") from error

    async def resolve_operation(
        self,
        command: RegisteredEpochReviewCommand,
    ) -> RegisteredEpochReviewResolution:
        self._ensure_active()
        if type(command) is not RegisteredEpochReviewCommand:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("registered-epoch review command must be exact")
        try:
            await lock_repository_scope(self._connection, command.scope)
            existing = await self._load_by_operation(command.scope, command.operation_id)
            return _resolve_existing(existing, command)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch review operation lookup failed") from error

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self._ensure_active()
        return await self._config_epochs.load_active(scope)

    async def load_registered_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> RegisteredEpochLookup:
        self._ensure_active()
        draft = await self._config_epochs.load_epoch(scope, epoch_id)
        if draft is None:
            return None
        try:
            registrant = await self._earliest_registrant(scope, epoch_id)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch registrant lookup failed") from error
        if registrant is None:
            return RegisteredEpochWithoutReceipt()
        return RegisteredEpochSubject(draft=draft, registrant=registrant)

    async def accept(
        self,
        prepared: PreparedRegisteredEpochReview,
    ) -> RegisteredEpochReviewWriteResult:
        self._ensure_active()
        if type(prepared) is not PreparedRegisteredEpochReview:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("registered-epoch review must be exactly prepared")
        draft = prepared.draft
        command = draft.command
        try:
            await lock_repository_scope(self._connection, command.scope)
            if not await self._consume_attestation(prepared.attestation.transaction):
                return RegisteredEpochReviewAttestationConflict()
            existing = await self._load_by_operation(command.scope, command.operation_id)
            resolution = _resolve_existing(existing, command)
            if resolution is not None:
                return resolution
            active = await self._config_epochs.load_active(command.scope)
            active_pointer = None if active is None else active.active
            if active_pointer != command.expected_active:
                return RegisteredEpochReviewBaselineConflict(active=active_pointer)
            retained = await self.load_registered_epoch(command.scope, command.target_epoch_id)
            if retained != draft.subject:
                raise PersistenceInvariantViolation(
                    "registered-epoch review subject changed under the scope lock"
                )
            appended = await self._audit_events._append_pair_owned(
                prepared._take_audit_event(),
                command.scope,
            )
            if not isinstance(appended, AuditAppendAppended):
                raise PersistenceInvariantViolation(
                    "registered-epoch review audit idempotency is inconsistent with its operation"
                )
            record = RegisteredEpochReviewRecord.from_draft(
                draft,
                attestation=prepared.attestation,
                audit_event_id=appended.record.audit_event_id,
                audit_input_hash=appended.record.input_hash,
            )
            await self._connection.execute(
                insert(registered_epoch_reviews).values(_record_to_row(record))
            )
            return RegisteredEpochReviewCreated(record=record)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except EpochDraftIntegrityError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation(
                "registered-epoch review target epoch is invalid"
            ) from error
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch review registration failed") from error

    async def load_activation_candidate(
        self,
        *,
        scope: RepositoryScope,
        target_epoch_id: str,
        expected_source_hash: str,
        review_operation_id: str,
    ) -> RegisteredEpochReviewRecord | None:
        self._ensure_active()
        try:
            result = await self._connection.execute(
                select(registered_epoch_reviews).where(
                    registered_epoch_reviews.c.installation_id == scope.installation_id,
                    registered_epoch_reviews.c.repository_id == scope.repository_id,
                    registered_epoch_reviews.c.operation_id == review_operation_id,
                    registered_epoch_reviews.c.target_epoch_id == target_epoch_id,
                    registered_epoch_reviews.c.source_hash == expected_source_hash,
                    registered_epoch_reviews.c.receipt_expires_at > func.statement_timestamp(),
                )
            )
            row = result.mappings().one_or_none()
            return None if row is None else await self._record_from_row(dict(row))
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except (SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch activation receipt lookup failed") from error

    async def activate_config(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RegisteredEpochActivationAuthority,
    ) -> AttestedConfigActivationResult:
        self._ensure_active()
        if (
            type(prepared) is not PreparedConfigEpochActivation
            or type(authority) is not RegisteredEpochActivationAuthority
        ):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("registered-epoch activation inputs must be exact")
        try:
            await lock_repository_scope(self._connection, prepared.command.scope)
            replay = await self._config_epochs.resolve_operation(
                ConfigEpochReplayCommand.from_activation(prepared.command)
            )
            if replay is not None:
                return replay
            if not await self._activation_authority_is_current(prepared, authority):
                return RepositoryActivationAuthorityConflict()
            return await self._config_epochs.activate(prepared)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("registered-epoch config activation failed") from error

    async def _activation_authority_is_current(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RegisteredEpochActivationAuthority,
    ) -> bool:
        command = prepared.command
        review = authority.review
        reviewer = review.attestation.reviewer
        if (
            command.mutation_kind != "activation"
            or command.activation_version != 3
            or command.proposal_manifest_id is not None
            or command.review_operation_id != review.command.operation_id
            or command.scope != review.command.scope
            or command.target_epoch_id != review.command.target_epoch_id
            or command.expected_source_hash != review.command.expected_source_hash
            or command.authority_evidence_hash != authority.evidence_hash
            or command.authority_observed_at != _timestamp(authority.rechecked_at)
        ):
            return False
        retained = await self.load_activation_candidate(
            scope=command.scope,
            target_epoch_id=command.target_epoch_id,
            expected_source_hash=review.command.expected_source_hash,
            review_operation_id=review.command.operation_id,
        )
        if retained != review:
            return False
        expected_active = review.command.expected_active
        if command.expected_revision != (
            None if expected_active is None else expected_active.revision
        ):
            return False
        active = await self._config_epochs.load_active(command.scope)
        if (None if active is None else active.active) != expected_active:
            return False
        now = await self.current_time()
        return (
            now < reviewer.expires_at
            and authority.rechecked_at <= now
            and now < authority.rechecked_at + _ACTIVATION_RECHECK_LIFETIME
        )

    async def _consume_attestation(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> bool:
        # Only this subject's pending table is consumed; a proposal challenge never matches.
        live_session = (
            select(control_plane_sessions.c.handle_digest)
            .where(*_live_session_predicates(transaction.binding))
            .exists()
        )
        result = await self._connection.execute(
            delete(_PENDING)
            .where(
                *_attestation_predicates(transaction),
                _PENDING.c.expires_at > func.statement_timestamp(),
                live_session,
            )
            .returning(_PENDING.c.transaction_digest)
        )
        return result.scalar_one_or_none() is not None

    async def _cleanup_expired_attestations(self) -> None:
        expired = (
            select(_PENDING.c.transaction_digest)
            .where(_PENDING.c.expires_at <= func.statement_timestamp())
            .order_by(_PENDING.c.expires_at, _PENDING.c.transaction_digest)
            .limit(_EXPIRED_ATTESTATION_CLEANUP_LIMIT)
        )
        await self._connection.execute(
            delete(_PENDING).where(_PENDING.c.transaction_digest.in_(expired))
        )

    async def _earliest_registrant(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> RegisteredEpochRegistrant | None:
        result = await self._connection.execute(
            select(
                config_epoch_registrations.c.operation_id,
                config_epoch_registrations.c.audit_event_id,
                config_epoch_registrations.c.audit_input_hash,
            )
            .select_from(
                config_epoch_registrations.join(
                    audit_events,
                    audit_events.c.audit_event_id == config_epoch_registrations.c.audit_event_id,
                )
            )
            .where(
                config_epoch_registrations.c.installation_id == scope.installation_id,
                config_epoch_registrations.c.repository_id == scope.repository_id,
                config_epoch_registrations.c.epoch_id == epoch_id,
            )
            .order_by(audit_events.c.sequence)
            .limit(1)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        receipt = dict(row)
        try:
            audit_event_id = _required_string(receipt, "audit_event_id")
            event = await self._load_audit_event(
                scope,
                audit_event_id,
                CONFIG_EPOCH_REGISTRATION_EVENT_TYPE,
            )
            if event.input_hash != _required_bytes(receipt, "audit_input_hash").hex():
                raise PersistenceInvariantViolation(
                    "registration receipt and audit input hash disagree"
                )
            return RegisteredEpochRegistrant(
                operation_id=_required_string(receipt, "operation_id"),
                actor=event.actor,
                audit_event_id=audit_event_id,
            )
        except PersistenceInvariantViolation:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise PersistenceInvariantViolation("stored registration receipt is invalid") from error

    async def _load_by_operation(
        self,
        scope: RepositoryScope,
        operation_id: str,
    ) -> RegisteredEpochReviewRecord | None:
        result = await self._connection.execute(
            select(registered_epoch_reviews).where(
                registered_epoch_reviews.c.installation_id == scope.installation_id,
                registered_epoch_reviews.c.repository_id == scope.repository_id,
                registered_epoch_reviews.c.operation_id == operation_id,
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else await self._record_from_row(dict(row))

    async def _record_from_row(self, row: Mapping[str, object]) -> RegisteredEpochReviewRecord:
        try:
            scope = RepositoryScope(
                _required_int(row, "installation_id"),
                _required_int(row, "repository_id"),
            )
            audit_event_id = _required_string(row, "audit_event_id")
            event = await self._load_audit_event(
                scope,
                audit_event_id,
                REGISTERED_EPOCH_REVIEW_ACCEPTED_EVENT_TYPE,
            )
            target_epoch_id = _required_string(row, "target_epoch_id")
            subject = await self.load_registered_epoch(scope, target_epoch_id)
            if type(
                subject
            ) is not RegisteredEpochSubject or subject.registrant.operation_id != _required_string(
                row, "registration_operation_id"
            ):
                raise PersistenceInvariantViolation(
                    "registered-epoch review registrant is inconsistent"
                )
            command = RegisteredEpochReviewCommand(
                scope=scope,
                operation_id=_required_string(row, "operation_id"),
                target_epoch_id=target_epoch_id,
                expected_source_hash=_required_string(row, "source_hash"),
                expected_active=_active_from_row(scope, row),
                actor=event.actor,
            )
            record = RegisteredEpochReviewRecord(
                command=command,
                provider_revision=_required_string(row, "provider_revision"),
                inventory_digest=_required_string(row, "inventory_digest"),
                registrant=subject.registrant,
                semantic_diff=decode_policy_semantic_diff(
                    _required_bytes(row, "semantic_diff_canonical_json")
                ),
                attestation=_attestation_evidence_from_review_row(row, command),
                audit_event_id=audit_event_id,
                audit_input_hash=_required_bytes(row, "audit_input_hash").hex(),
            )
            _require_row_evidence(row, record)
            retained = prepare_registered_epoch_review(
                RegisteredEpochReviewDraft(
                    command=command,
                    provider_revision=record.provider_revision,
                    inventory_digest=record.inventory_digest,
                    subject=subject,
                    semantic_diff=record.semantic_diff,
                ),
                attestation=record.attestation,
                occurred_at=event.created_at,
            )
            if (
                event.input_hash != record.audit_input_hash
                or retained.audit_input_hash != record.audit_input_hash
            ):
                raise PersistenceInvariantViolation(
                    "registered-epoch review row and audit event are inconsistent"
                )
            return record
        except PersistenceInvariantViolation:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise PersistenceInvariantViolation(
                "stored registered-epoch review is invalid"
            ) from error

    async def _load_audit_event(
        self,
        scope: RepositoryScope,
        audit_event_id: str,
        event_type: str,
    ) -> AuditEventRecord:
        result = await self._connection.execute(
            select(audit_events).where(audit_events.c.audit_event_id == audit_event_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise PersistenceInvariantViolation("registered-epoch review audit event is missing")
        event = row_to_record(dict(row))
        if (
            event.audit_event_id != audit_event_id
            or event.installation_id != scope.installation_id
            or event.repository_id != scope.repository_id
            or event.event_type != event_type
        ):
            raise PersistenceInvariantViolation(
                "registered-epoch review audit event is inconsistent"
            )
        return event


def _resolve_existing(
    existing: RegisteredEpochReviewRecord | None,
    command: RegisteredEpochReviewCommand,
) -> RegisteredEpochReviewResolution:
    if existing is None:
        return None
    if existing.command == command:
        return RegisteredEpochReviewDuplicate(record=existing)
    return RegisteredEpochReviewOperationConflict(existing=existing)


def _record_to_row(record: RegisteredEpochReviewRecord) -> dict[str, object]:
    command = record.command
    active = command.expected_active
    diff = record.semantic_diff
    attestation = record.attestation
    binding = attestation.transaction.binding
    reviewer = attestation.reviewer
    return {
        "installation_id": command.scope.installation_id,
        "repository_id": command.scope.repository_id,
        "operation_id": command.operation_id,
        "target_epoch_id": command.target_epoch_id,
        "source_hash": command.expected_source_hash,
        "registration_operation_id": record.registrant.operation_id,
        "provider_revision": record.provider_revision,
        "inventory_digest": record.inventory_digest,
        "base_epoch_id": None if active is None else active.epoch_id,
        "base_revision": None if active is None else active.revision,
        "semantic_diff_version": diff.version,
        "semantic_diff_canonical_json": diff.canonical_bytes,
        "semantic_diff_hash": diff.diff_hash,
        "changed_pointer_count": len(diff.changed_pointers),
        "attestation_transaction_digest": attestation.transaction.transaction_digest,
        "session_handle_digest": binding.session_handle_digest,
        "initiating_actor": command.actor,
        "reviewer_user_id": reviewer.user_id,
        "reviewer_login": reviewer.login,
        "reviewer_permission": reviewer.permission,
        "attestation_issued_at": binding.issued_at,
        "permission_observed_at": reviewer.observed_at,
        "receipt_expires_at": reviewer.expires_at,
        "authority_profile_digest": binding.authority_profile_digest,
        "audit_event_id": record.audit_event_id,
        "audit_input_hash": bytes.fromhex(record.audit_input_hash),
    }


def _active_from_row(
    scope: RepositoryScope,
    row: Mapping[str, object],
) -> ActiveConfigEpoch | None:
    epoch_id = _optional_string(row, "base_epoch_id")
    revision = _optional_int(row, "base_revision")
    if epoch_id is None and revision is None:
        return None
    if epoch_id is None or revision is None:
        raise PersistenceInvariantViolation("registered-epoch review baseline shape is invalid")
    return ActiveConfigEpoch(scope=scope, epoch_id=epoch_id, revision=revision)


def _require_row_evidence(row: Mapping[str, object], record: RegisteredEpochReviewRecord) -> None:
    if (
        _required_string(row, "semantic_diff_version") != record.semantic_diff.version
        or _required_string(row, "semantic_diff_hash") != record.semantic_diff.diff_hash
        or _required_int(row, "changed_pointer_count") != len(record.semantic_diff.changed_pointers)
    ):
        raise PersistenceInvariantViolation("registered-epoch review diff evidence is inconsistent")


def _live_session_predicates(
    binding: RegisteredEpochStepUpBinding,
) -> tuple[ColumnElement[bool], ...]:
    return (
        control_plane_sessions.c.handle_digest == binding.session_handle_digest,
        control_plane_sessions.c.actor_id == binding.initiating_actor,
        control_plane_sessions.c.profile_digest == binding.authority_profile_digest,
        control_plane_sessions.c.issued_at <= func.statement_timestamp(),
        control_plane_sessions.c.expires_at > func.statement_timestamp(),
    )


def _same_operation(binding: RegisteredEpochStepUpBinding) -> ColumnElement[bool]:
    return and_(
        _PENDING.c.installation_id == binding.scope.installation_id,
        _PENDING.c.repository_id == binding.scope.repository_id,
        _PENDING.c.operation_id == binding.operation_id,
    )


def _attestation_transaction_to_row(
    transaction: RegisteredEpochAttestationTransaction,
) -> dict[str, object]:
    binding = transaction.binding
    return {
        "transaction_digest": transaction.transaction_digest,
        "session_handle_digest": binding.session_handle_digest,
        "installation_id": binding.scope.installation_id,
        "repository_id": binding.scope.repository_id,
        "operation_id": binding.operation_id,
        "target_epoch_id": binding.target_epoch_id,
        "source_hash": binding.source_hash,
        "provider_revision": binding.revision,
        "inventory_digest": binding.inventory_digest,
        "expected_active_epoch_id": binding.expected_active_epoch_id,
        "expected_active_revision": binding.expected_active_revision,
        "initiating_actor": binding.initiating_actor,
        "authority_profile_digest": binding.authority_profile_digest,
        "issued_at": binding.issued_at,
        "expires_at": binding.expires_at,
    }


def _attestation_predicates(
    transaction: RegisteredEpochAttestationTransaction,
) -> tuple[ColumnElement[bool], ...]:
    binding = transaction.binding
    table = _PENDING.c
    return (
        table.transaction_digest == transaction.transaction_digest,
        table.session_handle_digest == binding.session_handle_digest,
        table.installation_id == binding.scope.installation_id,
        table.repository_id == binding.scope.repository_id,
        table.operation_id == binding.operation_id,
        table.target_epoch_id == binding.target_epoch_id,
        table.source_hash == binding.source_hash,
        table.provider_revision == binding.revision,
        table.inventory_digest == binding.inventory_digest,
        table.expected_active_epoch_id == binding.expected_active_epoch_id,
        table.expected_active_revision == binding.expected_active_revision,
        table.initiating_actor == binding.initiating_actor,
        table.authority_profile_digest == binding.authority_profile_digest,
        table.issued_at == binding.issued_at,
        table.expires_at == binding.expires_at,
    )


def _attestation_transaction_from_row(
    row: Mapping[str, object],
) -> RegisteredEpochAttestationTransaction:
    return RegisteredEpochAttestationTransaction(
        transaction_digest=_required_bytes(row, "transaction_digest"),
        binding=RegisteredEpochStepUpBinding(
            session_handle_digest=_required_bytes(row, "session_handle_digest"),
            initiating_actor=_required_string(row, "initiating_actor"),
            scope=RepositoryScope(
                _required_int(row, "installation_id"),
                _required_int(row, "repository_id"),
            ),
            operation_id=_required_string(row, "operation_id"),
            target_epoch_id=_required_string(row, "target_epoch_id"),
            source_hash=_required_string(row, "source_hash"),
            revision=_required_string(row, "provider_revision"),
            inventory_digest=_required_string(row, "inventory_digest"),
            expected_active_epoch_id=_optional_string(row, "expected_active_epoch_id"),
            expected_active_revision=_optional_int(row, "expected_active_revision"),
            authority_profile_digest=_required_string(row, "authority_profile_digest"),
            issued_at=_required_datetime(row, "issued_at"),
            expires_at=_required_datetime(row, "expires_at"),
        ),
    )


def _attestation_evidence_from_review_row(
    row: Mapping[str, object],
    command: RegisteredEpochReviewCommand,
) -> RegisteredEpochAttestationEvidence:
    issued_at = _required_datetime(row, "attestation_issued_at")
    expires_at = _required_datetime(row, "receipt_expires_at")
    active = command.expected_active
    return RegisteredEpochAttestationEvidence(
        transaction=RegisteredEpochAttestationTransaction(
            transaction_digest=_required_bytes(row, "attestation_transaction_digest"),
            binding=RegisteredEpochStepUpBinding(
                session_handle_digest=_required_bytes(row, "session_handle_digest"),
                initiating_actor=_required_string(row, "initiating_actor"),
                scope=command.scope,
                operation_id=command.operation_id,
                target_epoch_id=command.target_epoch_id,
                source_hash=command.expected_source_hash,
                revision=_required_string(row, "provider_revision"),
                inventory_digest=_required_string(row, "inventory_digest"),
                expected_active_epoch_id=None if active is None else active.epoch_id,
                expected_active_revision=None if active is None else active.revision,
                authority_profile_digest=_required_string(row, "authority_profile_digest"),
                issued_at=issued_at,
                expires_at=expires_at,
            ),
        ),
        reviewer=GitHubReviewerPrincipal(
            evidence=GitHubReviewerEvidence(
                user_id=_required_int(row, "reviewer_user_id"),
                login=_required_string(row, "reviewer_login"),
                permission=_reviewer_permission(row),
            ),
            observed_at=_required_datetime(row, "permission_observed_at"),
            expires_at=expires_at,
        ),
    )


def _reviewer_permission(row: Mapping[str, object]) -> ReviewerPermission:
    value = _required_string(row, "reviewer_permission")
    if value == "admin":
        return "admin"
    if value == "maintain":
        return "maintain"
    raise TypeError("reviewer permission is not admitted")


def _required_datetime(row: Mapping[str, object], key: str) -> datetime:
    return _aware_utc(row[key], key)


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} is not an aware datetime")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
