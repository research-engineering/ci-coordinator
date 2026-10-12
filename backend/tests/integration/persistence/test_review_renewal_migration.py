from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType

import pytest
from alembic import command
from sqlalchemy import Table, create_engine, func, insert, select, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence import (
    PostgresUnitOfWork,
    compatibility_profile,
    principal_attestation,
    runtime_principal_access,
)
from ci_coordinator.persistence import review_renewal_migration as migration
from ci_coordinator.persistence.compatibility_contracts import (
    RevisionDeclaration,
    build_declaration,
)
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityTimeouts,
    load_bundled_profile,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.control_plane_session_codec import record_values
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.migration_protocol import (
    _load_declaration,
    apply_forward_declaration,
)
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.proposal_review_unit_of_work import PostgresProposalReviewUnitOfWork
from ci_coordinator.persistence.schema import (
    audit_events,
    audit_ledger_head,
    config_epochs,
    control_plane_sessions,
    repository_attestation_transactions,
    workflow_proposal_reviews,
)
from ci_coordinator.persistence.schema_capabilities import (
    AUDIT_LEDGER,
    CONFIG_EPOCH_LIFECYCLE,
    CONTROL_PLANE_IDENTITY_STATE,
    DATABASE_COMPATIBILITY_PROTOCOL,
    PROPOSAL_REVIEW_REGISTRATION,
)
from ci_coordinator.proposal_review import (
    ProposalReviewAttestationConflict,
    ProposalReviewCreated,
    ProposalReviewDuplicate,
    RepositoryAttestationEvidence,
    RepositoryAttestationRegistered,
)

from .conftest import RUNTIME_PASSWORD, RUNTIME_ROLE, _provision_runtime_principal, alembic_config
from .test_proposal_review_registration import (
    _attestation,
    _control_plane_session_record,
    _draft,
    _prepare,
    _register_attestation,
    _review,
)

pytestmark = pytest.mark.persistence


class _OldRequiredReviewUow(PostgresProposalReviewUnitOfWork):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = tuple(
            sorted(
                definition.declaration()
                for definition in (
                    DATABASE_COMPATIBILITY_PROTOCOL,
                    AUDIT_LEDGER,
                    CONFIG_EPOCH_LIFECYCLE,
                    PROPOSAL_REVIEW_REGISTRATION,
                    CONTROL_PLANE_IDENTITY_STATE,
                )
            )
        )
        self.initialized = False

    def _initialize_repositories(self) -> None:
        self.initialized = True
        super()._initialize_repositories()


def _rows(connection: Connection, table: Table) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row) for row in connection.execute(select(table).order_by(*table.primary_key))
    )


def test_cutoff_rolls_back_on_failure_then_preserves_history_and_rejects_old_pending(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, "20260915_0015")
    _project_runtime_acl_before_logout_fence(monkeypatch)
    _provision_runtime_principal(unmigrated_database_url)
    _seed_session_before_logout_fence(unmigrated_database_url)
    runtime_url = (
        make_url(unmigrated_database_url)
        .set(username=RUNTIME_ROLE, password=RUNTIME_PASSWORD)
        .render_as_string(hide_password=False)
    )
    completed = _review(target=_draft("renewable"))
    pending_draft = replace(completed, command=replace(completed.command, operation_id="pending"))

    async def seed() -> RepositoryAttestationEvidence:
        engine = create_postgres_engine(runtime_url)
        try:
            evidence = await _register_attestation(
                engine, completed, unit_of_work_type=_OldRequiredReviewUow
            )
            async with _OldRequiredReviewUow(engine) as transaction:
                result = await transaction.proposal_reviews.accept(
                    _prepare(
                        completed, attestation=evidence, occurred_at="2026-09-26T10:00:00.000Z"
                    )
                )
                assert isinstance(result, ProposalReviewCreated)
                await transaction.commit()
            return await _register_attestation(
                engine, pending_draft, unit_of_work_type=_OldRequiredReviewUow
            )
        finally:
            await engine.dispose()

    pending = asyncio.run(seed())
    engine = create_engine(unmigrated_database_url)
    protected = (
        workflow_proposal_reviews,
        control_plane_sessions,
        config_epochs,
        audit_events,
        audit_ledger_head,
    )
    try:
        with engine.connect() as connection:
            before = tuple(_rows(connection, table) for table in protected)
            old_pending = _rows(connection, repository_attestation_transactions)
            assert len(old_pending) == 1
        real_attest = attest_resulting_capabilities
        profile = load_bundled_profile()
        for blocker in ("shared-fence", "pending-row"):
            with engine.begin() as holder, monkeypatch.context() as patch:
                if blocker == "shared-fence":
                    holder.execute(
                        text("SELECT pg_advisory_xact_lock_shared(:class_id, :object_id)"),
                        {"class_id": profile.fence_class_id, "object_id": profile.fence_object_id},
                    )
                else:
                    holder.execute(select(repository_attestation_transactions).with_for_update())
                patch.setattr(
                    compatibility_profile,
                    "load_bundled_profile",
                    lambda: replace(
                        profile, migration_timeouts=CompatibilityTimeouts(100, 5000, 10000)
                    ),
                )
                with pytest.raises(DBAPIError) as blocked:
                    command.upgrade(config, "20260926_0017")
                assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
                assert _rows(holder, repository_attestation_transactions) == old_pending
                assert tuple(_rows(holder, table) for table in protected) == before
                assert (
                    holder.scalar(text("SELECT version_num FROM public.alembic_version"))
                    == "20260915_0015"
                )

        def reject_expand(connection: Connection, declaration: RevisionDeclaration) -> None:
            real_attest(connection, declaration)
            if declaration.revision_id == "20260926_0017":
                raise RuntimeError("injected final renewal attestation failure")

        with monkeypatch.context() as patch:
            patch.setattr(migration, "attest_resulting_capabilities", reject_expand)
            with pytest.raises(RuntimeError, match="injected final renewal"):
                command.upgrade(config, "20260926_0017")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == "20260915_0015"
            )
            assert _rows(connection, repository_attestation_transactions) == old_pending
            assert tuple(_rows(connection, table) for table in protected) == before
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM ci_coordinator.database_compatibility_declarations "
                        "WHERE generation > 15"
                    )
                )
                == 0
            )
        command.upgrade(config, "20260926_0017")
        with engine.connect() as connection:
            assert _rows(connection, repository_attestation_transactions) == ()
            assert tuple(_rows(connection, table) for table in protected) == before
            previous, contracted, expanded = migration.renewal_declarations()
            for declaration in (previous, contracted, expanded):
                actual = set(
                    connection.execute(
                        text(
                            "SELECT capability_id, descriptor_hash FROM "
                            "ci_coordinator.database_compatibility_capabilities "
                            "WHERE revision_id = :revision"
                        ),
                        {"revision": declaration.revision_id},
                    ).tuples()
                )
                assert actual == {
                    (item.capability_id, item.descriptor_hash) for item in declaration.capabilities
                }
            assert set(contracted.capabilities) < set(previous.capabilities)
            assert set(contracted.capabilities) < set(expanded.capabilities)
            assert AUDIT_LEDGER.declaration() in set(previous.capabilities) & set(
                contracted.capabilities
            ) & set(expanded.capabilities)

        async def after_cutoff() -> None:
            runtime = create_postgres_engine(runtime_url)
            try:
                old = _OldRequiredReviewUow(runtime)
                with pytest.raises(DatabaseCapabilityUnavailable):
                    async with old:
                        pytest.fail("retired requirements exposed the review repository")
                assert not old.initialized
                async with PostgresProposalReviewUnitOfWork(runtime) as transaction:
                    replay = await transaction.proposal_reviews.resolve_operation(completed.command)
                    assert isinstance(replay, ProposalReviewDuplicate)
                    assert not await transaction.proposal_reviews.attestation_is_pending(
                        pending.transaction
                    )
                    rejected = await transaction.proposal_reviews.accept(
                        _prepare(
                            pending_draft,
                            attestation=pending,
                            occurred_at="2026-09-26T10:00:01.000Z",
                        )
                    )
                    assert isinstance(rejected, ProposalReviewAttestationConflict)
                    now = await transaction.proposal_reviews.current_time()
                    fresh = _attestation(
                        pending_draft, issued_at=now - timedelta(seconds=1), observed_at=now
                    )
                    fresh = replace(
                        fresh, transaction=replace(fresh.transaction, transaction_digest=b"n" * 32)
                    )
                    registered = await transaction.proposal_reviews.register_attestation(
                        fresh.transaction
                    )
                    assert isinstance(registered, RepositoryAttestationRegistered)
                    await transaction.proposal_reviews.retire_attestation(pending.transaction)
                    assert await transaction.proposal_reviews.attestation_is_pending(
                        fresh.transaction
                    )
                    await transaction.commit()
                async with PostgresUnitOfWork(runtime) as generic:
                    assert (await generic.audit_events.snapshot()).last_sequence == 1
            finally:
                await runtime.dispose()

        asyncio.run(after_cutoff())
        with engine.connect() as connection:
            current_pending = _rows(connection, repository_attestation_transactions)
            assert len(current_pending) == 1
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _rows(connection, repository_attestation_transactions) == current_pending
            assert tuple(_rows(connection, table) for table in protected) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "missing",
    [
        "config-epoch-lifecycle/v2",
        "proposal-review-registration/v2",
        "audit-ledger/v1",
        "control-plane-identity-state/v1",
        "database-compatibility-protocol/v1",
    ],
)
def test_each_current_review_requirement_is_independently_required(
    unmigrated_database_url: str, missing: str
) -> None:
    command.upgrade(alembic_config(unmigrated_database_url), "head")
    _provision_runtime_principal(unmigrated_database_url)
    profile = load_bundled_profile()
    current = _head_declaration(unmigrated_database_url)
    assert set(migration.renewal_declarations()[2].capabilities) <= set(current.capabilities)
    contracted = build_declaration(
        profile,
        generation=current.generation + 1,
        revision_id="20991231_9981",
        parent_revision_id=current.revision_id,
        transition_kind="contract",
        capabilities=tuple(item for item in current.capabilities if item.capability_id != missing),
    )
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:class_id, :object_id)"),
                {"class_id": profile.fence_class_id, "object_id": profile.fence_object_id},
            )
            apply_forward_declaration(
                connection,
                profile,
                previous_revision_id=current.revision_id,
                proposed=contracted,
                attest_resulting_capabilities=lambda value: attest_resulting_capabilities(
                    connection, value
                ),
            )
            connection.execute(
                text("UPDATE public.alembic_version SET version_num = :revision"),
                {"revision": contracted.revision_id},
            )

        async def attempt() -> None:
            runtime_url = (
                make_url(unmigrated_database_url)
                .set(username=RUNTIME_ROLE, password=RUNTIME_PASSWORD)
                .render_as_string(hide_password=False)
            )
            runtime = create_postgres_engine(runtime_url)
            try:
                with pytest.raises(DatabaseCapabilityUnavailable):
                    async with PostgresProposalReviewUnitOfWork(runtime):
                        pytest.fail("missing requirement admitted repository access")
            finally:
                await runtime.dispose()

        asyncio.run(attempt())
    finally:
        engine.dispose()


def _project_runtime_acl_before_logout_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    # Current grants include the relations created by 20261011_0018, 20261011_0019 and
    # 20261012_0020; earlier revisions lack them.
    fence = {
        "control_plane_logout_fences",
        "control_plane_logout_fence_generation",
        "registered_epoch_attestation_transactions",
        "registered_epoch_reviews",
        "ci_history_population_bounds",
    }
    table_grants = MappingProxyType(
        {
            relation: privileges
            for relation, privileges in runtime_principal_access.RUNTIME_TABLE_GRANTS.items()
            if relation not in fence
        }
    )
    column_grants = MappingProxyType(
        {
            relation: grants
            for relation, grants in runtime_principal_access.RUNTIME_COLUMN_GRANTS.items()
            if relation not in fence
        }
    )
    for module in (runtime_principal_access, principal_attestation):
        monkeypatch.setattr(module, "RUNTIME_TABLE_GRANTS", table_grants)
        monkeypatch.setattr(module, "RUNTIME_COLUMN_GRANTS", column_grants)
    monkeypatch.setattr(
        principal_attestation,
        "_EXPECTED_TABLE_PRIVILEGES_JSON",
        _canonical_json(
            [
                {
                    "allowed": privilege in granted,
                    "privilege_type": privilege,
                    "relation_name": name,
                }
                for name, granted in table_grants.items()
                for privilege in runtime_principal_access.RUNTIME_TABLE_PRIVILEGES
            ]
        ),
    )
    monkeypatch.setattr(
        principal_attestation, "_EXPECTED_TABLE_GRANTS_JSON", _canonical_json(dict(table_grants))
    )
    monkeypatch.setattr(
        principal_attestation,
        "_EXPECTED_COLUMN_GRANTS_JSON",
        principal_attestation._column_grants_json(),
    )


def _seed_session_before_logout_fence(database_url: str) -> None:
    # Current session insertion requires the 0018 fence, so the owner seeds the historic row.
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            now = connection.scalar(select(func.statement_timestamp()))
            assert isinstance(now, datetime)
            connection.execute(
                insert(control_plane_sessions).values(
                    **record_values(_control_plane_session_record(now))
                )
            )
    finally:
        engine.dispose()


def _head_declaration(database_url: str) -> RevisionDeclaration:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            head = connection.scalar(text("SELECT version_num FROM public.alembic_version"))
            assert type(head) is str
            declaration = _load_declaration(connection, load_bundled_profile(), head)
    finally:
        engine.dispose()
    assert declaration is not None
    return declaration


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
