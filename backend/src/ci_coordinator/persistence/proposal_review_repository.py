"""Atomic PostgreSQL registration of one owner-reviewed workflow proposal."""

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
    ActiveConfigEpoch,
    ActiveConfigEpochSnapshot,
    ConfigEpochRegistrationConflict,
    ConfigEpochRegistrationCreated,
    PreparedConfigEpochActivation,
)
from ci_coordinator.control_plane_identity import (
    GitHubReviewerEvidence,
    GitHubReviewerPrincipal,
    ReviewerPermission,
    ReviewerStepUpBinding,
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
    control_plane_sessions,
    repository_attestation_transactions,
    workflow_proposal_reviews,
)
from ci_coordinator.proposal_review import (
    PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE,
    AttestedConfigActivationResult,
    PreparedProposalReview,
    ProposalReviewAttestationConflict,
    ProposalReviewBaselineConflict,
    ProposalReviewCommand,
    ProposalReviewCreated,
    ProposalReviewDraft,
    ProposalReviewDuplicate,
    ProposalReviewEpochConflict,
    ProposalReviewOperationConflict,
    ProposalReviewRecord,
    ProposalReviewResolution,
    ProposalReviewWriteResult,
    RepositoryActivationAuthority,
    RepositoryActivationAuthorityConflict,
    RepositoryAttestationCapacityExceeded,
    RepositoryAttestationEvidence,
    RepositoryAttestationRegistered,
    RepositoryAttestationRegistration,
    RepositoryAttestationRegistrationRejected,
    RepositoryAttestationTransaction,
    decode_policy_semantic_diff,
    prepare_proposal_review,
)

_MAX_PENDING_ATTESTATIONS_PER_SESSION = 8
_EXPIRED_ATTESTATION_CLEANUP_LIMIT = 128
_ACTIVATION_RECHECK_LIFETIME = timedelta(seconds=30)


class _PostgresProposalReviewRepository:
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

    async def resolve_operation(
        self,
        command: ProposalReviewCommand,
    ) -> ProposalReviewResolution:
        self._ensure_active()
        if type(command) is not ProposalReviewCommand:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("proposal review command must be exact")
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
            raise StoreUnavailable("proposal review operation lookup failed") from error

    async def current_time(self) -> datetime:
        self._ensure_active()
        try:
            value = await self._connection.scalar(select(func.statement_timestamp()))
            return _aware_utc(value, "proposal review database clock")
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise StoreUnavailable("proposal review database clock failed") from error

    async def register_attestation(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> RepositoryAttestationRegistration:
        self._ensure_active()
        if type(transaction) is not RepositoryAttestationTransaction:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("repository attestation must be exact")
        binding = transaction.binding
        try:
            await lock_repository_scope(self._connection, binding.scope)
            now = await self.current_time()
            if not binding.issued_at <= now < binding.expires_at:
                return RepositoryAttestationRegistrationRejected()
            session_is_current = await self._connection.scalar(
                select(control_plane_sessions.c.handle_digest)
                .where(
                    control_plane_sessions.c.handle_digest == binding.session_handle_digest,
                    control_plane_sessions.c.actor_id == binding.initiating_actor,
                    control_plane_sessions.c.profile_digest == binding.authority_profile_digest,
                    control_plane_sessions.c.issued_at <= func.statement_timestamp(),
                    control_plane_sessions.c.expires_at > func.statement_timestamp(),
                )
                .with_for_update(of=control_plane_sessions)
            )
            if session_is_current is None:
                return RepositoryAttestationRegistrationRejected()
            await _cleanup_expired_attestations(self._connection)
            pending_count = await self._connection.scalar(
                select(func.count())
                .select_from(repository_attestation_transactions)
                .where(
                    repository_attestation_transactions.c.session_handle_digest
                    == binding.session_handle_digest,
                    repository_attestation_transactions.c.expires_at > func.statement_timestamp(),
                    not_(
                        and_(
                            repository_attestation_transactions.c.installation_id
                            == binding.scope.installation_id,
                            repository_attestation_transactions.c.repository_id
                            == binding.scope.repository_id,
                            repository_attestation_transactions.c.operation_id
                            == binding.operation_id,
                        )
                    ),
                )
            )
            if type(pending_count) is not int:
                raise PersistenceInvariantViolation(
                    "repository attestation count is not an integer"
                )
            if pending_count >= _MAX_PENDING_ATTESTATIONS_PER_SESSION:
                return RepositoryAttestationCapacityExceeded()
            await self._connection.execute(
                delete(repository_attestation_transactions).where(
                    repository_attestation_transactions.c.installation_id
                    == binding.scope.installation_id,
                    repository_attestation_transactions.c.repository_id
                    == binding.scope.repository_id,
                    repository_attestation_transactions.c.operation_id == binding.operation_id,
                )
            )
            await self._connection.execute(
                insert(repository_attestation_transactions).values(
                    _attestation_transaction_to_row(transaction)
                )
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
            raise StoreUnavailable("repository attestation registration failed") from error

    async def attestation_is_pending(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> bool:
        self._ensure_active()
        if type(transaction) is not RepositoryAttestationTransaction:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("repository attestation must be exact")
        try:
            row = await self._connection.execute(
                select(repository_attestation_transactions).where(
                    *_attestation_predicates(transaction),
                    repository_attestation_transactions.c.expires_at > func.statement_timestamp(),
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
            raise StoreUnavailable("repository attestation lookup failed") from error

    async def retire_attestation(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> None:
        self._ensure_active()
        if type(transaction) is not RepositoryAttestationTransaction:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("repository attestation must be exact")
        try:
            await lock_repository_scope(self._connection, transaction.binding.scope)
            await self._connection.execute(
                delete(repository_attestation_transactions).where(
                    *_attestation_predicates(transaction)
                )
            )
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("repository attestation retirement failed") from error

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self._ensure_active()
        return await self._config_epochs.load_active(scope)

    async def load_activation_candidate(
        self,
        *,
        scope: RepositoryScope,
        target_epoch_id: str,
        proposal_manifest_id: str,
    ) -> ProposalReviewRecord | None:
        self._ensure_active()
        try:
            result = await self._connection.execute(
                select(workflow_proposal_reviews).where(
                    workflow_proposal_reviews.c.installation_id == scope.installation_id,
                    workflow_proposal_reviews.c.repository_id == scope.repository_id,
                    workflow_proposal_reviews.c.target_epoch_id == target_epoch_id,
                    workflow_proposal_reviews.c.proposal_manifest_id == proposal_manifest_id,
                    workflow_proposal_reviews.c.receipt_expires_at > func.statement_timestamp(),
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
            raise StoreUnavailable("repository activation receipt lookup failed") from error

    async def activate_config(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RepositoryActivationAuthority,
    ) -> AttestedConfigActivationResult:
        self._ensure_active()
        if (
            type(prepared) is not PreparedConfigEpochActivation
            or type(authority) is not RepositoryActivationAuthority
        ):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("repository activation inputs must be exact")
        try:
            await lock_repository_scope(self._connection, prepared.command.scope)
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
            raise StoreUnavailable("attested config activation failed") from error

    async def accept(self, prepared: PreparedProposalReview) -> ProposalReviewWriteResult:
        self._ensure_active()
        if type(prepared) is not PreparedProposalReview:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("proposal review must be exactly prepared")
        draft = prepared.draft
        command = draft.command
        try:
            await lock_repository_scope(self._connection, command.scope)
            if not await self._consume_attestation(prepared.attestation.transaction):
                return ProposalReviewAttestationConflict()
            existing = await self._load_by_operation(command.scope, command.operation_id)
            resolution = _resolve_existing(existing, command)
            if resolution is not None:
                return resolution

            reviewed_manifest = await self._load_by_manifest(
                command.scope,
                command.expected_manifest_id,
            )
            if reviewed_manifest is not None:
                return ProposalReviewDuplicate(record=reviewed_manifest)

            active = await self._config_epochs.load_active(command.scope)
            active_pointer = None if active is None else active.active
            if active_pointer != command.expected_active:
                return ProposalReviewBaselineConflict(active=active_pointer)

            registration = await self._config_epochs.register(draft.target)
            if isinstance(registration, ConfigEpochRegistrationConflict):
                return ProposalReviewEpochConflict(epoch_id=registration.epoch_id)
            epoch_created = isinstance(registration, ConfigEpochRegistrationCreated)

            appended = await self._audit_events._append_pair_owned(
                prepared._take_audit_event(),
                command.scope,
            )
            if not isinstance(appended, AuditAppendAppended):
                raise PersistenceInvariantViolation(
                    "proposal review audit idempotency is inconsistent with its operation"
                )
            record = ProposalReviewRecord.from_draft(
                draft,
                attestation=prepared.attestation,
                audit_event_id=appended.record.audit_event_id,
                audit_input_hash=appended.record.input_hash,
            )
            await self._connection.execute(
                insert(workflow_proposal_reviews).values(_record_to_row(record))
            )
            return ProposalReviewCreated(record=record, epoch_created=epoch_created)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except EpochDraftIntegrityError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation(
                "proposal review target epoch is invalid"
            ) from error
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("proposal review registration failed") from error

    async def _consume_attestation(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> bool:
        binding = transaction.binding
        live_session = (
            select(control_plane_sessions.c.handle_digest)
            .where(
                control_plane_sessions.c.handle_digest == binding.session_handle_digest,
                control_plane_sessions.c.actor_id == binding.initiating_actor,
                control_plane_sessions.c.profile_digest == binding.authority_profile_digest,
                control_plane_sessions.c.issued_at <= func.statement_timestamp(),
                control_plane_sessions.c.expires_at > func.statement_timestamp(),
            )
            .exists()
        )
        result = await self._connection.execute(
            delete(repository_attestation_transactions)
            .where(
                *_attestation_predicates(transaction),
                repository_attestation_transactions.c.expires_at > func.statement_timestamp(),
                live_session,
            )
            .returning(repository_attestation_transactions.c.transaction_digest)
        )
        consumed = result.scalar_one_or_none()
        return consumed is not None

    async def _activation_authority_is_current(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RepositoryActivationAuthority,
    ) -> bool:
        command = prepared.command
        review = authority.review
        reviewer = review.attestation.reviewer
        if (
            command.mutation_kind != "activation"
            or command.scope != review.command.scope
            or command.target_epoch_id != review.target_epoch_id
            or command.proposal_manifest_id != review.command.expected_manifest_id
            or command.authority_evidence_hash != authority.evidence_hash
            or command.authority_observed_at != _timestamp(authority.rechecked_at)
        ):
            return False
        retained = await self.load_activation_candidate(
            scope=command.scope,
            target_epoch_id=command.target_epoch_id,
            proposal_manifest_id=command.proposal_manifest_id,
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

    async def _load_by_operation(
        self,
        scope: RepositoryScope,
        operation_id: str,
    ) -> ProposalReviewRecord | None:
        result = await self._connection.execute(
            select(workflow_proposal_reviews).where(
                workflow_proposal_reviews.c.installation_id == scope.installation_id,
                workflow_proposal_reviews.c.repository_id == scope.repository_id,
                workflow_proposal_reviews.c.operation_id == operation_id,
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else await self._record_from_row(dict(row))

    async def _load_by_manifest(
        self,
        scope: RepositoryScope,
        manifest_id: str,
    ) -> ProposalReviewRecord | None:
        result = await self._connection.execute(
            select(workflow_proposal_reviews).where(
                workflow_proposal_reviews.c.installation_id == scope.installation_id,
                workflow_proposal_reviews.c.repository_id == scope.repository_id,
                workflow_proposal_reviews.c.proposal_manifest_id == manifest_id,
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else await self._record_from_row(dict(row))

    async def _record_from_row(self, row: Mapping[str, object]) -> ProposalReviewRecord:
        try:
            scope = RepositoryScope(
                _required_int(row, "installation_id"),
                _required_int(row, "repository_id"),
            )
            operation_id = _required_string(row, "operation_id")
            audit_event_id = _required_string(row, "audit_event_id")
            event = await self._load_audit_event(scope, audit_event_id)
            expected_active = _active_from_row(scope, row)
            semantic_diff = decode_policy_semantic_diff(
                _required_bytes(row, "semantic_diff_canonical_json")
            )
            record = ProposalReviewRecord(
                command=ProposalReviewCommand(
                    scope=scope,
                    operation_id=operation_id,
                    expected_manifest_id=_required_string(row, "proposal_manifest_id"),
                    expected_active=expected_active,
                    actor=event.actor,
                ),
                provider_revision=_required_string(row, "provider_revision"),
                inventory_digest=_required_string(row, "inventory_digest"),
                proposal_digest=_required_string(row, "proposal_digest"),
                target_epoch_id=_required_string(row, "target_epoch_id"),
                semantic_diff=semantic_diff,
                attestation=_attestation_evidence_from_review_row(row),
                audit_event_id=audit_event_id,
                audit_input_hash=_required_bytes(row, "audit_input_hash").hex(),
            )
            _require_row_evidence(row, record)
            target = await self._config_epochs.load_epoch(scope, record.target_epoch_id)
            if target is None:
                raise PersistenceInvariantViolation("proposal review target epoch is missing")
            retained = prepare_proposal_review(
                ProposalReviewDraft(
                    command=record.command,
                    provider_revision=record.provider_revision,
                    inventory_digest=record.inventory_digest,
                    proposal_digest=record.proposal_digest,
                    target=target,
                    semantic_diff=record.semantic_diff,
                ),
                attestation=record.attestation,
                occurred_at=event.created_at,
            )
            if (
                event.audit_event_id != record.audit_event_id
                or event.input_hash != record.audit_input_hash
                or retained.audit_input_hash != record.audit_input_hash
            ):
                raise PersistenceInvariantViolation(
                    "proposal review row and audit event are inconsistent"
                )
            return record
        except PersistenceInvariantViolation:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise PersistenceInvariantViolation("stored proposal review is invalid") from error

    async def _load_audit_event(
        self,
        scope: RepositoryScope,
        audit_event_id: str,
    ) -> AuditEventRecord:
        result = await self._connection.execute(
            select(audit_events).where(audit_events.c.audit_event_id == audit_event_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise PersistenceInvariantViolation("proposal review audit event is missing")
        event = row_to_record(dict(row))
        if (
            event.audit_event_id != audit_event_id
            or event.installation_id != scope.installation_id
            or event.repository_id != scope.repository_id
            or event.event_type != PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE
        ):
            raise PersistenceInvariantViolation("proposal review audit event is inconsistent")
        return event


def _resolve_existing(
    existing: ProposalReviewRecord | None,
    command: ProposalReviewCommand,
) -> ProposalReviewResolution:
    if existing is None:
        return None
    if existing.command == command:
        return ProposalReviewDuplicate(record=existing)
    return ProposalReviewOperationConflict(existing=existing)


def _record_to_row(record: ProposalReviewRecord) -> dict[str, object]:
    active = record.command.expected_active
    diff = record.semantic_diff
    return {
        "installation_id": record.command.scope.installation_id,
        "repository_id": record.command.scope.repository_id,
        "operation_id": record.command.operation_id,
        "proposal_manifest_id": record.command.expected_manifest_id,
        "provider_revision": record.provider_revision,
        "inventory_digest": record.inventory_digest,
        "proposal_digest": record.proposal_digest,
        "base_epoch_id": None if active is None else active.epoch_id,
        "base_revision": None if active is None else active.revision,
        "target_epoch_id": record.target_epoch_id,
        "semantic_diff_version": diff.version,
        "semantic_diff_canonical_json": diff.canonical_bytes,
        "semantic_diff_hash": diff.diff_hash,
        "changed_pointer_count": len(diff.changed_pointers),
        "attestation_transaction_digest": record.attestation.transaction.transaction_digest,
        "session_handle_digest": record.attestation.transaction.binding.session_handle_digest,
        "initiating_actor": record.command.actor,
        "reviewer_user_id": record.attestation.reviewer.user_id,
        "reviewer_login": record.attestation.reviewer.login,
        "reviewer_permission": record.attestation.reviewer.permission,
        "attestation_issued_at": record.attestation.transaction.binding.issued_at,
        "permission_observed_at": record.attestation.reviewer.observed_at,
        "receipt_expires_at": record.attestation.reviewer.expires_at,
        "authority_profile_digest": (
            record.attestation.transaction.binding.authority_profile_digest
        ),
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
        raise PersistenceInvariantViolation("proposal review baseline shape is invalid")
    return ActiveConfigEpoch(scope=scope, epoch_id=epoch_id, revision=revision)


def _require_row_evidence(
    row: Mapping[str, object],
    record: ProposalReviewRecord,
) -> None:
    if (
        _required_string(row, "semantic_diff_version") != record.semantic_diff.version
        or _required_string(row, "semantic_diff_hash") != record.semantic_diff.diff_hash
        or _required_int(row, "changed_pointer_count") != len(record.semantic_diff.changed_pointers)
    ):
        raise PersistenceInvariantViolation("proposal review diff evidence is inconsistent")


def _attestation_transaction_to_row(
    transaction: RepositoryAttestationTransaction,
) -> dict[str, object]:
    binding = transaction.binding
    return {
        "transaction_digest": transaction.transaction_digest,
        "session_handle_digest": binding.session_handle_digest,
        "installation_id": binding.scope.installation_id,
        "repository_id": binding.scope.repository_id,
        "operation_id": binding.operation_id,
        "proposal_manifest_id": binding.proposal_manifest_id,
        "provider_revision": binding.revision,
        "proposal_digest": binding.proposal_digest,
        "expected_active_epoch_id": binding.expected_active_epoch_id,
        "expected_active_revision": binding.expected_active_revision,
        "initiating_actor": binding.initiating_actor,
        "authority_profile_digest": binding.authority_profile_digest,
        "issued_at": binding.issued_at,
        "expires_at": binding.expires_at,
    }


def _attestation_predicates(
    transaction: RepositoryAttestationTransaction,
) -> tuple[ColumnElement[bool], ...]:
    binding = transaction.binding
    table = repository_attestation_transactions.c
    return (
        table.transaction_digest == transaction.transaction_digest,
        table.session_handle_digest == binding.session_handle_digest,
        table.installation_id == binding.scope.installation_id,
        table.repository_id == binding.scope.repository_id,
        table.operation_id == binding.operation_id,
        table.proposal_manifest_id == binding.proposal_manifest_id,
        table.provider_revision == binding.revision,
        table.proposal_digest == binding.proposal_digest,
        table.expected_active_epoch_id == binding.expected_active_epoch_id,
        table.expected_active_revision == binding.expected_active_revision,
        table.initiating_actor == binding.initiating_actor,
        table.authority_profile_digest == binding.authority_profile_digest,
        table.issued_at == binding.issued_at,
        table.expires_at == binding.expires_at,
    )


def _attestation_transaction_from_row(
    row: Mapping[str, object],
) -> RepositoryAttestationTransaction:
    scope = RepositoryScope(
        _required_int(row, "installation_id"),
        _required_int(row, "repository_id"),
    )
    return RepositoryAttestationTransaction(
        transaction_digest=_required_bytes(row, "transaction_digest"),
        binding=ReviewerStepUpBinding(
            session_handle_digest=_required_bytes(row, "session_handle_digest"),
            initiating_actor=_required_string(row, "initiating_actor"),
            scope=scope,
            operation_id=_required_string(row, "operation_id"),
            proposal_manifest_id=_required_string(row, "proposal_manifest_id"),
            revision=_required_string(row, "provider_revision"),
            proposal_digest=_required_string(row, "proposal_digest"),
            expected_active_epoch_id=_optional_string(row, "expected_active_epoch_id"),
            expected_active_revision=_optional_int(row, "expected_active_revision"),
            authority_profile_digest=_required_string(row, "authority_profile_digest"),
            issued_at=_required_datetime(row, "issued_at"),
            expires_at=_required_datetime(row, "expires_at"),
        ),
    )


def _attestation_evidence_from_review_row(
    row: Mapping[str, object],
) -> RepositoryAttestationEvidence:
    scope = RepositoryScope(
        _required_int(row, "installation_id"),
        _required_int(row, "repository_id"),
    )
    issued_at = _required_datetime(row, "attestation_issued_at")
    observed_at = _required_datetime(row, "permission_observed_at")
    expires_at = _required_datetime(row, "receipt_expires_at")
    return RepositoryAttestationEvidence(
        transaction=RepositoryAttestationTransaction(
            transaction_digest=_required_bytes(row, "attestation_transaction_digest"),
            binding=ReviewerStepUpBinding(
                session_handle_digest=_required_bytes(row, "session_handle_digest"),
                initiating_actor=_required_string(row, "initiating_actor"),
                scope=scope,
                operation_id=_required_string(row, "operation_id"),
                proposal_manifest_id=_required_string(row, "proposal_manifest_id"),
                revision=_required_string(row, "provider_revision"),
                proposal_digest=_required_string(row, "proposal_digest"),
                expected_active_epoch_id=_optional_string(row, "base_epoch_id"),
                expected_active_revision=_optional_int(row, "base_revision"),
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
            observed_at=observed_at,
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
    value = row[key]
    return _aware_utc(value, key)


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} is not an aware datetime")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _cleanup_expired_attestations(connection: AsyncConnection) -> None:
    expired = (
        select(repository_attestation_transactions.c.transaction_digest)
        .where(repository_attestation_transactions.c.expires_at <= func.statement_timestamp())
        .order_by(
            repository_attestation_transactions.c.expires_at,
            repository_attestation_transactions.c.transaction_digest,
        )
        .limit(_EXPIRED_ATTESTATION_CLEANUP_LIMIT)
    )
    await connection.execute(
        delete(repository_attestation_transactions).where(
            repository_attestation_transactions.c.transaction_digest.in_(expired)
        )
    )
