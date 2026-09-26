"""Live PostgreSQL fact collection for the restricted runtime principal."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.runtime_principal_access import (
    RUNTIME_ACTIVITY_SEQUENCE,
    RUNTIME_COLUMN_GRANTS,
    RUNTIME_COLUMN_PRIVILEGES,
    RUNTIME_RELATION_KINDS,
    RUNTIME_TABLE_GRANTS,
    RUNTIME_TABLE_PRIVILEGES,
)

_EXPECTED_TABLE_PRIVILEGES_JSON = json.dumps(
    [
        {
            "allowed": privilege in granted,
            "privilege_type": privilege,
            "relation_name": relation,
        }
        for relation, granted in RUNTIME_TABLE_GRANTS.items()
        for privilege in RUNTIME_TABLE_PRIVILEGES
    ],
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
)
_EXPECTED_TABLE_GRANTS_JSON = json.dumps(
    dict(RUNTIME_TABLE_GRANTS),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
)


def _column_grants_json() -> str:
    relations: dict[str, dict[str, list[str]]] = {}
    for relation, grants in RUNTIME_COLUMN_GRANTS.items():
        columns: dict[str, list[str]] = {}
        for grant in grants:
            for column in grant.columns:
                columns.setdefault(column, []).append(grant.privilege)
        relations[relation] = columns
    return json.dumps(relations, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


_EXPECTED_COLUMN_GRANTS_JSON = _column_grants_json()

_RUNTIME_PRINCIPAL_IS_RESTRICTED = text(
    "WITH principal AS ("
    "SELECT role.oid, session_user = current_user AS same_identity, role.rolsuper, "
    "role.rolcreaterole, role.rolcreatedb, role.rolreplication, role.rolbypassrls, "
    "role.rolinherit, role.rolcanlogin FROM pg_roles AS role "
    "WHERE role.rolname = session_user"
    "), schema_owner AS ("
    "SELECT namespace.oid, namespace.nspowner FROM pg_namespace AS namespace "
    "WHERE namespace.nspname = :schema"
    "), expected_table_privileges AS ("
    "SELECT expected.relation_name, expected.privilege_type, expected.allowed "
    "FROM jsonb_to_recordset(CAST(:expected_table_privileges AS jsonb)) "
    "AS expected(relation_name text, privilege_type text, allowed boolean)"
    "), observed_table_privileges AS ("
    "SELECT relation.relname, expected.privilege_type, expected.allowed, "
    "has_table_privilege(principal.oid, relation.oid, expected.privilege_type) AS granted "
    "FROM pg_class AS relation "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "CROSS JOIN principal "
    "JOIN expected_table_privileges AS expected ON expected.relation_name = relation.relname "
    "WHERE namespace.nspname = :schema AND relation.relkind = ANY("
    'CAST(:relation_kinds AS "char"[]))'
    "), unexpected_table_privileges AS ("
    "SELECT has_table_privilege(principal.oid, relation.oid, "
    "privilege.privilege_type) AS granted "
    "FROM pg_class AS relation "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "CROSS JOIN principal "
    "CROSS JOIN (SELECT DISTINCT privilege_type FROM expected_table_privileges) AS privilege "
    "WHERE namespace.nspname = :schema AND relation.relkind = ANY("
    'CAST(:relation_kinds AS "char"[])) '
    "AND NOT EXISTS (SELECT 1 FROM expected_table_privileges AS expected "
    "WHERE expected.relation_name = relation.relname)"
    ") SELECT "
    "EXISTS (SELECT 1 FROM principal) AND EXISTS (SELECT 1 FROM schema_owner) "
    "AND (SELECT same_identity FROM principal) "
    "AND (SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb "
    "AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit FROM principal) "
    "AND (SELECT principal.oid <> schema_owner.nspowner FROM principal CROSS JOIN schema_owner) "
    "AND (SELECT has_schema_privilege(principal.oid, schema_owner.oid, 'USAGE') "
    "AND NOT has_schema_privilege(principal.oid, schema_owner.oid, 'CREATE') "
    "FROM principal CROSS JOIN schema_owner) "
    "AND NOT EXISTS (SELECT 1 FROM pg_auth_members AS membership "
    "CROSS JOIN principal WHERE membership.member = principal.oid "
    "OR membership.roleid = principal.oid) "
    "AND (SELECT count(*) FROM observed_table_privileges) "
    "= (SELECT count(*) FROM expected_table_privileges) "
    "AND NOT EXISTS (SELECT 1 FROM observed_table_privileges "
    "WHERE granted IS DISTINCT FROM allowed) "
    "AND NOT EXISTS (SELECT 1 FROM unexpected_table_privileges WHERE granted) "
    "AND NOT EXISTS (SELECT 1 FROM pg_class AS relation "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid "
    "CROSS JOIN principal "
    "CROSS JOIN unnest(CAST(:column_privileges AS text[])) AS privilege(name) "
    "WHERE namespace.nspname = :schema AND relation.relkind = ANY("
    'CAST(:relation_kinds AS "char"[])) '
    "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
    "AND (has_column_privilege(principal.oid, relation.oid, attribute.attnum, "
    "privilege.name) IS DISTINCT FROM "
    "(COALESCE((CAST(:expected_table_grants AS jsonb) -> relation.relname) "
    "? privilege.name, false) OR COALESCE((CAST(:expected_column_grants AS jsonb) "
    "-> relation.relname -> attribute.attname) ? privilege.name, false)) "
    "OR has_column_privilege(principal.oid, relation.oid, attribute.attnum, "
    "privilege.name || ' WITH GRANT OPTION'))) "
    "AND (SELECT has_table_privilege("
    "principal.oid, to_regclass(:migration_head_relation), 'SELECT') "
    "AND NOT has_table_privilege(principal.oid, to_regclass(:migration_head_relation), 'INSERT') "
    "AND NOT has_table_privilege(principal.oid, to_regclass(:migration_head_relation), 'UPDATE') "
    "AND NOT has_table_privilege(principal.oid, to_regclass(:migration_head_relation), 'DELETE') "
    "AND NOT has_table_privilege(principal.oid, to_regclass(:migration_head_relation), 'TRUNCATE') "
    "AND NOT has_table_privilege(principal.oid, "
    "to_regclass(:migration_head_relation), 'REFERENCES') "
    "AND NOT has_table_privilege(principal.oid, to_regclass(:migration_head_relation), 'TRIGGER') "
    "AND NOT has_table_privilege(principal.oid, to_regclass(:migration_head_relation), 'MAINTAIN') "
    "FROM principal) "
    "AND NOT EXISTS (SELECT 1 FROM principal "
    "CROSS JOIN unnest(CAST(:column_privileges AS text[])) AS privilege(name) "
    "WHERE has_any_column_privilege(principal.oid, "
    "to_regclass(:migration_head_relation), privilege.name) "
    "IS DISTINCT FROM (privilege.name = 'SELECT') "
    "OR has_any_column_privilege(principal.oid, "
    "to_regclass(:migration_head_relation), privilege.name || ' WITH GRANT OPTION')) "
    "AND NOT EXISTS (SELECT 1 FROM pg_class AS relation "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "CROSS JOIN principal WHERE namespace.nspname = :schema "
    "AND ((relation.relkind = 'S' AND ("
    "has_sequence_privilege(principal.oid, relation.oid, 'USAGE') "
    "IS DISTINCT FROM (relation.relname = :activity_sequence) "
    "OR has_sequence_privilege(principal.oid, relation.oid, 'SELECT') "
    "OR has_sequence_privilege(principal.oid, relation.oid, 'UPDATE')"
    ")))) "
    "AND (SELECT has_sequence_privilege(principal.oid, "
    "to_regclass(:activity_sequence_relation), 'USAGE') FROM principal) "
    "AND NOT EXISTS (SELECT 1 FROM pg_proc AS routine "
    "JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
    "CROSS JOIN principal WHERE namespace.nspname = :schema "
    "AND has_function_privilege(principal.oid, routine.oid, 'EXECUTE')) "
    "AND NOT EXISTS (SELECT 1 FROM pg_type AS type "
    "JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace "
    "CROSS JOIN principal WHERE namespace.nspname = :schema "
    "AND has_type_privilege(principal.oid, type.oid, 'USAGE')) "
    "AND NOT EXISTS (SELECT 1 FROM pg_namespace AS namespace "
    "CROSS JOIN principal CROSS JOIN LATERAL aclexplode("
    "COALESCE(namespace.nspacl, acldefault('n', namespace.nspowner))) AS privilege "
    "WHERE namespace.nspname = :schema AND privilege.grantee = principal.oid "
    "AND privilege.is_grantable) "
    "AND NOT EXISTS (SELECT 1 FROM pg_class AS relation "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "CROSS JOIN principal CROSS JOIN LATERAL aclexplode(COALESCE("
    "relation.relacl, acldefault("
    "(CASE WHEN relation.relkind = 'S' THEN 's' ELSE 'r' END)::\"char\", "
    "relation.relowner))) AS privilege WHERE namespace.nspname = :schema "
    "AND (relation.relkind = 'S' OR relation.relkind = ANY("
    'CAST(:relation_kinds AS "char"[]))) AND privilege.grantee = principal.oid '
    "AND privilege.is_grantable) "
    "AND NOT EXISTS (SELECT 1 FROM pg_class AS relation CROSS JOIN principal "
    "CROSS JOIN LATERAL aclexplode(COALESCE("
    "relation.relacl, acldefault('r', relation.relowner))) AS privilege "
    "WHERE relation.oid = to_regclass(:migration_head_relation) "
    "AND privilege.grantee = principal.oid AND privilege.is_grantable) "
    "AND NOT EXISTS (SELECT 1 FROM pg_proc AS routine "
    "JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
    "CROSS JOIN principal CROSS JOIN LATERAL aclexplode("
    "COALESCE(routine.proacl, acldefault('f', routine.proowner))) AS privilege "
    "WHERE namespace.nspname = :schema AND privilege.grantee = principal.oid "
    "AND privilege.is_grantable) "
    "AND NOT EXISTS (SELECT 1 FROM pg_type AS type "
    "JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace "
    "CROSS JOIN principal CROSS JOIN LATERAL aclexplode("
    "COALESCE(type.typacl, acldefault('T', type.typowner))) AS privilege "
    "WHERE namespace.nspname = :schema AND type.typelem = 0 "
    "AND privilege.grantee = principal.oid AND privilege.is_grantable) "
    "AND NOT EXISTS (SELECT 1 FROM pg_default_acl AS default_acl "
    "LEFT JOIN pg_namespace AS namespace ON namespace.oid = default_acl.defaclnamespace "
    "CROSS JOIN principal CROSS JOIN LATERAL aclexplode(default_acl.defaclacl) AS privilege "
    "WHERE (default_acl.defaclnamespace = 0 OR namespace.nspname = :schema) "
    "AND privilege.grantee = principal.oid)"
)


async def runtime_principal_is_restricted(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
) -> bool:
    """Return whether the active session has exactly the runtime authority."""
    result = await connection.scalar(
        _RUNTIME_PRINCIPAL_IS_RESTRICTED,
        {
            "activity_sequence": RUNTIME_ACTIVITY_SEQUENCE,
            "activity_sequence_relation": (
                f"{profile.application_schema}.{RUNTIME_ACTIVITY_SEQUENCE}"
            ),
            "column_privileges": list(RUNTIME_COLUMN_PRIVILEGES),
            "expected_column_grants": _EXPECTED_COLUMN_GRANTS_JSON,
            "expected_table_grants": _EXPECTED_TABLE_GRANTS_JSON,
            "expected_table_privileges": _EXPECTED_TABLE_PRIVILEGES_JSON,
            "schema": profile.application_schema,
            "migration_head_relation": profile.migration_head_relation,
            "relation_kinds": list(RUNTIME_RELATION_KINDS),
        },
    )
    return result is True
