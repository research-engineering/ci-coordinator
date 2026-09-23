from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditEventInput,
    append_event,
    prepare_audit_event,
)
from ci_coordinator.config_epochs import (
    ConfigEpochRegistrationCommand,
    ConfigEpochRegistrationCommitted,
    prepare_config_epoch_registration,
)
from ci_coordinator.persistence import PostgresUnitOfWork
from ci_coordinator.persistence.audit_codec import prepared_record_to_row
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable

from ._config_epoch_support import config_epoch_draft
from .conftest import alembic_config

pytestmark = pytest.mark.persistence

_BASELINE_REVISION = "20260716_0001"
_REGISTRATION_REVISION = "20260901_0002"
_ECONOMICS_REVISION = "20260904_0003"
_HEAD_REVISION = "20260915_0015"
_EXTRA_REVISION = "20991231_9998"
_BASELINE_SCHEMA_TABLES = {
    "active_config_epochs",
    "audit_events",
    "audit_ledger_head",
    "config_epoch_activations",
    "config_epochs",
    "control_plane_logout_replays",
    "control_plane_sessions",
    "database_compatibility_capabilities",
    "database_compatibility_declarations",
    "governance_baseline_operations",
    "governance_baselines",
    "issued_plan_envelopes",
    "operator_overrides",
    "production_admission_authorities",
    "production_admission_scope_bindings",
    "reconciliation_observations",
    "reconciliation_results",
    "reconciliation_subjects",
    "repository_attestation_transactions",
    "shadow_evidence",
    "webhook_deliveries",
    "workflow_proposal_reviews",
}
_SCHEMA_TABLES = _BASELINE_SCHEMA_TABLES | {
    "ci_workflow_attempt_collections",
    "ci_workflow_attempt_snapshot_jobs",
    "ci_workflow_attempt_snapshots",
    "ci_workflow_observations",
    "config_epoch_registrations",
}
_BASELINE_SCHEMA_CAPABILITIES = {
    "audit-ledger/v1",
    "config-epoch-lifecycle/v1",
    "control-plane-identity-state/v1",
    "database-compatibility-protocol/v1",
    "governance-baseline-state/v1",
    "operator-override-state/v1",
    "proposal-review-registration/v1",
    "runtime-ingress-issuance-state/v1",
    "runtime-shadow-reconciliation-state/v1",
    "webhook-body-identity/v1",
}
_SCHEMA_CAPABILITIES = _BASELINE_SCHEMA_CAPABILITIES | {
    "ci-economics-evidence/v1",
    "config-epoch-registration-operations/v1",
}


def test_fresh_baseline_upgrade_attests_its_catalog(unmigrated_database_url: str) -> None:
    command.upgrade(alembic_config(unmigrated_database_url), _BASELINE_REVISION)
    assert asyncio.run(_schema_facts(unmigrated_database_url)) == _expected_schema_facts(head=False)


def test_fresh_bootstrap_contains_webhook_body_identity(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _HEAD_REVISION
            )
            assert connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 "
                    "FROM ci_coordinator.database_compatibility_capabilities "
                    "WHERE revision_id = :revision_id "
                    "AND capability_id = 'webhook-body-identity/v1')"
                ),
                {"revision_id": _HEAD_REVISION},
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ci_coordinator.webhook_deliveries "
                    "(delivery_id, body_sha256) VALUES ('bootstrap-unique-1', :body)"
                ),
                {"body": "a" * 64},
            )
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ci_coordinator.webhook_deliveries "
                    "(delivery_id, body_sha256) VALUES ('bootstrap-unique-2', :body)"
                ),
                {"body": "a" * 64},
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM ci_coordinator.webhook_deliveries"))
        engine.dispose()


def test_baseline_upgrade_downgrade_round_trip_is_exact(unmigrated_database_url: str) -> None:
    database_url = unmigrated_database_url
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    _reset_to_baseline_only(database_url)
    command.downgrade(config, "base")
    assert asyncio.run(_base_facts(database_url)) == (False, ())
    command.upgrade(config, _BASELINE_REVISION)
    assert asyncio.run(_schema_facts(database_url)) == _expected_schema_facts(head=False)

    command.downgrade(config, "base")
    assert asyncio.run(_base_facts(database_url)) == (False, ())

    _harden_role_global_defaults(database_url)
    hardened_defaults = asyncio.run(_base_facts(database_url))[1]
    assert len(hardened_defaults) == 2
    try:
        command.upgrade(config, _BASELINE_REVISION)
        assert asyncio.run(_schema_facts(database_url)) == _expected_schema_facts(
            hardened_defaults,
            head=False,
        )
        command.downgrade(config, "base")
        assert asyncio.run(_base_facts(database_url)) == (False, hardened_defaults)
    finally:
        command.downgrade(config, "base")
        _restore_builtin_role_global_defaults(database_url)
        command.upgrade(config, "head")


def test_expand_upgrade_downgrade_and_exact_replay_preserve_the_declared_chain(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)

    command.upgrade(config, _ECONOMICS_REVISION)
    assert asyncio.run(_schema_facts(postgres_database_url)) == _expected_schema_facts()

    command.downgrade(config, _BASELINE_REVISION)
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _BASELINE_REVISION
            )
            assert connection.scalar(
                text("SELECT to_regclass('ci_coordinator.config_epoch_registrations') IS NULL")
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ci_coordinator.database_compatibility_declarations")
                )
                == 3
            )
        command.upgrade(config, _ECONOMICS_REVISION)
        assert asyncio.run(_schema_facts(postgres_database_url)) == _expected_schema_facts()
    finally:
        command.upgrade(config, "head")
        engine.dispose()


def test_expand_migration_rejects_drift_in_any_retained_capability(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    engine = create_engine(postgres_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE ci_coordinator.control_plane_sessions "
                    "ADD COLUMN migration_drift_probe integer"
                )
            )

        with pytest.raises(
            RuntimeError,
            match="resulting capability does not attest: control-plane-identity-state/v1",
        ):
            command.upgrade(config, _ECONOMICS_REVISION)

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _BASELINE_REVISION
            )
            assert connection.scalar(
                text("SELECT to_regclass('ci_coordinator.config_epoch_registrations') IS NULL")
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ci_coordinator.database_compatibility_declarations")
                )
                == 1
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE ci_coordinator.control_plane_sessions "
                    "DROP COLUMN IF EXISTS migration_drift_probe"
                )
            )
        command.upgrade(config, "head")
        engine.dispose()


def test_expand_downgrade_rejects_drift_in_any_retained_capability(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    command.upgrade(config, _ECONOMICS_REVISION)
    engine = create_engine(postgres_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE ci_coordinator.control_plane_sessions "
                    "ADD COLUMN downgrade_drift_probe integer"
                )
            )

        with pytest.raises(
            RuntimeError,
            match="resulting capability does not attest: control-plane-identity-state/v1",
        ):
            command.downgrade(config, _BASELINE_REVISION)

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _ECONOMICS_REVISION
            )
            assert connection.scalar(
                text("SELECT to_regclass('ci_coordinator.config_epoch_registrations') IS NOT NULL")
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE ci_coordinator.control_plane_sessions "
                    "DROP COLUMN IF EXISTS downgrade_drift_probe"
                )
            )
        command.upgrade(config, "head")
        engine.dispose()


def test_expand_downgrade_refuses_retained_registration_receipts(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    command.upgrade(config, _ECONOMICS_REVISION)

    async def retain_registration() -> None:
        draft = config_epoch_draft()
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                audit = _PostgresAuditEventRepository(connection, lambda: None, lambda: None)
                repository = _PostgresConfigEpochRepository(
                    connection, audit, lambda: None, lambda: None
                )
                result = await repository.register_operation(
                    prepare_config_epoch_registration(
                        ConfigEpochRegistrationCommand(
                            draft=draft,
                            operation_id="retained-before-downgrade",
                            actor="keycloak-human:v1:" + "a" * 64,
                            occurred_at="2026-09-01T10:00:00.000Z",
                        )
                    )
                )
                assert isinstance(result, ConfigEpochRegistrationCommitted)
        finally:
            await engine.dispose()

    try:
        asyncio.run(retain_registration())
        with pytest.raises(RuntimeError, match="receipts forbid destructive downgrade"):
            command.downgrade(config, _BASELINE_REVISION)
    finally:
        command.upgrade(config, "head")


def test_baseline_downgrade_rejects_unknown_schema_objects(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    engine = create_engine(postgres_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE ci_coordinator.unknown_downgrade_probe (id int)"))

        with pytest.raises(RuntimeError, match="baseline catalog attestation failed"):
            command.downgrade(config, "base")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT to_regclass('ci_coordinator.unknown_downgrade_probe') IS NOT NULL")
            )
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _BASELINE_REVISION
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS ci_coordinator.unknown_downgrade_probe"))
        command.upgrade(config, "head")
        engine.dispose()


def test_baseline_downgrade_rejects_unclassified_schema_objects(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    engine = create_engine(postgres_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE COLLATION ci_coordinator.unknown_downgrade_collation "
                    'FROM pg_catalog."C"'
                )
            )

        with pytest.raises(DBAPIError):
            command.downgrade(config, "base")

        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT to_regcollation("
                    "'ci_coordinator.unknown_downgrade_collation') IS NOT NULL"
                )
            )
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _BASELINE_REVISION
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DROP COLLATION IF EXISTS ci_coordinator.unknown_downgrade_collation")
            )
        command.upgrade(config, "head")
        engine.dispose()


def test_baseline_downgrade_rejects_extra_declarations(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    engine = create_engine(postgres_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ci_coordinator.database_compatibility_declarations "
                    "(generation, revision_id, parent_revision_id, transition_kind, lineage_id, "
                    "protocol_version, declaration_hash) VALUES "
                    "(2, :revision_id, :parent_revision_id, 'expand', "
                    "'ci-coordinator-postgresql/v1', 1, :declaration_hash)"
                ),
                {
                    "revision_id": _EXTRA_REVISION,
                    "parent_revision_id": _BASELINE_REVISION,
                    "declaration_hash": "0" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO ci_coordinator.database_compatibility_capabilities "
                    "(revision_id, capability_id, descriptor_hash) "
                    "VALUES (:revision_id, 'downgrade-probe/v1', :descriptor_hash)"
                ),
                {"revision_id": _EXTRA_REVISION, "descriptor_hash": "1" * 64},
            )

        with pytest.raises(RuntimeError, match="non-exact baseline declaration"):
            command.downgrade(config, "base")

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ci_coordinator.database_compatibility_declarations")
                )
                == 2
            )
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _BASELINE_REVISION
            )
    finally:
        _remove_extra_declaration(engine)
        command.upgrade(config, "head")
        engine.dispose()


def test_undeclared_current_head_is_rejected_before_repository_exposure(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text("UPDATE public.alembic_version SET version_num = '20991231_9999'")
                )
            with pytest.raises(DatabaseCapabilityUnavailable, match="no compatibility declaration"):
                await PostgresUnitOfWork(runtime_engine).__aenter__()
        finally:
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text("UPDATE public.alembic_version SET version_num = :revision"),
                    {"revision": _HEAD_REVISION},
                )
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_baseline_downgrade_refuses_retained_audit_events(
    postgres_database_url: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    engine = create_engine(postgres_database_url)

    try:
        _insert_audit_event(
            engine,
            AuditEventInput(
                idempotency_key="baseline-retained",
                subject_type="dynamic-ci-plan",
                subject_id="plan-baseline-retained",
                event_type="plan.persisted",
                created_at="2026-07-16T12:00:00.000Z",
                actor="test-suite",
                payload={"retained": True},
            ),
        )
        with pytest.raises(RuntimeError, match="refusing to remove retained CI Coordinator data"):
            command.downgrade(config, "base")
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM ci_coordinator.audit_events"))
        engine.dispose()
        command.downgrade(config, "base")
        command.upgrade(config, "head")


@pytest.mark.parametrize("retained_state", ["session", "logout-replay"])
def test_baseline_downgrade_refuses_retained_control_plane_identity(
    postgres_database_url: str,
    retained_state: str,
) -> None:
    config = alembic_config(postgres_database_url)
    _reset_to_baseline_only(postgres_database_url)
    engine = create_engine(postgres_database_url)
    try:
        with engine.begin() as connection:
            if retained_state == "session":
                connection.execute(
                    text(
                        "INSERT INTO ci_coordinator.control_plane_sessions "
                        "(handle_digest, issuer, subject, keycloak_sid, actor_id, roles, "
                        "profile_digest, issued_at, expires_at) VALUES "
                        "(:handle_digest, 'https://identity.example/realms/control-plane', "
                        "'subject-a', 'sid-a', :actor_id, 16, :profile_digest, "
                        "statement_timestamp(), "
                        "statement_timestamp() + INTERVAL '5 minutes')"
                    ),
                    {
                        "handle_digest": b"s" * 32,
                        "actor_id": f"keycloak-human:v1:{'a' * 64}",
                        "profile_digest": "b" * 64,
                    },
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO ci_coordinator.control_plane_logout_replays "
                        "(issuer, jti, retain_until) VALUES "
                        "('https://identity.example/realms/control-plane', "
                        "'logout-token', statement_timestamp() + INTERVAL '5 minutes')"
                    )
                )

        with pytest.raises(RuntimeError, match="refusing to remove retained CI Coordinator data"):
            command.downgrade(config, "base")
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM ci_coordinator.control_plane_logout_replays"))
            connection.execute(text("DELETE FROM ci_coordinator.control_plane_sessions"))
        engine.dispose()
        command.downgrade(config, "base")
        command.upgrade(config, "head")


def test_destructive_downgrade_waits_for_participant_and_preserves_retained_data(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        unit_of_work = PostgresUnitOfWork(runtime_engine)
        try:
            await unit_of_work.__aenter__()
            result = await unit_of_work.audit_events.append(
                prepare_audit_event(
                    AuditEventInput(
                        idempotency_key="concurrent-retained",
                        subject_type="dynamic-ci-plan",
                        subject_id="plan-concurrent-retained",
                        event_type="plan.persisted",
                        created_at="2026-07-16T12:00:00.000Z",
                        actor="test-suite",
                        payload={"retained": True},
                    )
                )
            )
            assert isinstance(result, AuditAppendAppended)
            downgrade = asyncio.create_task(
                asyncio.to_thread(
                    command.downgrade,
                    alembic_config(postgres_database_url),
                    "base",
                )
            )
            await _wait_until_exclusive_fence_is_blocked(migration_engine, downgrade)
            await unit_of_work.commit()
            await unit_of_work.__aexit__(None, None, None)
            with pytest.raises(
                RuntimeError,
                match="requires forward repair or admitted restore",
            ):
                await downgrade
            async with migration_engine.connect() as connection:
                assert (
                    await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                    == _HEAD_REVISION
                )
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM ci_coordinator.audit_events")
                    )
                    == 1
                )
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


async def _schema_facts(database_url: str) -> tuple[object, ...]:
    engine = create_postgres_engine(database_url)
    try:
        async with engine.connect() as connection:
            names = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names(schema="ci_coordinator"))
            )
            sequence_default = await connection.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = 'ci_coordinator' AND table_name = 'audit_events' "
                    "AND column_name = 'sequence'"
                )
            )
            sequence_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class AS relation "
                    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'ci_coordinator' "
                    "AND relation.relkind = 'S' AND relation.relname LIKE 'audit_%'"
                )
            )
            unique_constraints = {
                row[0]
                for row in await connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'ci_coordinator.audit_events'::regclass "
                        "AND contype = 'u'"
                    )
                )
            }
            declaration = tuple(
                await connection.execute(
                    text(
                        "SELECT generation, revision_id, parent_revision_id, transition_kind "
                        "FROM ci_coordinator.database_compatibility_declarations "
                        "ORDER BY generation"
                    )
                )
            )
            capabilities = {
                row[0]
                for row in await connection.execute(
                    text(
                        "SELECT capability_id "
                        "FROM ci_coordinator.database_compatibility_capabilities"
                    )
                )
            }
            default_acl_rows = tuple(
                await connection.execute(
                    text(
                        "SELECT defaclobjtype::text, defaclnamespace FROM pg_default_acl "
                        "WHERE defaclrole = ("
                        "SELECT oid FROM pg_roles WHERE rolname = current_user"
                        ") AND defaclnamespace = 0 AND defaclobjtype IN ('f', 'T') "
                        "ORDER BY CASE defaclobjtype WHEN 'T' THEN 1 ELSE 2 END"
                    )
                )
            )
    finally:
        await engine.dispose()
    return (
        names,
        sequence_default,
        sequence_count,
        unique_constraints,
        declaration,
        capabilities,
        default_acl_rows,
    )


def _expected_schema_facts(
    default_acl_rows: tuple[object, ...] = (),
    *,
    head: bool = True,
) -> tuple[object, ...]:
    declarations = (
        (
            (1, _BASELINE_REVISION, None, "bootstrap"),
            (2, _REGISTRATION_REVISION, _BASELINE_REVISION, "expand"),
            (3, _ECONOMICS_REVISION, _REGISTRATION_REVISION, "expand"),
        )
        if head
        else ((1, _BASELINE_REVISION, None, "bootstrap"),)
    )
    return (
        _SCHEMA_TABLES if head else _BASELINE_SCHEMA_TABLES,
        None,
        0,
        {
            "uq_audit_events_audit_event_id",
            "uq_audit_events_event_hash",
            "uq_audit_events_idempotency_digest",
        },
        declarations,
        _SCHEMA_CAPABILITIES if head else _BASELINE_SCHEMA_CAPABILITIES,
        default_acl_rows,
    )


def _insert_audit_event(engine: Engine, event_input: AuditEventInput) -> None:
    prepared = prepare_audit_event(event_input)
    appended = append_event((), event_input)
    assert isinstance(appended, AuditAppendAppended)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ci_coordinator.audit_events "
                "(sequence, idempotency_key, idempotency_key_digest, subject_type, "
                "subject_id, event_type, created_at, actor, payload_canonical_json, "
                "schema_version, audit_event_id, previous_event_hash, payload_hash, "
                "input_hash, event_hash) VALUES "
                "(:sequence, :idempotency_key, :idempotency_key_digest, :subject_type, "
                ":subject_id, :event_type, :created_at, :actor, :payload_canonical_json, "
                ":schema_version, :audit_event_id, :previous_event_hash, :payload_hash, "
                ":input_hash, :event_hash)"
            ),
            prepared_record_to_row(appended.record, prepared),
        )


async def _base_facts(database_url: str) -> tuple[bool, tuple[object, ...]]:
    engine = create_postgres_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ci_coordinator')")
            )
            default_acl_rows = tuple(
                await connection.execute(
                    text(
                        "SELECT defaclobjtype::text, defaclnamespace FROM pg_default_acl "
                        "WHERE defaclrole = ("
                        "SELECT oid FROM pg_roles WHERE rolname = current_user"
                        ") AND defaclnamespace = 0 AND defaclobjtype IN ('f', 'T') "
                        "ORDER BY CASE defaclobjtype WHEN 'T' THEN 1 ELSE 2 END, 2"
                    )
                )
            )
    finally:
        await engine.dispose()
    return schema_exists is True, default_acl_rows


def _reset_to_baseline_only(database_url: str) -> None:
    """Recreate the disposable pre-release bootstrap state."""
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS ci_coordinator CASCADE"))
            connection.execute(text("DELETE FROM public.alembic_version"))
    finally:
        engine.dispose()
    command.upgrade(alembic_config(database_url), _BASELINE_REVISION)


def _harden_role_global_defaults(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
            )
            connection.execute(text("ALTER DEFAULT PRIVILEGES REVOKE USAGE ON TYPES FROM PUBLIC"))
    finally:
        engine.dispose()


def _restore_builtin_role_global_defaults(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC")
            )
            connection.execute(text("ALTER DEFAULT PRIVILEGES GRANT USAGE ON TYPES TO PUBLIC"))
    finally:
        engine.dispose()


def _remove_extra_declaration(engine: Engine) -> None:
    with engine.begin() as connection:
        declaration_table = connection.scalar(
            text("SELECT to_regclass('ci_coordinator.database_compatibility_declarations')")
        )
        if declaration_table is None:
            return
        connection.execute(
            text(
                "ALTER TABLE ci_coordinator.database_compatibility_capabilities "
                "DISABLE TRIGGER tr_database_compatibility_capabilities_immutable"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE ci_coordinator.database_compatibility_declarations "
                "DISABLE TRIGGER tr_database_compatibility_declarations_immutable"
            )
        )
        connection.execute(
            text(
                "DELETE FROM ci_coordinator.database_compatibility_capabilities "
                "WHERE revision_id = :revision_id"
            ),
            {"revision_id": _EXTRA_REVISION},
        )
        connection.execute(
            text(
                "DELETE FROM ci_coordinator.database_compatibility_declarations "
                "WHERE revision_id = :revision_id"
            ),
            {"revision_id": _EXTRA_REVISION},
        )
        connection.execute(
            text(
                "ALTER TABLE ci_coordinator.database_compatibility_capabilities "
                "ENABLE TRIGGER tr_database_compatibility_capabilities_immutable"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE ci_coordinator.database_compatibility_declarations "
                "ENABLE TRIGGER tr_database_compatibility_declarations_immutable"
            )
        )


async def _wait_until_exclusive_fence_is_blocked(
    engine: AsyncEngine,
    task: asyncio.Task[object],
) -> None:
    async with asyncio.timeout(2):
        while True:
            if task.done():
                raise AssertionError("migration did not wait for the active participant")
            async with engine.connect() as observer:
                blocked = await observer.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' "
                        "AND query LIKE 'SELECT pg_catalog.pg_advisory_xact_lock(%')"
                    )
                )
            if blocked:
                return
            await asyncio.sleep(0.01)
