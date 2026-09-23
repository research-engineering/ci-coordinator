"""Fenced installation and verification of the exact PostgreSQL runtime ACL."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, LiteralString

from psycopg import Connection, Cursor, sql

from ci_coordinator.persistence.ci_economics_collection_codec import (
    COLLECTION_TRANSITION_COLUMNS,
)
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityProfile,
    load_bundled_profile,
)

type DatabaseRow = tuple[object, ...]
type AccessFailureCode = Literal[
    "migration_principal_invalid",
    "runtime_access_mismatch",
    "runtime_role_invalid",
    "runtime_role_not_drained",
    "runtime_role_unsafe",
]

_ROLE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
RUNTIME_TABLE_PRIVILEGES: Final = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
RUNTIME_COLUMN_PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
RUNTIME_RELATION_KINDS: Final = ("r", "p", "v", "m", "f")
_SEQUENCE_PRIVILEGES: Final = ("USAGE", "SELECT", "UPDATE")
RUNTIME_ACTIVITY_SEQUENCE: Final = "activity_events_sequence_seq"

RUNTIME_TABLE_GRANTS: Final[Mapping[str, tuple[LiteralString, ...]]] = MappingProxyType(
    {
        "database_compatibility_declarations": ("SELECT",),
        "database_compatibility_capabilities": ("SELECT",),
        "audit_events": ("SELECT", "INSERT"),
        "audit_ledger_head": ("SELECT", "UPDATE"),
        "config_epochs": ("SELECT", "INSERT"),
        "active_config_epochs": ("SELECT", "INSERT", "UPDATE"),
        "config_epoch_activations": ("SELECT", "INSERT"),
        "workflow_proposal_reviews": ("SELECT", "INSERT"),
        "repository_attestation_transactions": ("SELECT", "INSERT", "DELETE"),
        "governance_baselines": ("SELECT", "INSERT"),
        "governance_baseline_operations": ("SELECT", "INSERT"),
        "control_plane_sessions": ("SELECT", "INSERT", "DELETE"),
        "activity_events": ("SELECT", "INSERT", "DELETE"),
        "activity_diagnostic_buckets": ("SELECT", "INSERT", "DELETE"),
        "control_plane_logout_replays": ("INSERT", "DELETE"),
        "webhook_deliveries": ("SELECT", "INSERT"),
        "ci_workflow_observations": ("SELECT", "INSERT", "DELETE"),
        "ci_workflow_attempt_collections": ("SELECT", "INSERT", "DELETE"),
        "ci_workflow_attempt_snapshots": ("SELECT", "INSERT", "DELETE"),
        "ci_workflow_attempt_snapshot_jobs": ("SELECT", "INSERT"),
        "ci_job_measurement_reports": ("SELECT", "INSERT", "DELETE"),
        "ci_economics_budget_policies": ("SELECT", "INSERT"),
        "ci_economics_budget_signals": ("SELECT", "INSERT"),
        "ci_observation_subscriptions": ("SELECT", "INSERT"),
        "ci_observation_scans": ("SELECT", "INSERT", "DELETE"),
        "ci_observation_gaps": ("SELECT", "INSERT", "DELETE"),
        "analytics_purpose_settings": ("SELECT", "INSERT"),
        "ci_history_defaults": ("SELECT",),
        "ci_history_datasets": ("SELECT", "INSERT"),
        "ci_history_scans": ("SELECT", "INSERT"),
        "ci_history_attempts": ("SELECT", "INSERT"),
        "ci_history_jobs": ("SELECT", "INSERT"),
        "ci_history_details": ("SELECT", "INSERT", "DELETE"),
        "ci_history_gaps": ("SELECT", "INSERT"),
        "ci_history_rechecks": ("SELECT", "INSERT", "DELETE"),
        "ci_history_delivery_inbox": ("SELECT", "INSERT"),
        "issued_plan_envelopes": ("SELECT", "INSERT"),
        "production_admission_authorities": ("SELECT", "INSERT"),
        "production_admission_scope_bindings": ("SELECT", "INSERT"),
        "production_evidence_bundles": ("SELECT", "INSERT"),
        "production_staged_grants": ("SELECT", "INSERT"),
        "production_scope_states": ("SELECT", "INSERT"),
    }
)


@dataclass(frozen=True, slots=True)
class ColumnGrant:
    privilege: LiteralString
    columns: tuple[str, ...]


_SHADOW_EVIDENCE_COLUMNS: Final = (
    "profile_id",
    "repository",
    "event",
    "surface",
    "record_canonical_json",
    "semantic_hash",
)
_RECONCILIATION_SUBJECT_COLUMNS: Final = (
    "subject_id",
    "installation_id",
    "repository_id",
    "subject_canonical_json",
    "contract_canonical_json",
    "identity_hash",
    "contract_hash",
    "revision",
    "created_at",
    "deadline_at",
    "next_attempt_at",
    "attempt_count",
    "max_attempts",
    "backoff_seconds",
    "max_backoff_seconds",
    "claim_generation",
    "lease_token",
    "lease_acquired_at",
    "lease_expires_at",
    "execution_origin",
    "production_generation",
    "production_authority_id",
)
_RECONCILIATION_SUBJECT_UPDATE_COLUMNS: Final = (
    "revision",
    "next_attempt_at",
    "attempt_count",
    "backoff_seconds",
    "claim_generation",
    "lease_token",
    "lease_acquired_at",
    "lease_expires_at",
)
_RECONCILIATION_OBSERVATION_COLUMNS: Final = (
    "subject_id",
    "observation_id",
    "revision",
    "observation_canonical_json",
    "semantic_hash",
)
_RECONCILIATION_RESULT_COLUMNS: Final = (
    "subject_id",
    "revision",
    "result_canonical_json",
    "semantic_hash",
)
_OPERATOR_OVERRIDE_COLUMNS: Final = (
    "override_id",
    "operation_id",
    "installation_id",
    "repository_id",
    "kind",
    "target_subject_id",
    "expires_at",
    "applied_at",
    "record_canonical_json",
    "semantic_hash",
    "audit_event_id",
    "audit_input_hash",
)
_CONTROL_PLANE_LOGOUT_REPLAY_COLUMNS: Final = (
    "issuer",
    "jti",
    "retain_until",
)
_CONTROL_PLANE_SESSION_LOCK_COLUMNS: Final = ("handle_digest",)
_CONFIG_EPOCH_REGISTRATION_COLUMNS: Final = (
    "installation_id",
    "repository_id",
    "operation_id",
    "epoch_id",
    "audit_event_id",
    "audit_input_hash",
)
_CI_ECONOMICS_COLLECTION_UPDATE_COLUMNS: Final = (
    *COLLECTION_TRANSITION_COLUMNS,
    "updated_at",
)

RUNTIME_COLUMN_GRANTS: Final[Mapping[str, tuple[ColumnGrant, ...]]] = MappingProxyType(
    {
        "analytics_purpose_settings": (
            ColumnGrant("UPDATE", ("generation", "revision", "snapshot_canonical")),
        ),
        "ci_history_datasets": (
            ColumnGrant(
                "UPDATE",
                (
                    "generation",
                    "configuration_revision",
                    "data_revision",
                    "configured_at",
                    "state",
                    "configuration_canonical",
                    "attempt_count",
                    "job_count",
                    "gap_count",
                    "canonical_bytes",
                ),
            ),
        ),
        "ci_history_scans": (
            ColumnGrant(
                "UPDATE",
                (
                    "generation",
                    "configuration_revision",
                    "revision",
                    "state_canonical",
                    "traversal_complete",
                    "next_attempt_at",
                    "lease_worker_id",
                    "lease_token",
                    "lease_acquired_at",
                    "lease_expires_at",
                ),
            ),
        ),
        "ci_history_attempts": (
            ColumnGrant(
                "UPDATE",
                (
                    "header_canonical",
                    "statistics_digest",
                    "job_count",
                    "statistics_bytes",
                    "has_conflict",
                    "detail_state",
                    "detail_first_imported_at",
                    "detail_policy_canonical",
                    "detail_policy_source",
                    "detail_policy_revision",
                    "detail_expires_at",
                ),
            ),
        ),
        "ci_history_rechecks": (
            ColumnGrant(
                "UPDATE",
                (
                    "source",
                    "revision",
                    "state_canonical",
                    "next_attempt_at",
                    "acquisition_count",
                    "lease_worker_id",
                    "lease_token",
                    "lease_acquired_at",
                    "lease_expires_at",
                ),
            ),
        ),
        "ci_history_jobs": (
            ColumnGrant(
                "UPDATE",
                ("name", "conclusion", "created_at", "started_at", "completed_at", "job_canonical"),
            ),
        ),
        "ci_observation_subscriptions": (
            ColumnGrant(
                "UPDATE",
                (
                    "revision",
                    "enabled",
                    "snapshot_digest",
                    "snapshot_canonical",
                    "next_attempt_at",
                    "preferred_lane",
                    "detail_truncated_until",
                ),
            ),
        ),
        "ci_observation_scans": (
            ColumnGrant(
                "UPDATE",
                (
                    "revision",
                    "state_canonical",
                    "next_attempt_at",
                    "lease_expires_at",
                    "last_completed_through",
                    "last_page_at",
                    "pages_seen",
                    "sources_registered",
                    "last_outcome",
                ),
            ),
        ),
        "ci_economics_budget_policies": (
            ColumnGrant("UPDATE", ("revision", "policy_digest", "policy_canonical")),
        ),
        "ci_history_delivery_inbox": (
            ColumnGrant("UPDATE", ("delivered_generation", "delivered_at")),
        ),
        "production_scope_states": (
            ColumnGrant(
                "UPDATE",
                (
                    "revision",
                    "generation",
                    "revoked_through_generation",
                    "active_authority_id",
                    "staged_authority_id",
                    "latch_override_id",
                    "latch_applied_at",
                ),
            ),
        ),
        "config_epoch_registrations": (
            ColumnGrant("SELECT", _CONFIG_EPOCH_REGISTRATION_COLUMNS),
            ColumnGrant("INSERT", _CONFIG_EPOCH_REGISTRATION_COLUMNS),
        ),
        "control_plane_logout_replays": (
            ColumnGrant("SELECT", _CONTROL_PLANE_LOGOUT_REPLAY_COLUMNS),
        ),
        "control_plane_sessions": (ColumnGrant("UPDATE", _CONTROL_PLANE_SESSION_LOCK_COLUMNS),),
        "activity_diagnostic_buckets": (ColumnGrant("UPDATE", ("count",)),),
        "shadow_evidence": (
            ColumnGrant("SELECT", _SHADOW_EVIDENCE_COLUMNS),
            ColumnGrant("INSERT", _SHADOW_EVIDENCE_COLUMNS),
        ),
        "reconciliation_subjects": (
            ColumnGrant("SELECT", _RECONCILIATION_SUBJECT_COLUMNS),
            ColumnGrant("INSERT", _RECONCILIATION_SUBJECT_COLUMNS),
            ColumnGrant("UPDATE", _RECONCILIATION_SUBJECT_UPDATE_COLUMNS),
        ),
        "reconciliation_observations": (
            ColumnGrant("SELECT", _RECONCILIATION_OBSERVATION_COLUMNS),
            ColumnGrant("INSERT", _RECONCILIATION_OBSERVATION_COLUMNS),
        ),
        "reconciliation_results": (
            ColumnGrant("SELECT", _RECONCILIATION_RESULT_COLUMNS),
            ColumnGrant("INSERT", _RECONCILIATION_RESULT_COLUMNS),
        ),
        "operator_overrides": (
            ColumnGrant("SELECT", _OPERATOR_OVERRIDE_COLUMNS),
            ColumnGrant("INSERT", _OPERATOR_OVERRIDE_COLUMNS),
        ),
        "ci_workflow_attempt_collections": (
            ColumnGrant("UPDATE", _CI_ECONOMICS_COLLECTION_UPDATE_COLUMNS),
        ),
    }
)


class RuntimePrincipalAccessError(ValueError):
    """A stable deployment-facing rejection without provider diagnostics."""

    def __init__(self, code: AccessFailureCode) -> None:
        super().__init__(code)
        self.code = code


def admit_runtime_role_name(value: object) -> str:
    """Admit one bounded unquoted PostgreSQL role identity."""
    if type(value) is not str or _ROLE_NAME.fullmatch(value) is None:
        raise RuntimePrincipalAccessError("runtime_role_invalid")
    return value


def apply_runtime_principal_access(
    connection: Connection[DatabaseRow],
    runtime_role: str,
    *,
    profile: CompatibilityProfile | None = None,
) -> None:
    """Replace direct object privileges and prove the exact result before commit."""
    admitted_role = admit_runtime_role_name(runtime_role)
    selected_profile = load_bundled_profile() if profile is None else profile
    with connection.cursor() as cursor:
        _configure_and_lock(cursor, selected_profile, exclusive=True)
        runtime_oid = _require_principals(
            cursor,
            admitted_role,
            selected_profile.application_schema,
        )
        _require_runtime_role_drained(cursor, runtime_oid)
        _revoke_direct_access(cursor, admitted_role, selected_profile.application_schema)
        _grant_runtime_access(cursor, admitted_role, selected_profile.application_schema)
        if not _runtime_access_is_exact(cursor, admitted_role, selected_profile):
            raise RuntimePrincipalAccessError("runtime_access_mismatch")


def check_runtime_principal_access(
    connection: Connection[DatabaseRow],
    runtime_role: str,
    *,
    profile: CompatibilityProfile | None = None,
) -> None:
    """Prove the runtime role contract without changing database state."""
    admitted_role = admit_runtime_role_name(runtime_role)
    selected_profile = load_bundled_profile() if profile is None else profile
    with connection.cursor() as cursor:
        _configure_and_lock(cursor, selected_profile, exclusive=False)
        _require_principals(cursor, admitted_role, selected_profile.application_schema)
        if not _runtime_access_is_exact(cursor, admitted_role, selected_profile):
            raise RuntimePrincipalAccessError("runtime_access_mismatch")


def _configure_and_lock(
    cursor: Cursor[DatabaseRow],
    profile: CompatibilityProfile,
    *,
    exclusive: bool,
) -> None:
    timeouts = profile.migration_timeouts if exclusive else profile.participant_timeouts
    cursor.execute(
        "SELECT pg_catalog.set_config('search_path', 'pg_catalog', true), "
        "pg_catalog.set_config('lock_timeout', %s, true), "
        "pg_catalog.set_config('statement_timeout', %s, true), "
        "pg_catalog.set_config('transaction_timeout', %s, true)",
        (
            f"{timeouts.lock_timeout_ms}ms",
            f"{timeouts.statement_timeout_ms}ms",
            f"{timeouts.transaction_timeout_ms}ms",
        ),
    )
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    cursor.execute(
        sql.SQL("SELECT pg_catalog.{}(%s, %s)").format(sql.Identifier(function)),
        (profile.fence_class_id, profile.fence_object_id),
    )


def _require_principals(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    application_schema: str,
) -> object:
    cursor.execute(
        "SELECT runtime.oid, runtime.rolcanlogin, runtime.rolinherit, runtime.rolsuper, "
        "runtime.rolcreatedb, runtime.rolcreaterole, runtime.rolreplication, "
        "runtime.rolbypassrls, namespace.nspowner, migration.oid, "
        "session_user = current_user "
        "FROM pg_catalog.pg_roles AS runtime "
        "CROSS JOIN pg_catalog.pg_namespace AS namespace "
        "JOIN pg_catalog.pg_roles AS migration ON migration.rolname = current_user "
        "WHERE runtime.rolname = %s AND namespace.nspname = %s",
        (runtime_role, application_schema),
    )
    row = cursor.fetchone()
    if row is None or len(row) != 11:
        raise RuntimePrincipalAccessError("runtime_role_unsafe")
    runtime_oid, can_login, inherits, superuser, create_db, create_role, replication, bypass_rls = (
        row[:8]
    )
    schema_owner, migration_oid, same_identity = row[8:]
    if schema_owner != migration_oid or same_identity is not True:
        raise RuntimePrincipalAccessError("migration_principal_invalid")
    if (
        runtime_oid == schema_owner
        or can_login is not True
        or inherits is not False
        or any(
            value is not False
            for value in (superuser, create_db, create_role, replication, bypass_rls)
        )
    ):
        raise RuntimePrincipalAccessError("runtime_role_unsafe")
    if _role_has_membership_edge(cursor, runtime_oid):
        raise RuntimePrincipalAccessError("runtime_role_unsafe")
    if _role_owns_application_object(cursor, runtime_oid, application_schema):
        raise RuntimePrincipalAccessError("runtime_role_unsafe")
    if _default_acl_mentions_role(cursor, runtime_oid, application_schema):
        raise RuntimePrincipalAccessError("runtime_role_unsafe")
    return runtime_oid


def _require_runtime_role_drained(
    cursor: Cursor[DatabaseRow],
    runtime_role_oid: object,
) -> None:
    cursor.execute(
        "SELECT NOT EXISTS ("
        "SELECT 1 FROM pg_catalog.pg_stat_activity "
        "WHERE datid = (SELECT oid FROM pg_catalog.pg_database "
        "WHERE datname = pg_catalog.current_database()) "
        "AND usesysid = %s AND pid <> pg_catalog.pg_backend_pid())",
        (runtime_role_oid,),
    )
    if cursor.fetchone() != (True,):
        raise RuntimePrincipalAccessError("runtime_role_not_drained")


def _role_has_membership_edge(cursor: Cursor[DatabaseRow], role_oid: object) -> bool:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE roleid = %s OR member = %s)",
        (role_oid, role_oid),
    )
    return cursor.fetchone() != (False,)


def _role_owns_application_object(
    cursor: Cursor[DatabaseRow],
    role_oid: object,
    application_schema: str,
) -> bool:
    cursor.execute(
        "SELECT EXISTS ("
        "SELECT 1 FROM pg_catalog.pg_class AS relation "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
        "WHERE relation.relowner = %s AND (namespace.nspname = %s OR "
        "(namespace.nspname = 'public' AND relation.relname = 'alembic_version')) "
        "UNION ALL "
        "SELECT 1 FROM pg_catalog.pg_proc AS routine "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
        "WHERE routine.proowner = %s AND namespace.nspname = %s "
        "UNION ALL "
        "SELECT 1 FROM pg_catalog.pg_type AS type_metadata "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = type_metadata.typnamespace "
        "WHERE type_metadata.typowner = %s AND namespace.nspname = %s)",
        (role_oid, application_schema, role_oid, application_schema, role_oid, application_schema),
    )
    return cursor.fetchone() != (False,)


def _default_acl_mentions_role(
    cursor: Cursor[DatabaseRow],
    role_oid: object,
    application_schema: str,
) -> bool:
    cursor.execute(
        "SELECT EXISTS ("
        "SELECT 1 FROM pg_catalog.pg_default_acl AS default_acl "
        "LEFT JOIN pg_catalog.pg_namespace AS namespace "
        "ON namespace.oid = default_acl.defaclnamespace "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(default_acl.defaclacl) AS privilege "
        "WHERE (default_acl.defaclnamespace = 0 OR namespace.nspname = %s) "
        "AND privilege.grantee = %s)",
        (application_schema, role_oid),
    )
    return cursor.fetchone() != (False,)


def _revoke_direct_access(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    application_schema: str,
) -> None:
    role = sql.Identifier(runtime_role)
    schema = sql.Identifier(application_schema)
    for statement in (
        sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(schema, role),
        sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(schema, role),
        sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(schema, role),
        sql.SQL("REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA {} FROM {}").format(schema, role),
        sql.SQL("REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM {}").format(role),
    ):
        cursor.execute(statement)
    cursor.execute(
        "SELECT type_metadata.typname "
        "FROM pg_catalog.pg_type AS type_metadata "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = type_metadata.typnamespace "
        "WHERE namespace.nspname = %s AND type_metadata.typelem = 0 "
        "ORDER BY type_metadata.typname",
        (application_schema,),
    )
    for row in cursor.fetchall():
        type_name = row[0]
        if type(type_name) is not str:
            raise RuntimePrincipalAccessError("runtime_access_mismatch")
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON TYPE {}.{} FROM {}").format(
                schema,
                sql.Identifier(type_name),
                role,
            )
        )


def _grant_runtime_access(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    application_schema: str,
) -> None:
    role = sql.Identifier(runtime_role)
    schema = sql.Identifier(application_schema)
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role))
    for relation, privileges in RUNTIME_TABLE_GRANTS.items():
        cursor.execute(
            sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(
                _keywords(privileges),
                schema,
                sql.Identifier(relation),
                role,
            )
        )
    for relation, grants in RUNTIME_COLUMN_GRANTS.items():
        clauses = sql.SQL(", ").join(
            sql.SQL("{} ({})").format(
                sql.SQL(grant.privilege),
                _identifiers(grant.columns),
            )
            for grant in grants
        )
        cursor.execute(
            sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(
                clauses,
                schema,
                sql.Identifier(relation),
                role,
            )
        )
    cursor.execute(sql.SQL("GRANT SELECT ON TABLE public.alembic_version TO {}").format(role))
    cursor.execute(
        sql.SQL("GRANT USAGE ON SEQUENCE {}.{} TO {}").format(
            schema, sql.Identifier(RUNTIME_ACTIVITY_SEQUENCE), role
        )
    )


def _runtime_access_is_exact(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    profile: CompatibilityProfile,
) -> bool:
    return all(
        (
            _schema_access_is_exact(cursor, runtime_role, profile.application_schema),
            _relation_access_is_exact(cursor, runtime_role, profile.application_schema),
            _column_access_is_exact(cursor, runtime_role, profile.application_schema),
            _non_table_access_is_absent(cursor, runtime_role, profile.application_schema),
            _migration_metadata_access_is_exact(
                cursor,
                runtime_role,
                profile.migration_head_relation,
            ),
        )
    )


def _schema_access_is_exact(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    application_schema: str,
) -> bool:
    cursor.execute(
        "SELECT pg_catalog.has_schema_privilege(%s, %s, 'USAGE'), "
        "pg_catalog.has_schema_privilege(%s, %s, 'CREATE'), "
        "pg_catalog.has_schema_privilege(%s, %s, 'USAGE WITH GRANT OPTION'), "
        "pg_catalog.has_schema_privilege(%s, %s, 'CREATE WITH GRANT OPTION')",
        (
            runtime_role,
            application_schema,
            runtime_role,
            application_schema,
            runtime_role,
            application_schema,
            runtime_role,
            application_schema,
        ),
    )
    return cursor.fetchone() == (True, False, False, False)


def _relation_access_is_exact(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    application_schema: str,
) -> bool:
    cursor.execute(
        "SELECT relation.relname, privilege.name, "
        "pg_catalog.has_table_privilege(%s, relation.oid, privilege.name), "
        "pg_catalog.has_table_privilege(%s, relation.oid, privilege.name || "
        "' WITH GRANT OPTION') "
        "FROM pg_catalog.pg_class AS relation "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
        "CROSS JOIN pg_catalog.unnest(%s::text[]) AS privilege(name) "
        'WHERE namespace.nspname = %s AND relation.relkind = ANY(%s::"char"[]) '
        "ORDER BY relation.relname, privilege.name",
        (
            runtime_role,
            runtime_role,
            list(RUNTIME_TABLE_PRIVILEGES),
            application_schema,
            list(RUNTIME_RELATION_KINDS),
        ),
    )
    rows = cursor.fetchall()
    observed: dict[tuple[str, str], tuple[object, object]] = {}
    relation_names: set[str] = set()
    for row in rows:
        relation, privilege, granted, grantable = row
        if type(relation) is not str or type(privilege) is not str:
            return False
        relation_names.add(relation)
        observed[(relation, privilege)] = (granted, grantable)
    expected_relations = set(RUNTIME_TABLE_GRANTS) | set(RUNTIME_COLUMN_GRANTS)
    if not expected_relations.issubset(relation_names):
        return False
    expected = {
        (relation, privilege): (
            privilege in RUNTIME_TABLE_GRANTS.get(relation, ()),
            False,
        )
        for relation in relation_names
        for privilege in RUNTIME_TABLE_PRIVILEGES
    }
    return observed == expected


def _column_access_is_exact(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    application_schema: str,
) -> bool:
    cursor.execute(
        "SELECT relation.relname, attribute.attname, privilege.name, "
        "pg_catalog.has_column_privilege(%s, relation.oid, attribute.attnum, privilege.name), "
        "pg_catalog.has_column_privilege(%s, relation.oid, attribute.attnum, "
        "privilege.name || ' WITH GRANT OPTION') "
        "FROM pg_catalog.pg_class AS relation "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
        "JOIN pg_catalog.pg_attribute AS attribute ON attribute.attrelid = relation.oid "
        "CROSS JOIN pg_catalog.unnest(%s::text[]) AS privilege(name) "
        'WHERE namespace.nspname = %s AND relation.relkind = ANY(%s::"char"[]) '
        "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
        "ORDER BY relation.relname, attribute.attnum, privilege.name",
        (
            runtime_role,
            runtime_role,
            list(RUNTIME_COLUMN_PRIVILEGES),
            application_schema,
            list(RUNTIME_RELATION_KINDS),
        ),
    )
    rows = cursor.fetchall()
    observed: dict[tuple[str, str, str], tuple[object, object]] = {}
    observed_columns: set[tuple[str, str]] = set()
    for row in rows:
        relation, column, privilege, granted, grantable = row
        if type(relation) is not str or type(column) is not str or type(privilege) is not str:
            return False
        observed[(relation, column, privilege)] = (granted, grantable)
        observed_columns.add((relation, column))
    expected = {
        (relation, column, privilege): (
            privilege in RUNTIME_TABLE_GRANTS.get(relation, ())
            or column in _granted_columns(relation, privilege),
            False,
        )
        for relation, column, privilege in observed
    }
    expected_columns = {
        (relation, column)
        for relation, grants in RUNTIME_COLUMN_GRANTS.items()
        for grant in grants
        for column in grant.columns
    }
    return expected_columns.issubset(observed_columns) and observed == expected


def _non_table_access_is_absent(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    application_schema: str,
) -> bool:
    cursor.execute(
        "SELECT pg_catalog.has_sequence_privilege(%s, pg_catalog.to_regclass(%s), 'USAGE')",
        (runtime_role, f"{application_schema}.{RUNTIME_ACTIVITY_SEQUENCE}"),
    )
    if cursor.fetchone() != (True,):
        return False
    checks = (
        (
            (
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class AS relation "
                "JOIN pg_catalog.pg_namespace AS namespace ON "
                "namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = %s AND relation.relkind = 'S' AND ("
                "pg_catalog.has_sequence_privilege(%s, relation.oid, 'USAGE') "
                "IS DISTINCT FROM (relation.relname = %s) OR "
                "pg_catalog.has_sequence_privilege(%s, relation.oid, 'SELECT') OR "
                "pg_catalog.has_sequence_privilege(%s, relation.oid, 'UPDATE') OR "
                "pg_catalog.has_sequence_privilege(%s, relation.oid, "
                "'USAGE WITH GRANT OPTION') OR "
                "pg_catalog.has_sequence_privilege(%s, relation.oid, "
                "'SELECT WITH GRANT OPTION') OR "
                "pg_catalog.has_sequence_privilege(%s, relation.oid, "
                "'UPDATE WITH GRANT OPTION')))"
            ),
            (
                application_schema,
                runtime_role,
                RUNTIME_ACTIVITY_SEQUENCE,
                *(runtime_role for _ in range(5)),
            ),
        ),
        (
            (
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_proc AS routine "
                "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
                "WHERE namespace.nspname = %s AND ("
                "pg_catalog.has_function_privilege(%s, routine.oid, 'EXECUTE') OR "
                "pg_catalog.has_function_privilege(%s, routine.oid, "
                "'EXECUTE WITH GRANT OPTION')))"
            ),
            (application_schema, runtime_role, runtime_role),
        ),
        (
            (
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_type AS type_metadata "
                "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = "
                "type_metadata.typnamespace WHERE namespace.nspname = %s "
                "AND type_metadata.typelem = 0 AND ("
                "pg_catalog.has_type_privilege(%s, type_metadata.oid, 'USAGE') OR "
                "pg_catalog.has_type_privilege(%s, type_metadata.oid, "
                "'USAGE WITH GRANT OPTION')))"
            ),
            (application_schema, runtime_role, runtime_role),
        ),
    )
    for statement, parameters in checks:
        cursor.execute(statement, parameters)
        if cursor.fetchone() != (False,):
            return False
    return True


def _migration_metadata_access_is_exact(
    cursor: Cursor[DatabaseRow],
    runtime_role: str,
    migration_head_relation: str,
) -> bool:
    cursor.execute(
        "SELECT privilege.name, "
        "pg_catalog.has_table_privilege(%s, pg_catalog.to_regclass(%s), privilege.name), "
        "pg_catalog.has_table_privilege(%s, pg_catalog.to_regclass(%s), "
        "privilege.name || ' WITH GRANT OPTION') "
        "FROM pg_catalog.unnest(%s::text[]) AS privilege(name) ORDER BY privilege.name",
        (
            runtime_role,
            migration_head_relation,
            runtime_role,
            migration_head_relation,
            list(RUNTIME_TABLE_PRIVILEGES),
        ),
    )
    observed = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    expected = {privilege: (privilege == "SELECT", False) for privilege in RUNTIME_TABLE_PRIVILEGES}
    return observed == expected


def _granted_columns(relation: object, privilege: object) -> tuple[str, ...]:
    if type(relation) is not str or type(privilege) is not str:
        return ()
    for grant in RUNTIME_COLUMN_GRANTS.get(relation, ()):
        if grant.privilege == privilege:
            return grant.columns
    return ()


def _identifiers(values: Iterable[str]) -> sql.Composed:
    return sql.SQL(", ").join(sql.Identifier(value) for value in values)


def _keywords(values: Iterable[LiteralString]) -> sql.Composed:
    return sql.SQL(", ").join(sql.SQL(value) for value in values)
