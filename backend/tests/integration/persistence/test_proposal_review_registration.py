from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from alembic import command as alembic_command
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

import ci_coordinator.persistence.proposal_review_repository as proposal_review_repository
from ci_coordinator.audit_replay import AuditAppendAppended
from ci_coordinator.config_control import (
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ConfigEpochActivationApplied,
    ConfigEpochActivationCommand,
    prepare_config_epoch_activation,
)
from ci_coordinator.control_plane_identity import (
    ControlPlaneSessionRecord,
    DisplayMetadata,
    GitHubReviewerEvidence,
    GitHubReviewerPrincipal,
    ReviewerStepUpBinding,
    derive_human_actor_id,
)
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence import (
    PersistenceInvariantViolation,
    PostgresConfigEpochUnitOfWork,
    PostgresControlPlaneSessionStore,
    PostgresUnitOfWork,
)
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable, StoreUnavailable
from ci_coordinator.persistence.proposal_review_schema_attestation import (
    proposal_review_registration_schema_matches_contract,
)
from ci_coordinator.persistence.proposal_review_unit_of_work import (
    PostgresProposalReviewUnitOfWork,
)
from ci_coordinator.persistence.schema import (
    audit_events,
    config_epochs,
    control_plane_sessions,
    workflow_proposal_reviews,
)
from ci_coordinator.proposal_review import (
    PreparedProposalReview,
    ProposalReviewAttestationConflict,
    ProposalReviewBaselineConflict,
    ProposalReviewCommand,
    ProposalReviewCreated,
    ProposalReviewDraft,
    ProposalReviewDuplicate,
    ProposalReviewOperationConflict,
    ProposalReviewRecord,
    RepositoryActivationAuthority,
    RepositoryActivationAuthorityConflict,
    RepositoryAttestationCapacityExceeded,
    RepositoryAttestationEvidence,
    RepositoryAttestationRegistered,
    RepositoryAttestationRegistrationRejected,
    RepositoryAttestationTransaction,
    compare_policy_drafts,
)
from ci_coordinator.proposal_review import (
    prepare_proposal_review as _prepare_proposal_review,
)

from .conftest import RUNTIME_ROLE, alembic_config

pytestmark = pytest.mark.persistence

SCOPE = RepositoryScope(7, 11)
_ISSUER = "https://identity.example/realms/control-plane"
_SUBJECT = "proposal-reviewer"
_ACTOR = derive_human_actor_id(_ISSUER, _SUBJECT)
_OTHER_ACTOR = derive_human_actor_id(_ISSUER, "other-reviewer")
_PROFILE_DIGEST = "e" * 64
_SESSION_DIGEST = b"s" * 32


def test_registration_replay_and_manifest_deduplication_are_atomic(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        target = _draft("target")
        review = _review(target=target)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        try:
            attestation = await _register_attestation(runtime_engine, review)
            async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                assert not hasattr(transaction, "config_epochs")
                result = await transaction.proposal_reviews.accept(
                    _prepare(
                        review,
                        attestation=attestation,
                        occurred_at="2026-07-19T10:00:00.000Z",
                    )
                )
                assert isinstance(result, ProposalReviewCreated)
                assert result.epoch_created
                assert await transaction.proposal_reviews.load_active(SCOPE) is None
                await transaction.commit()

            duplicate_review = replace(
                review,
                command=replace(
                    review.command,
                    operation_id="review-another-operation",
                ),
            )
            duplicate_attestation = await _register_attestation(
                runtime_engine,
                duplicate_review,
            )
            async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                replay = await transaction.proposal_reviews.resolve_operation(review.command)
                conflict = await transaction.proposal_reviews.resolve_operation(
                    replace(review.command, actor=_OTHER_ACTOR)
                )
                duplicate_manifest = await transaction.proposal_reviews.accept(
                    _prepare(
                        duplicate_review,
                        attestation=duplicate_attestation,
                        occurred_at="2026-07-19T10:01:00.000Z",
                    )
                )
                await transaction.commit()
            assert isinstance(replay, ProposalReviewDuplicate)
            assert replay.record == result.record
            assert (
                replay.record.attestation.transaction.binding.issued_at
                < replay.record.attestation.reviewer.observed_at
            )
            assert isinstance(conflict, ProposalReviewOperationConflict)
            assert isinstance(duplicate_manifest, ProposalReviewDuplicate)
            assert duplicate_manifest.record == result.record
            assert await _counts(admin_engine) == (1, 1, 1)
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_concurrent_manifest_acceptance_has_one_created_effect_and_one_duplicate(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        first = _review(target=_draft("concurrent"))
        second = replace(
            first,
            command=replace(
                first.command,
                operation_id="review-concurrent-second",
            ),
        )
        barrier = asyncio.Barrier(2)
        first_engine = create_postgres_engine(runtime_postgres_database_url)
        second_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)

        async def accept(
            review: ProposalReviewDraft,
            attestation: RepositoryAttestationEvidence,
            engine: AsyncEngine,
        ) -> ProposalReviewCreated | ProposalReviewDuplicate:
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                await barrier.wait()
                result = await transaction.proposal_reviews.accept(
                    _prepare(
                        review,
                        attestation=attestation,
                        occurred_at="2026-07-19T10:00:30.000Z",
                    )
                )
                assert isinstance(result, ProposalReviewCreated | ProposalReviewDuplicate)
                await transaction.commit()
                return result

        try:
            first_attestation = await _register_attestation(first_engine, first)
            second_attestation = await _register_attestation(second_engine, second)
            results = await asyncio.gather(
                accept(first, first_attestation, first_engine),
                accept(second, second_attestation, second_engine),
            )
            created = next(
                result for result in results if isinstance(result, ProposalReviewCreated)
            )
            duplicate = next(
                result for result in results if isinstance(result, ProposalReviewDuplicate)
            )
            assert duplicate.record == created.record
            assert created.record.command.actor == _ACTOR
            assert await _counts(admin_engine) == (1, 1, 1)
        finally:
            await admin_engine.dispose()
            await second_engine.dispose()
            await first_engine.dispose()

    asyncio.run(scenario())


def test_concurrent_callbacks_consume_one_attestation_at_most_once(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        review = _review(target=_draft("one-use-callback"))
        first_engine = create_postgres_engine(runtime_postgres_database_url)
        second_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        barrier = asyncio.Barrier(2)

        async def accept(engine: AsyncEngine) -> object:
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                await barrier.wait()
                result = await transaction.proposal_reviews.accept(
                    _prepare(
                        review,
                        attestation=attestation,
                        occurred_at="2026-07-19T10:00:45.000Z",
                    )
                )
                await transaction.commit()
                return result

        try:
            attestation = await _register_attestation(first_engine, review)
            results = await asyncio.gather(accept(first_engine), accept(second_engine))
            assert sum(isinstance(item, ProposalReviewCreated) for item in results) == 1
            assert sum(isinstance(item, ProposalReviewAttestationConflict) for item in results) == 1
            assert await _counts(admin_engine) == (1, 1, 1)
        finally:
            await admin_engine.dispose()
            await second_engine.dispose()
            await first_engine.dispose()

    asyncio.run(scenario())


def test_generic_audit_port_rejects_proposal_review_events(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        prepared = _prepare(
            _review(target=_draft("pair-owned")),
            occurred_at="2026-07-19T10:00:00.000Z",
        )
        try:
            with pytest.raises(PersistenceInvariantViolation, match="pair-owned"):
                async with PostgresUnitOfWork(engine) as transaction:
                    await transaction.audit_events.append(prepared._take_audit_event())
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_schema_attestation_rejects_a_disabled_immutability_trigger(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                assert await proposal_review_registration_schema_matches_contract(connection)
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.workflow_proposal_reviews "
                        "DISABLE TRIGGER tr_workflow_proposal_reviews_immutable"
                    )
                )
                assert not await proposal_review_registration_schema_matches_contract(connection)
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.workflow_proposal_reviews "
                        "ENABLE TRIGGER tr_workflow_proposal_reviews_immutable"
                    )
                )
                assert await proposal_review_registration_schema_matches_contract(connection)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_attestation_registration_requires_a_live_exact_session(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        review = _review(target=_draft("session-bound-attestation"))
        try:
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                now = await transaction.proposal_reviews.current_time()
                attestation = _attestation(
                    review,
                    issued_at=now - timedelta(seconds=1),
                    observed_at=now,
                )
                rejected = await transaction.proposal_reviews.register_attestation(
                    attestation.transaction
                )
                assert isinstance(rejected, RepositoryAttestationRegistrationRejected)
                await transaction.rollback()

            await _ensure_control_plane_session(engine)
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                registered = await transaction.proposal_reviews.register_attestation(
                    attestation.transaction
                )
                assert isinstance(registered, RepositoryAttestationRegistered)
                assert await transaction.proposal_reviews.attestation_is_pending(
                    attestation.transaction
                )
                assert not await transaction.proposal_reviews.attestation_is_pending(
                    replace(attestation.transaction, transaction_digest=b"x" * 32)
                )
                await transaction.proposal_reviews.retire_attestation(
                    replace(attestation.transaction, transaction_digest=b"x" * 32)
                )
                assert await transaction.proposal_reviews.attestation_is_pending(
                    attestation.transaction
                )
                await transaction.proposal_reviews.retire_attestation(attestation.transaction)
                await transaction.proposal_reviews.retire_attestation(attestation.transaction)
                assert not await transaction.proposal_reviews.attestation_is_pending(
                    attestation.transaction
                )
                await transaction.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_attestation_registration_bounds_pending_work_per_session(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _ensure_control_plane_session(engine)
            base = _review(target=_draft("bounded-attestations"))
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                now = await transaction.proposal_reviews.current_time()
                results: list[object] = []
                for index in range(9):
                    review = replace(
                        base,
                        command=replace(base.command, operation_id=f"bounded-{index}"),
                    )
                    results.append(
                        await transaction.proposal_reviews.register_attestation(
                            _attestation(
                                review,
                                issued_at=now - timedelta(seconds=1),
                                observed_at=now,
                            ).transaction
                        )
                    )
                assert all(
                    isinstance(item, RepositoryAttestationRegistered) for item in results[:8]
                )
                assert isinstance(results[8], RepositoryAttestationCapacityExceeded)
                await transaction.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_attestation_capacity_locks_the_exact_session_row(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        probe_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _ensure_control_plane_session(engine)
            review = _review(target=_draft("session-capacity-lock"))
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                now = await transaction.proposal_reviews.current_time()
                registration = await transaction.proposal_reviews.register_attestation(
                    _attestation(
                        review,
                        issued_at=now - timedelta(seconds=1),
                        observed_at=now,
                    ).transaction
                )
                assert isinstance(registration, RepositoryAttestationRegistered)

                async with probe_engine.begin() as probe:
                    await probe.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(DBAPIError) as blocked:
                        await probe.execute(
                            select(control_plane_sessions.c.handle_digest)
                            .where(control_plane_sessions.c.handle_digest == _SESSION_DIGEST)
                            .with_for_update(of=control_plane_sessions)
                        )
                    assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
                await transaction.rollback()
        finally:
            await engine.dispose()
            await probe_engine.dispose()

    asyncio.run(scenario())


def test_lock_time_baseline_check_rejects_an_aba_pointer(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        first = _draft("first")
        second = _draft("second")
        target = _draft("target")
        stale = ActiveConfigEpoch(SCOPE, first.epoch_id, 1)
        review = _review(target=target, base=first, expected_active=stale)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(runtime_engine) as transaction:
                await transaction.config_epochs.register(first)
                await transaction.config_epochs.register(second)
                for command in (
                    _activation(first.epoch_id, None, "activate-first"),
                    _activation(second.epoch_id, 1, "activate-second"),
                    _activation(first.epoch_id, 2, "reactivate-first"),
                ):
                    assert isinstance(
                        await transaction.config_epochs.activate(
                            prepare_config_epoch_activation(command)
                        ),
                        ConfigEpochActivationApplied,
                    )
                await transaction.commit()

            attestation = await _register_attestation(runtime_engine, review)
            async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                result = await transaction.proposal_reviews.accept(
                    _prepare(
                        review,
                        attestation=attestation,
                        occurred_at="2026-07-19T10:02:00.000Z",
                    )
                )
                await transaction.rollback()
            assert isinstance(result, ProposalReviewBaselineConflict)
            assert result.active == ActiveConfigEpoch(SCOPE, first.epoch_id, 3)
            reviews, events, epochs = await _counts(admin_engine)
            assert reviews == 0
            assert events == 3
            assert epochs == 2
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reenter_reviewed_base", [False, True], ids=("different", "aba"))
def test_activation_receipt_cannot_authorize_a_changed_baseline(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    reenter_reviewed_base: bool,
) -> None:
    async def scenario() -> None:
        reviewed_base = _draft("activation-reviewed-base")
        intervening = _draft("activation-intervening")
        target = _draft("activation-target")
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        try:
            async with PostgresConfigEpochUnitOfWork(runtime_engine) as transaction:
                for draft in (reviewed_base, intervening):
                    await transaction.config_epochs.register(draft)
                applied = await transaction.config_epochs.activate(
                    prepare_config_epoch_activation(
                        _activation(reviewed_base.epoch_id, None, "activate-reviewed-base")
                    )
                )
                assert isinstance(applied, ConfigEpochActivationApplied)
                await transaction.commit()

            review = _review(
                target=target,
                base=reviewed_base,
                expected_active=ActiveConfigEpoch(SCOPE, reviewed_base.epoch_id, 1),
            )
            attestation = await _register_attestation(runtime_engine, review)
            async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                accepted = await transaction.proposal_reviews.accept(
                    _prepare(
                        review,
                        attestation=attestation,
                        occurred_at="2026-09-02T10:00:00.000Z",
                    )
                )
                assert isinstance(accepted, ProposalReviewCreated)
                await transaction.commit()

            async with PostgresConfigEpochUnitOfWork(runtime_engine) as transaction:
                changed = await transaction.config_epochs.activate(
                    prepare_config_epoch_activation(
                        _activation(intervening.epoch_id, 1, "activate-intervening")
                    )
                )
                assert isinstance(changed, ConfigEpochActivationApplied)
                if reenter_reviewed_base:
                    changed = await transaction.config_epochs.activate(
                        prepare_config_epoch_activation(
                            _activation(reviewed_base.epoch_id, 2, "reactivate-reviewed-base")
                        )
                    )
                    assert isinstance(changed, ConfigEpochActivationApplied)
                await transaction.commit()

            expected_revision = 3 if reenter_reviewed_base else 2
            async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                observed_at = await transaction.proposal_reviews.current_time()
                authority = RepositoryActivationAuthority(
                    review=accepted.record,
                    rechecked=accepted.record.attestation.reviewer.evidence,
                    rechecked_at=observed_at,
                )
                command = ConfigEpochActivationCommand(
                    scope=SCOPE,
                    target_epoch_id=target.epoch_id,
                    expected_revision=expected_revision,
                    operation_id=f"activate-stale-review-{expected_revision}",
                    actor=_ACTOR,
                    occurred_at="2026-09-02T10:00:01.000Z",
                    proposal_manifest_id=review.command.expected_manifest_id,
                    authority_evidence_hash=authority.evidence_hash,
                    authority_observed_at=(
                        observed_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    ),
                )
                rejected = await transaction.proposal_reviews.activate_config(
                    prepare_config_epoch_activation(command),
                    authority,
                )
                assert isinstance(rejected, RepositoryActivationAuthorityConflict)
                active = await transaction.proposal_reviews.load_active(SCOPE)
                assert active is not None
                assert active.active.revision == expected_revision
                assert active.active.epoch_id == (
                    reviewed_base.epoch_id if reenter_reviewed_base else intervening.epoch_id
                )
                await transaction.commit()

            assert await _counts(admin_engine) == (
                1,
                4 if reenter_reviewed_base else 3,
                3,
            )
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_audit_conflict_rolls_back_new_epoch_and_review(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        target = _draft("target")
        review = _review(target=target)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        try:
            attestation = await _register_attestation(runtime_engine, review)
            orphan = _prepare(
                review,
                attestation=attestation,
                occurred_at="2026-07-19T10:03:00.000Z",
            )
            async with runtime_engine.begin() as connection:
                repository = _PostgresAuditEventRepository(
                    connection,
                    lambda: None,
                    lambda: None,
                )
                appended = await repository._append_pair_owned(
                    orphan._take_audit_event(),
                    SCOPE,
                )
                assert isinstance(appended, AuditAppendAppended)

            with pytest.raises(
                PersistenceInvariantViolation,
                match="audit idempotency is inconsistent",
            ):
                async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                    await transaction.proposal_reviews.accept(
                        _prepare(
                            review,
                            attestation=attestation,
                            occurred_at="2026-07-19T10:03:00.000Z",
                        )
                    )
            assert await _counts(admin_engine) == (0, 1, 0)
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_review_row_failure_rolls_back_epoch_and_audit(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = proposal_review_repository._record_to_row

    def invalid_review_row(record: ProposalReviewRecord) -> dict[str, object]:
        row = original(record)
        row["changed_pointer_count"] = -1
        return row

    monkeypatch.setattr(proposal_review_repository, "_record_to_row", invalid_review_row)

    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        try:
            review = _review(target=_draft("invalid-review-row"))
            attestation = await _register_attestation(runtime_engine, review)
            with pytest.raises(StoreUnavailable, match="registration failed"):
                async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                    await transaction.proposal_reviews.accept(
                        _prepare(
                            review,
                            attestation=attestation,
                            occurred_at="2026-07-19T10:03:30.000Z",
                        )
                    )
            assert await _counts(admin_engine) == (0, 0, 0)
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_declared_capability_without_runtime_grants_fails_closed(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text(
                        "REVOKE SELECT, INSERT ON "
                        f"ci_coordinator.workflow_proposal_reviews FROM {RUNTIME_ROLE}"
                    )
                )
            with pytest.raises(DatabaseCapabilityUnavailable, match="runtime database principal"):
                async with PostgresProposalReviewUnitOfWork(runtime_engine):
                    pass
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text(
                        "GRANT SELECT, INSERT ON "
                        f"ci_coordinator.workflow_proposal_reviews TO {RUNTIME_ROLE}"
                    )
                )
            async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                await transaction.rollback()
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "statement",
    (
        (
            "UPDATE ci_coordinator.workflow_proposal_reviews "
            "SET provider_revision = '0000000000000000000000000000000000000000'"
        ),
        "DELETE FROM ci_coordinator.workflow_proposal_reviews",
    ),
    ids=["update", "delete"],
)
def test_runtime_principal_cannot_mutate_retained_review(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    statement: str,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        try:
            review = _review(target=_draft("immutable-review"))
            attestation = await _register_attestation(runtime_engine, review)
            async with PostgresProposalReviewUnitOfWork(runtime_engine) as transaction:
                await transaction.proposal_reviews.accept(
                    _prepare(
                        review,
                        attestation=attestation,
                        occurred_at="2026-07-19T10:03:45.000Z",
                    )
                )
                await transaction.commit()
            async with runtime_engine.connect() as connection:
                direct_transaction = await connection.begin()
                try:
                    with pytest.raises(DBAPIError, match="permission denied"):
                        await connection.execute(text(statement))
                finally:
                    await direct_transaction.rollback()
            assert await _counts(admin_engine) == (1, 1, 1)
        finally:
            await admin_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_bootstrap_downgrade_preserves_retained_reviews(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def retain_review() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            review = _review(target=_draft("retained"))
            attestation = await _register_attestation(engine, review)
            async with PostgresProposalReviewUnitOfWork(engine) as transaction:
                result = await transaction.proposal_reviews.accept(
                    _prepare(
                        review,
                        attestation=attestation,
                        occurred_at="2026-07-19T10:04:00.000Z",
                    )
                )
                assert isinstance(result, ProposalReviewCreated)
                await transaction.commit()
        finally:
            await engine.dispose()

    asyncio.run(retain_review())
    with pytest.raises(
        RuntimeError,
        match="requires forward repair or admitted restore",
    ):
        alembic_command.downgrade(
            alembic_config(postgres_database_url),
            "base",
        )

    async def assert_retained() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            assert await _counts(engine) == (1, 1, 1)
        finally:
            await engine.dispose()

    asyncio.run(assert_retained())


def _review(
    *,
    target: ValidatedEpochDraft,
    base: ValidatedEpochDraft | None = None,
    expected_active: ActiveConfigEpoch | None = None,
) -> ProposalReviewDraft:
    return ProposalReviewDraft(
        command=ProposalReviewCommand(
            scope=SCOPE,
            operation_id="review-operation",
            expected_manifest_id="proposal:" + "a" * 32,
            expected_active=expected_active,
            actor=_ACTOR,
        ),
        provider_revision="b" * 40,
        inventory_digest="c" * 64,
        proposal_digest="d" * 64,
        target=target,
        semantic_diff=compare_policy_drafts(base, target),
    )


def _draft(name: str) -> ValidatedEpochDraft:
    result = admit_policy_document(
        canonical_json(
            {
                "schemaVersion": "ci-repository-policy/v1",
                "repository": {
                    "installationId": SCOPE.installation_id,
                    "repositoryId": SCOPE.repository_id,
                    "owner": "example",
                    "name": "repo",
                    "defaultBranch": "master",
                    "rules": [
                        {
                            "name": name,
                            "on": {"event": "push", "branches": ["master"]},
                            "mode": "observe",
                            "expectedSignals": [
                                {
                                    "kind": "workflow",
                                    "name": name,
                                    "workflowFile": ".github/workflows/ci.yml",
                                    "source": "native",
                                    "requiredConclusion": "success",
                                    "required": True,
                                }
                            ],
                            "omittedSignals": [],
                        }
                    ],
                    "dynamicCi": None,
                },
            }
        ),
        "json",
    )
    assert isinstance(result, ValidatedEpochDraft)
    return result


def _prepare(
    draft: ProposalReviewDraft,
    *,
    occurred_at: str,
    attestation: RepositoryAttestationEvidence | None = None,
) -> PreparedProposalReview:
    return _prepare_proposal_review(
        draft,
        attestation=_attestation(draft) if attestation is None else attestation,
        occurred_at=occurred_at,
    )


def _attestation(
    draft: ProposalReviewDraft,
    *,
    issued_at: datetime | None = None,
    observed_at: datetime | None = None,
) -> RepositoryAttestationEvidence:
    issued = issued_at or datetime(2026, 7, 19, 10, tzinfo=UTC)
    observed = observed_at or issued
    expires_at = issued + timedelta(minutes=5)
    command = draft.command
    active = command.expected_active
    return RepositoryAttestationEvidence(
        RepositoryAttestationTransaction(
            transaction_digest=sha256(f"{command.actor}\0{command.operation_id}".encode()).digest(),
            binding=ReviewerStepUpBinding(
                session_handle_digest=_SESSION_DIGEST,
                initiating_actor=command.actor,
                scope=command.scope,
                operation_id=command.operation_id,
                proposal_manifest_id=command.expected_manifest_id,
                revision=draft.provider_revision,
                proposal_digest=draft.proposal_digest,
                expected_active_epoch_id=None if active is None else active.epoch_id,
                expected_active_revision=None if active is None else active.revision,
                authority_profile_digest=_PROFILE_DIGEST,
                issued_at=issued,
                expires_at=expires_at,
            ),
        ),
        GitHubReviewerPrincipal(
            evidence=GitHubReviewerEvidence(42, "reviewer", "maintain"),
            observed_at=observed,
            expires_at=expires_at,
        ),
    )


async def _register_attestation(
    engine: AsyncEngine,
    draft: ProposalReviewDraft,
) -> RepositoryAttestationEvidence:
    await _ensure_control_plane_session(engine)
    async with PostgresProposalReviewUnitOfWork(engine) as transaction:
        observed_at = await transaction.proposal_reviews.current_time()
        attestation = _attestation(
            draft,
            issued_at=observed_at - timedelta(seconds=1),
            observed_at=observed_at,
        )
        registration = await transaction.proposal_reviews.register_attestation(
            attestation.transaction
        )
        assert isinstance(registration, RepositoryAttestationRegistered)
        await transaction.commit()
    return attestation


async def _ensure_control_plane_session(engine: AsyncEngine) -> None:
    store = PostgresControlPlaneSessionStore(engine)
    if await store.load(_SESSION_DIGEST) is not None:
        return
    now = await store.current_time()
    await store.replace(
        previous_handle_digest=None,
        record=ControlPlaneSessionRecord(
            handle_digest=_SESSION_DIGEST,
            issuer=_ISSUER,
            subject=_SUBJECT,
            keycloak_session_id="proposal-review-session",
            actor_id=_ACTOR,
            roles=frozenset(("configure",)),
            authority_profile_digest=_PROFILE_DIGEST,
            issued_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=10),
            display=DisplayMetadata("proposal-reviewer", "Proposal Reviewer"),
        ),
    )


def _activation(
    target_epoch_id: str,
    expected_revision: int | None,
    operation_id: str,
) -> ConfigEpochActivationCommand:
    return ConfigEpochActivationCommand(
        scope=SCOPE,
        target_epoch_id=target_epoch_id,
        expected_revision=expected_revision,
        operation_id=operation_id,
        actor=_ACTOR,
        occurred_at="2026-07-19T09:00:00.000Z",
        proposal_manifest_id="proposal:" + "f" * 32,
        authority_evidence_hash="a" * 64,
        authority_observed_at="2026-07-19T09:00:00.000Z",
    )


async def _counts(engine: AsyncEngine) -> tuple[int, int, int]:
    async with engine.connect() as connection:
        first, second, third = [
            await connection.scalar(select(func.count()).select_from(table))
            for table in (workflow_proposal_reviews, audit_events, config_epochs)
        ]
    if first is None or second is None or third is None:
        raise AssertionError("count aggregate unexpectedly returned null")
    return first, second, third
