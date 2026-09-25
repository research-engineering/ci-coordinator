from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from control_plane_http_support import human_principal
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.sql.selectable import Select

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import (
    BackChannelLogoutEvidence,
    BackChannelLogoutTarget,
    ControlPlaneSessionRecord,
)
from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.control_plane_identity.activity_query import ActivityQuery
from ci_coordinator.persistence import control_plane_identity_schema_attestation as identity_schema
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.compatibility_admission import admit_schema_dependent_operation
from ci_coordinator.persistence.compatibility_contracts import build_declaration, capabilities_cover
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.compatibility_repository import load_current_compatibility
from ci_coordinator.persistence.connection import (
    configure_read_committed,
    create_postgres_engine,
    verify_read_committed,
)
from ci_coordinator.persistence.control_plane_session_repository import (
    PostgresBackChannelLogoutStore,
    PostgresControlPlaneSessionStore,
)
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.proposal_review_unit_of_work import PostgresProposalReviewUnitOfWork
from ci_coordinator.persistence.schema import (
    audit_events,
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
from ci_coordinator.proposal_review import RepositoryAttestationRegistered

from .conftest import (
    RUNTIME_PASSWORD,
    RUNTIME_ROLE,
    _provision_runtime_principal,
    alembic_config,
)
from .test_proposal_review_registration import (
    _attestation,
    _draft,
    _ensure_control_plane_session,
    _review,
)

pytestmark = pytest.mark.persistence

_OLD_REQUIREMENTS = tuple(
    sorted(
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            CONFIG_EPOCH_LIFECYCLE.declaration(),
            PROPOSAL_REVIEW_REGISTRATION.declaration(),
        )
    )
)


class _ObservedProposalUnitOfWork(PostgresProposalReviewUnitOfWork):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self.initialized = False

    def _initialize_repositories(self) -> None:
        self.initialized = True
        super()._initialize_repositories()


def test_identity_capability_and_facts_survive_every_published_epoch(
    unmigrated_database_url: str,
) -> None:
    config = alembic_config(unmigrated_database_url)
    revisions = tuple(reversed(tuple(ScriptDirectory.from_config(config).walk_revisions())))
    engine = create_engine(unmigrated_database_url)
    try:
        assert revisions
        for revision in revisions:
            command.upgrade(config, revision.revision)
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT descriptor_hash FROM "
                            "ci_coordinator.database_compatibility_capabilities "
                            "WHERE revision_id = :revision "
                            "AND capability_id = 'control-plane-identity-state/v1'"
                        ),
                        {"revision": revision.revision},
                    )
                    == "3e0eca4e06e8665a4f545fc36fc87f2f5debd205da0a7fac8942a0a2477b8194"
                )
                assert identity_schema.control_plane_identity_schema_matches_contract_sync(
                    connection
                ), revision.revision
    finally:
        engine.dispose()


@pytest.mark.parametrize("damage", ["declaration", "facts"])
def test_identity_is_an_independent_pre_exposure_obligation(
    unmigrated_database_url: str, damage: Literal["declaration", "facts"]
) -> None:
    command.upgrade(alembic_config(unmigrated_database_url), "head")
    _provision_runtime_principal(unmigrated_database_url)
    runtime_url = (
        make_url(unmigrated_database_url)
        .set(username=RUNTIME_ROLE, password=RUNTIME_PASSWORD)
        .render_as_string(hide_password=False)
    )

    async def scenario() -> None:
        admin = create_postgres_engine(unmigrated_database_url)
        runtime = create_postgres_engine(runtime_url)
        session_queries: list[Connection] = []

        def observe(
            connection: Connection,
            clause: ClauseElement,
            _multiparams: object,
            _params: object,
            _options: object,
        ) -> None:
            if isinstance(clause, Select) and control_plane_sessions in clause.get_final_froms():
                session_queries.append(connection)

        event.listen(runtime.sync_engine, "before_execute", observe)
        try:
            async with _ObservedProposalUnitOfWork(runtime) as positive:
                assert positive.initialized
                assert positive.proposal_reviews is not None
            await _damage_identity(admin, damage)
            profile = load_bundled_profile()
            async with runtime.connect() as raw:
                connection = await configure_read_committed(raw, profile)
                async with connection.begin():
                    await verify_read_committed(connection, profile)
                    await acquire_compatibility_fence(
                        connection, profile, CompatibilityFenceMode.PARTICIPANT
                    )
                    current = await admit_schema_dependent_operation(
                        connection, profile, _OLD_REQUIREMENTS
                    )
                    identity = CONTROL_PLANE_IDENTITY_STATE.declaration()
                    assert (identity in current.declaration.capabilities) is (damage == "facts")
                    assert (
                        await identity_schema.control_plane_identity_schema_matches_contract(
                            connection
                        )
                    ) is (damage == "declaration")
            unit_of_work = _ObservedProposalUnitOfWork(runtime)
            message = "does not provide" if damage == "declaration" else "identity schema facts"
            with pytest.raises(DatabaseCapabilityUnavailable, match=message):
                async with unit_of_work:
                    pytest.fail("unqualified repository was exposed")
            assert not unit_of_work.initialized
            with pytest.raises(RuntimeError, match="not active"):
                _ = unit_of_work.proposal_reviews
            assert session_queries == []
            async with admin.connect() as connection:
                for table in (
                    repository_attestation_transactions,
                    workflow_proposal_reviews,
                    config_epochs,
                    audit_events,
                ):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
        finally:
            event.remove(runtime.sync_engine, "before_execute", observe)
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


async def _damage_identity(engine: AsyncEngine, damage: Literal["declaration", "facts"]) -> None:
    profile = load_bundled_profile()
    async with engine.connect() as raw:
        connection = await configure_read_committed(raw, profile)
        async with connection.begin():
            await verify_read_committed(connection, profile)
            await acquire_compatibility_fence(connection, profile, CompatibilityFenceMode.MIGRATION)
            if damage == "facts":
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.control_plane_sessions DROP CONSTRAINT "
                        "ck_control_plane_sessions_time_order"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.control_plane_sessions ADD CONSTRAINT "
                        "ck_control_plane_sessions_time_order CHECK (isfinite(issued_at) "
                        "AND isfinite(expires_at) AND expires_at > issued_at "
                        "AND expires_at <= issued_at + INTERVAL '16 minutes')"
                    )
                )
                return
            current = (await load_current_compatibility(connection, profile)).declaration
            identity = CONTROL_PLANE_IDENTITY_STATE.declaration()
            retained = tuple(item for item in current.capabilities if item != identity)
            assert set(current.capabilities) - set(retained) == {identity}
            assert capabilities_cover(_OLD_REQUIREMENTS, retained)
            successor = build_declaration(
                profile,
                generation=current.generation + 1,
                revision_id="20991231_9999",
                parent_revision_id=current.revision_id,
                transition_kind="contract",
                capabilities=retained,
            )

            def publish(sync: Connection) -> None:
                apply_forward_declaration(
                    sync,
                    profile,
                    previous_revision_id=current.revision_id,
                    proposed=successor,
                    attest_resulting_capabilities=lambda declared: attest_resulting_capabilities(
                        sync, declared
                    ),
                )
                sync.execute(
                    text("UPDATE public.alembic_version SET version_num = :revision"),
                    {"revision": successor.revision_id},
                )

            await connection.run_sync(publish)


def _observe_identity_fence(monkeypatch: pytest.MonkeyPatch) -> list[Connection]:
    original = identity_schema.control_plane_identity_schema_matches_contract_sync
    calls: list[Connection] = []
    profile = load_bundled_profile()

    def attest(connection: Connection) -> bool:
        assert connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks "
                "WHERE locktype = 'advisory' AND pid = pg_catalog.pg_backend_pid() "
                "AND classid::bigint = :class_id AND objid::bigint = :object_id "
                "AND objsubid = 2 AND mode = 'ShareLock' AND granted)"
            ),
            {
                "class_id": profile.fence_class_id & 0xFFFFFFFF,
                "object_id": profile.fence_object_id & 0xFFFFFFFF,
            },
        )
        calls.append(connection)
        return original(connection)

    monkeypatch.setattr(
        identity_schema, "control_plane_identity_schema_matches_contract_sync", attest
    )
    return calls


def test_exact_epoch_attests_before_the_same_session_query_and_commits_registration(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _ensure_control_plane_session(engine)
            calls = _observe_identity_fence(monkeypatch)
            session_queries: list[Connection] = []

            def observe(
                connection: Connection,
                clause: ClauseElement,
                _multiparams: object,
                _params: object,
                _options: object,
            ) -> None:
                if (
                    isinstance(clause, Select)
                    and control_plane_sessions in clause.get_final_froms()
                ):
                    assert calls == [connection]
                    session_queries.append(connection)

            event.listen(engine.sync_engine, "before_execute", observe)
            try:
                unit_of_work = _ObservedProposalUnitOfWork(engine)
                assert not unit_of_work.initialized
                async with unit_of_work:
                    assert unit_of_work.initialized and len(calls) == 1
                    now = await unit_of_work.proposal_reviews.current_time()
                    attestation = _attestation(
                        _review(target=_draft("identity-admitted")),
                        issued_at=now - timedelta(seconds=1),
                        observed_at=now,
                    )
                    result = await unit_of_work.proposal_reviews.register_attestation(
                        attestation.transaction
                    )
                    assert isinstance(result, RepositoryAttestationRegistered)
                    await unit_of_work.commit()
                assert session_queries == calls and len(calls) == 1
            finally:
                event.remove(engine.sync_engine, "before_execute", observe)
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(repository_attestation_transactions)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_existing_identity_operations_invoke_one_catalog_attestor(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _observe_identity_fence(monkeypatch)

    async def one(operation: Callable[[], Awaitable[object]]) -> object:
        before = len(calls)
        result = await operation()
        assert len(calls) == before + 1
        return result

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        sessions = PostgresControlPlaneSessionStore(engine)
        activity = PostgresActivityStore(engine, ActivityCursorCodec(b"a" * 32))
        try:
            now = await one(sessions.current_time)
            assert isinstance(now, datetime)
            principal = replace(human_principal(), expires_at=now + timedelta(minutes=5))
            record = ControlPlaneSessionRecord(
                handle_digest=b"c" * 32,
                issuer=principal.issuer,
                subject=principal.subject,
                keycloak_session_id=principal.keycloak_session_id,
                actor_id=principal.actor_id,
                roles=principal.roles,
                authority_profile_digest=principal.authority_profile_digest,
                issued_at=now,
                expires_at=principal.expires_at,
                display=principal.display,
            )
            await one(lambda: sessions.replace(previous_handle_digest=None, record=record))
            assert await one(lambda: sessions.load(record.handle_digest)) == record
            await one(lambda: sessions.delete(record.handle_digest))
            evidence = BackChannelLogoutEvidence(
                issuer=record.issuer,
                token_id="identity-catalog-once",
                issued_at=now,
                expires_at=now + timedelta(minutes=1),
                target=BackChannelLogoutTarget(subject=record.subject),
            )
            await one(
                lambda: PostgresBackChannelLogoutStore(engine).consume_and_delete(
                    evidence=evidence, replay_retained_until=evidence.expires_at
                )
            )
            since = (now - timedelta(minutes=1)).replace(microsecond=0)
            until = (now + timedelta(minutes=1)).replace(microsecond=0)
            for query in (
                ActivityQuery("security", since, until, issuer=record.issuer),
                ActivityQuery("business", since, until, scope=RepositoryScope(7, 11)),
            ):
                before = len(calls)
                await activity.page(query, principal, None)
                assert len(calls) == before + 1
            await one(lambda: activity.login_diagnostic("login_rejected"))
            await one(lambda: activity.principal_diagnostic(principal, "role_denied"))
            await one(activity.cleanup)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
