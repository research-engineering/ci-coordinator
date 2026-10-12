"""Native PostgreSQL witnesses for registered-epoch review and activation (CI-018)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import Table, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ConfigEpochActivationApplied,
    ConfigEpochActivationCommand,
    ConfigEpochActivationDuplicate,
    ConfigEpochActivationOperationConflict,
    ConfigEpochRegistrationCommand,
    ConfigEpochRegistrationCommitted,
    prepare_config_epoch_activation,
    prepare_config_epoch_registration,
)
from ci_coordinator.control_plane_identity import (
    GitHubReviewerEvidence,
    GitHubReviewerPrincipal,
    RegisteredEpochStepUpBinding,
)
from ci_coordinator.persistence import (
    PersistenceInvariantViolation,
    PostgresConfigEpochUnitOfWork,
    PostgresUnitOfWork,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.proposal_review_unit_of_work import (
    PostgresProposalReviewUnitOfWork,
)
from ci_coordinator.persistence.registered_epoch_review_unit_of_work import (
    PostgresRegisteredEpochReviewUnitOfWork,
)
from ci_coordinator.persistence.schema import active_config_epochs, registered_epoch_reviews
from ci_coordinator.proposal_review import (
    PreparedRegisteredEpochReview,
    ProposalReviewCreated,
    RegisteredEpochActivationAuthority,
    RegisteredEpochAttestationEvidence,
    RegisteredEpochAttestationTransaction,
    RegisteredEpochRegistrant,
    RegisteredEpochReviewAttestationConflict,
    RegisteredEpochReviewBaselineConflict,
    RegisteredEpochReviewCommand,
    RegisteredEpochReviewCreated,
    RegisteredEpochReviewDraft,
    RegisteredEpochReviewDuplicate,
    RegisteredEpochReviewOperationConflict,
    RegisteredEpochSubject,
    RegisteredEpochWithoutReceipt,
    RepositoryActivationAuthorityConflict,
    RepositoryAttestationRegistered,
    compare_policy_drafts,
    prepare_registered_epoch_review,
)

from .conftest import RUNTIME_ROLE
from .test_proposal_review_registration import (
    _ACTOR,
    _PROFILE_DIGEST,
    _SESSION_DIGEST,
    SCOPE,
    _draft,
    _ensure_control_plane_session,
    _prepare,
    _register_attestation,
    _review,
)

pytestmark = pytest.mark.persistence

_REGISTRANT = "keycloak-workload:v1:" + "7" * 64
_LATER_REGISTRANT = "keycloak-workload:v1:" + "8" * 64
_REVISION = "b" * 40
_INVENTORY = "c" * 64


def test_lookup_keeps_absent_foreign_receiptless_and_registered_epochs_distinct(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        registered = _draft("registered-lookup")
        proposal_created = _draft("proposal-created-lookup")
        try:
            await _register_standalone(engine, registered, "register-first", _REGISTRANT)
            await _register_standalone(engine, registered, "register-later", _LATER_REGISTRANT)
            proposal = _review(target=proposal_created)
            evidence = await _register_attestation(engine, proposal)
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                accepted = await transaction.proposal_reviews.accept(
                    _prepare(proposal, attestation=evidence, occurred_at="2026-10-11T10:00:00.000Z")
                )
                assert isinstance(accepted, ProposalReviewCreated)
                await transaction.commit()

            async with PostgresRegisteredEpochReviewUnitOfWork(engine) as transaction:
                reviews = transaction.registered_epoch_reviews
                found = await reviews.load_registered_epoch(SCOPE, registered.epoch_id)
                assert isinstance(found, RegisteredEpochSubject)
                assert found.draft == registered
                assert found.registrant.operation_id == "register-first"
                assert found.registrant.actor == _REGISTRANT
                assert await reviews.load_registered_epoch(SCOPE, "0" * 64) is None
                foreign = RepositoryScope(SCOPE.installation_id + 1, SCOPE.repository_id)
                assert await reviews.load_registered_epoch(foreign, registered.epoch_id) is None
                assert isinstance(
                    await reviews.load_registered_epoch(SCOPE, proposal_created.epoch_id),
                    RegisteredEpochWithoutReceipt,
                )
                await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_accept_retains_one_review_without_activation_and_replays_exactly(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        target = _draft("registered-accept")
        try:
            subject = await _registered_subject(runtime, target)
            command = _command(target)
            evidence = await _register_registered_attestation(runtime, command)
            async with PostgresRegisteredEpochReviewUnitOfWork(runtime) as transaction:
                result = await transaction.registered_epoch_reviews.accept(
                    _prepared(command, subject, evidence)
                )
                assert isinstance(result, RegisteredEpochReviewCreated)
                assert result.record.registrant == subject.registrant
                await transaction.commit()

            async with PostgresRegisteredEpochReviewUnitOfWork(runtime) as transaction:
                reviews = transaction.registered_epoch_reviews
                replay = await reviews.resolve_operation(command)
                assert isinstance(replay, RegisteredEpochReviewDuplicate)
                assert replay.record == result.record
                changed = await reviews.resolve_operation(
                    replace(command, expected_source_hash="0" * 64)
                )
                assert isinstance(changed, RegisteredEpochReviewOperationConflict)
                consumed = await reviews.accept(_prepared(command, subject, evidence))
                assert isinstance(consumed, RegisteredEpochReviewAttestationConflict)
                assert await reviews.load_active(SCOPE) is None
                await transaction.rollback()

            assert await _count(admin, registered_epoch_reviews) == 1
            assert await _count(admin, active_config_epochs) == 0
        finally:
            await admin.dispose()
            await runtime.dispose()

    asyncio.run(scenario())


def test_concurrent_registered_callbacks_consume_one_attestation_at_most_once(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        target = _draft("registered-race")
        first = create_postgres_engine(runtime_postgres_database_url)
        second = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        barrier = asyncio.Barrier(2)
        try:
            subject = await _registered_subject(first, target)
            command = _command(target)
            evidence = await _register_registered_attestation(first, command)

            async def accept(engine: AsyncEngine) -> object:
                async with PostgresRegisteredEpochReviewUnitOfWork(engine) as transaction:
                    await barrier.wait()
                    result = await transaction.registered_epoch_reviews.accept(
                        _prepared(command, subject, evidence)
                    )
                    await transaction.commit()
                    return result

            results = await asyncio.gather(accept(first), accept(second))
            assert sum(isinstance(item, RegisteredEpochReviewCreated) for item in results) == 1
            assert (
                sum(isinstance(item, RegisteredEpochReviewAttestationConflict) for item in results)
                == 1
            )
            assert await _count(admin, registered_epoch_reviews) == 1
        finally:
            await admin.dispose()
            await second.dispose()
            await first.dispose()

    asyncio.run(scenario())


def test_subject_tables_never_consume_or_retire_each_others_challenges(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        target = _draft("registered-isolation")
        try:
            subject = await _registered_subject(engine, target)
            command = _command(target, operation_id="shared-operation")
            original = _review(target=_draft("proposal-isolation"))
            proposal = replace(
                original, command=replace(original.command, operation_id="shared-operation")
            )
            proposal_evidence = await _register_attestation(engine, proposal)
            registered_evidence = await _register_registered_attestation(engine, command)

            async with PostgresRegisteredEpochReviewUnitOfWork(engine) as transaction:
                accepted = await transaction.registered_epoch_reviews.accept(
                    _prepared(command, subject, registered_evidence)
                )
                assert isinstance(accepted, RegisteredEpochReviewCreated)
                await transaction.commit()
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                assert await transaction.proposal_reviews.attestation_is_pending(
                    proposal_evidence.transaction
                )
                await transaction.rollback()

            second = await _register_registered_attestation(
                engine, replace(command, operation_id="registered-second")
            )
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                await transaction.proposal_reviews.retire_attestation(proposal_evidence.transaction)
                await transaction.commit()
            async with PostgresRegisteredEpochReviewUnitOfWork(engine) as transaction:
                assert await transaction.registered_epoch_reviews.attestation_is_pending(
                    second.transaction
                )
                await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_lock_time_baseline_rejects_an_aba_pointer_without_consuming_effects(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        first = _draft("registered-aba-first")
        second = _draft("registered-aba-second")
        target = _draft("registered-aba-target")
        try:
            subject = await _registered_subject(runtime, target)
            async with PostgresConfigEpochUnitOfWork(runtime) as transaction:
                await transaction.config_epochs.register(first)
                await transaction.config_epochs.register(second)
                for epoch_id, expected, operation in (
                    (first.epoch_id, None, "aba-activate-first"),
                    (second.epoch_id, 1, "aba-activate-second"),
                    (first.epoch_id, 2, "aba-reactivate-first"),
                ):
                    applied = await transaction.config_epochs.activate(
                        prepare_config_epoch_activation(
                            _legacy_activation(epoch_id, expected, operation)
                        )
                    )
                    assert isinstance(applied, ConfigEpochActivationApplied)
                await transaction.commit()
            command = _command(target, expected_active=ActiveConfigEpoch(SCOPE, first.epoch_id, 1))
            evidence = await _register_registered_attestation(runtime, command)
            async with PostgresRegisteredEpochReviewUnitOfWork(runtime) as transaction:
                result = await transaction.registered_epoch_reviews.accept(
                    _prepared(command, subject, evidence, base=first)
                )
                await transaction.rollback()
            assert isinstance(result, RegisteredEpochReviewBaselineConflict)
            assert result.active == ActiveConfigEpoch(SCOPE, first.epoch_id, 3)
            assert await _count(admin, registered_epoch_reviews) == 0
        finally:
            await admin.dispose()
            await runtime.dispose()

    asyncio.run(scenario())


def test_reviewed_registered_epoch_activates_only_through_its_v3_selector(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        target = _draft("registered-activation")
        try:
            subject = await _registered_subject(runtime, target)
            command = _command(target)
            evidence = await _register_registered_attestation(runtime, command)
            async with PostgresRegisteredEpochReviewUnitOfWork(runtime) as transaction:
                accepted = await transaction.registered_epoch_reviews.accept(
                    _prepared(command, subject, evidence)
                )
                assert isinstance(accepted, RegisteredEpochReviewCreated)
                await transaction.commit()
            assert await _count(admin, active_config_epochs) == 0

            async with PostgresRegisteredEpochReviewUnitOfWork(runtime) as transaction:
                reviews = transaction.registered_epoch_reviews
                now = await reviews.current_time()
                candidate = await reviews.load_activation_candidate(
                    scope=SCOPE,
                    target_epoch_id=target.epoch_id,
                    expected_source_hash=target.source_hash,
                    review_operation_id=command.operation_id,
                )
                assert candidate == accepted.record
                assert (
                    await reviews.load_activation_candidate(
                        scope=SCOPE,
                        target_epoch_id=target.epoch_id,
                        expected_source_hash="0" * 64,
                        review_operation_id=command.operation_id,
                    )
                    is None
                )
                authority = RegisteredEpochActivationAuthority(
                    accepted.record, accepted.record.attestation.reviewer.evidence, now
                )
                value = _registered_activation(command, authority, now)
                foreign_selector = replace(value, review_operation_id="another-review")
                rejected = await reviews.activate_config(
                    prepare_config_epoch_activation(foreign_selector), authority
                )
                assert isinstance(rejected, RepositoryActivationAuthorityConflict)
                applied = await reviews.activate_config(
                    prepare_config_epoch_activation(value), authority
                )
                assert isinstance(applied, ConfigEpochActivationApplied)
                await transaction.commit()

            async with PostgresRegisteredEpochReviewUnitOfWork(runtime) as transaction:
                reviews = transaction.registered_epoch_reviews
                duplicate = await reviews.activate_config(
                    prepare_config_epoch_activation(value), authority
                )
                assert isinstance(duplicate, ConfigEpochActivationDuplicate)
                assert duplicate.record == applied.record
                proposal_selector = replace(
                    value,
                    activation_version=2,
                    expected_source_hash=None,
                    proposal_manifest_id="proposal:" + "a" * 32,
                )
                conflict = await reviews.activate_config(
                    prepare_config_epoch_activation(proposal_selector), authority
                )
                assert isinstance(conflict, ConfigEpochActivationOperationConflict)
                await transaction.rollback()
            assert await _count(admin, active_config_epochs) == 1
        finally:
            await admin.dispose()
            await runtime.dispose()

    asyncio.run(scenario())


def test_generic_audit_port_rejects_registered_epoch_review_events(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        target = _draft("registered-pair-owned")
        command = _command(target)
        subject = RegisteredEpochSubject(
            target, RegisteredEpochRegistrant("register-1", _REGISTRANT, "audit_" + "1" * 32)
        )
        prepared = _prepared(command, subject, _attestation(command, issued_at=_FIXED))
        try:
            with pytest.raises(PersistenceInvariantViolation, match="pair-owned"):
                async with PostgresUnitOfWork(engine) as transaction:
                    await transaction.audit_events.append(prepared._take_audit_event())
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE ci_coordinator.registered_epoch_reviews SET provider_revision = '" + "0" * 40 + "'",
        "DELETE FROM ci_coordinator.registered_epoch_reviews",
    ),
    ids=["update", "delete"],
)
def test_runtime_principal_cannot_mutate_a_retained_registered_epoch_review(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    statement: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        target = _draft("registered-immutable")
        try:
            subject = await _registered_subject(runtime, target)
            command = _command(target)
            evidence = await _register_registered_attestation(runtime, command)
            async with PostgresRegisteredEpochReviewUnitOfWork(runtime) as transaction:
                await transaction.registered_epoch_reviews.accept(
                    _prepared(command, subject, evidence)
                )
                await transaction.commit()
            async with runtime.connect() as connection:
                direct = await connection.begin()
                try:
                    with pytest.raises(DBAPIError, match="permission denied"):
                        await connection.execute(text(statement))
                finally:
                    await direct.rollback()
            async with admin.connect() as connection:
                owner = await connection.begin()
                try:
                    with pytest.raises(DBAPIError, match="review history is immutable"):
                        await connection.execute(text(statement))
                finally:
                    await owner.rollback()
            assert await _count(admin, registered_epoch_reviews) == 1
        finally:
            await admin.dispose()
            await runtime.dispose()

    asyncio.run(scenario())


def test_registered_review_grants_are_required_for_runtime_admission(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        "REVOKE SELECT, INSERT ON ci_coordinator.registered_epoch_reviews "
                        f"FROM {RUNTIME_ROLE}"
                    )
                )
            with pytest.raises(DatabaseCapabilityUnavailable, match="runtime database principal"):
                async with PostgresRegisteredEpochReviewUnitOfWork(runtime):
                    pass
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        "GRANT SELECT, INSERT ON ci_coordinator.registered_epoch_reviews "
                        f"TO {RUNTIME_ROLE}"
                    )
                )
            async with PostgresRegisteredEpochReviewUnitOfWork(runtime) as transaction:
                await transaction.rollback()
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


_FIXED = datetime.fromisoformat("2026-10-11T10:00:00+00:00")


async def _register_standalone(
    engine: AsyncEngine,
    draft: ValidatedEpochDraft,
    operation_id: str,
    actor: str,
) -> None:
    async with PostgresConfigEpochUnitOfWork(engine) as transaction:
        result = await transaction.config_epochs.register_operation(
            prepare_config_epoch_registration(
                ConfigEpochRegistrationCommand(
                    draft=draft,
                    operation_id=operation_id,
                    actor=actor,
                    occurred_at="2026-10-11T09:00:00.000Z",
                )
            )
        )
        assert isinstance(result, ConfigEpochRegistrationCommitted)
        await transaction.commit()


async def _registered_subject(
    engine: AsyncEngine,
    draft: ValidatedEpochDraft,
) -> RegisteredEpochSubject:
    await _register_standalone(engine, draft, f"register-{draft.epoch_id[:8]}", _REGISTRANT)
    async with PostgresRegisteredEpochReviewUnitOfWork(engine) as transaction:
        subject = await transaction.registered_epoch_reviews.load_registered_epoch(
            SCOPE, draft.epoch_id
        )
        await transaction.rollback()
    assert isinstance(subject, RegisteredEpochSubject)
    return subject


def _command(
    target: ValidatedEpochDraft,
    *,
    operation_id: str = "registered-review",
    expected_active: ActiveConfigEpoch | None = None,
) -> RegisteredEpochReviewCommand:
    return RegisteredEpochReviewCommand(
        scope=SCOPE,
        operation_id=operation_id,
        target_epoch_id=target.epoch_id,
        expected_source_hash=target.source_hash,
        expected_active=expected_active,
        actor=_ACTOR,
    )


def _attestation(
    command: RegisteredEpochReviewCommand,
    *,
    issued_at: datetime,
    observed_at: datetime | None = None,
) -> RegisteredEpochAttestationEvidence:
    expires_at = issued_at + timedelta(minutes=5)
    active = command.expected_active
    return RegisteredEpochAttestationEvidence(
        RegisteredEpochAttestationTransaction(
            transaction_digest=sha256(
                f"registered\0{command.actor}\0{command.operation_id}".encode()
            ).digest(),
            binding=RegisteredEpochStepUpBinding(
                session_handle_digest=_SESSION_DIGEST,
                initiating_actor=command.actor,
                scope=command.scope,
                operation_id=command.operation_id,
                target_epoch_id=command.target_epoch_id,
                source_hash=command.expected_source_hash,
                revision=_REVISION,
                inventory_digest=_INVENTORY,
                expected_active_epoch_id=None if active is None else active.epoch_id,
                expected_active_revision=None if active is None else active.revision,
                authority_profile_digest=_PROFILE_DIGEST,
                issued_at=issued_at,
                expires_at=expires_at,
            ),
        ),
        GitHubReviewerPrincipal(
            evidence=GitHubReviewerEvidence(42, "reviewer", "maintain"),
            observed_at=observed_at or issued_at,
            expires_at=expires_at,
        ),
    )


async def _register_registered_attestation(
    engine: AsyncEngine,
    command: RegisteredEpochReviewCommand,
) -> RegisteredEpochAttestationEvidence:
    await _ensure_control_plane_session(engine, actor=command.actor)
    async with PostgresRegisteredEpochReviewUnitOfWork(engine) as transaction:
        now = await transaction.registered_epoch_reviews.current_time()
        attestation = _attestation(command, issued_at=now - timedelta(seconds=1), observed_at=now)
        registration = await transaction.registered_epoch_reviews.register_attestation(
            attestation.transaction
        )
        assert isinstance(registration, RepositoryAttestationRegistered)
        await transaction.commit()
    return attestation


def _prepared(
    command: RegisteredEpochReviewCommand,
    subject: RegisteredEpochSubject,
    attestation: RegisteredEpochAttestationEvidence,
    *,
    base: ValidatedEpochDraft | None = None,
) -> PreparedRegisteredEpochReview:
    return prepare_registered_epoch_review(
        RegisteredEpochReviewDraft(
            command=command,
            provider_revision=_REVISION,
            inventory_digest=_INVENTORY,
            subject=subject,
            semantic_diff=compare_policy_drafts(base, subject.draft),
        ),
        attestation=attestation,
        occurred_at="2026-10-11T10:00:00.000Z",
    )


def _registered_activation(
    command: RegisteredEpochReviewCommand,
    authority: RegisteredEpochActivationAuthority,
    now: datetime,
) -> ConfigEpochActivationCommand:
    return ConfigEpochActivationCommand(
        scope=SCOPE,
        target_epoch_id=command.target_epoch_id,
        expected_revision=None,
        operation_id="activate-registered",
        actor=_ACTOR,
        occurred_at="2026-10-11T10:00:01.000Z",
        activation_version=3,
        review_operation_id=command.operation_id,
        expected_source_hash=command.expected_source_hash,
        authority_evidence_hash=authority.evidence_hash,
        authority_observed_at=now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )


def _legacy_activation(
    epoch_id: str,
    expected_revision: int | None,
    operation_id: str,
) -> ConfigEpochActivationCommand:
    return ConfigEpochActivationCommand(
        scope=SCOPE,
        target_epoch_id=epoch_id,
        expected_revision=expected_revision,
        operation_id=operation_id,
        actor=_ACTOR,
        occurred_at="2026-10-11T09:00:00.000Z",
        proposal_manifest_id="proposal:" + "f" * 32,
        authority_evidence_hash="a" * 64,
        authority_observed_at="2026-10-11T09:00:00.000Z",
    )


async def _count(engine: AsyncEngine, table: Table) -> int:
    async with engine.connect() as connection:
        value = await connection.scalar(select(func.count()).select_from(table))
    if type(value) is not int:
        raise AssertionError("count aggregate unexpectedly returned a non-integer")
    return value
