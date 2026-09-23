"""Exact catalog attestation for durable ingress, admission, and issuance state."""

from __future__ import annotations

from hashlib import sha256
from typing import Final, cast

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

type CatalogProjection = tuple[str, str, int, str]

_RELATIONS: Final = (
    "issued_plan_envelopes",
    "production_admission_authorities",
    "production_admission_scope_bindings",
    "webhook_deliveries",
)
_RUNTIME_INGRESS_ISSUANCE_PROJECTIONS: Final[tuple[CatalogProjection, ...]] = (
    (
        "columns",
        """
        SELECT relation.relname, attribute.attname,
               format_type(attribute.atttypid, attribute.atttypmod),
               NOT attribute.attnotnull
        FROM pg_attribute AS attribute
        JOIN pg_class AS relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = :schema
          AND relation.relname = ANY(:relations)
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        ORDER BY relation.relname, attribute.attnum
        """,
        22,
        "bfda2fe812a30b8455c29ead20de22721bd64c734ed75fc2818a5dc92c373ca3",
    ),
    (
        "constraints",
        """
        SELECT relation.relname, conname, contype,
               pg_get_constraintdef(pg_constraint.oid, true),
               condeferrable, condeferred, convalidated
        FROM pg_constraint
        JOIN pg_class AS relation ON relation.oid = conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = :schema
          AND relation.relname = ANY(:relations)
          AND contype <> 'n'
          AND NOT (
              relation.relname = 'webhook_deliveries'
              AND conname = 'uq_webhook_deliveries_body_sha256'
          )
        ORDER BY 1, 2
        """,
        23,
        "9baf7c9575fa78a794a60523feb57d16920f4dacdfee4ffc19b5da45c8dd3d3a",
    ),
    (
        "indexes",
        """
        SELECT table_metadata.relname, index_relation.relname,
               index_metadata.indisunique, index_metadata.indisprimary,
               index_metadata.indisvalid, index_metadata.indisready,
               index_metadata.indislive,
               pg_get_indexdef(index_relation.oid, 0, false)
        FROM pg_index AS index_metadata
        JOIN pg_class AS index_relation
          ON index_relation.oid = index_metadata.indexrelid
        JOIN pg_class AS table_metadata
          ON table_metadata.oid = index_metadata.indrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = index_relation.relnamespace
        LEFT JOIN pg_constraint AS constraint_metadata
          ON constraint_metadata.conindid = index_metadata.indexrelid
        WHERE namespace.nspname = :schema
          AND table_metadata.relname = ANY(:relations)
          AND constraint_metadata.oid IS NULL
        ORDER BY table_metadata.relname, index_relation.relname
        """,
        1,
        "d4ef882c750c5972ae315b13beda4fad1a0fe5d47830cae5e78d190db8af16ab",
    ),
    (
        "tables",
        """
        SELECT relation.relname, relation.relkind, relation.relpersistence,
               relation.relrowsecurity, relation.relforcerowsecurity
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = :schema
          AND relation.relname = ANY(:relations)
        ORDER BY relation.relname
        """,
        4,
        "e4293fbae8bd206579df613412ff7b832b8e344d01a8061bc83465fe2bb05d1a",
    ),
)
_WEBHOOK_BODY_IDENTITY_PROJECTIONS: Final[tuple[CatalogProjection, ...]] = (
    (
        "webhook-body-identity",
        """
        SELECT relation.relname, conname, contype,
               pg_get_constraintdef(pg_constraint.oid, true),
               condeferrable, condeferred, convalidated
        FROM pg_constraint
        JOIN pg_class AS relation ON relation.oid = conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = :schema
          AND relation.relname = 'webhook_deliveries'
          AND conname = 'uq_webhook_deliveries_body_sha256'
        ORDER BY 1, 2
        """,
        1,
        "4ead7889fda3b448e4fce8f577975c0fd2be61b4690b5dbe08ed6203487955a0",
    ),
)
_UNEXPECTED_OBJECTS = """
WITH admitted_relations AS (
    SELECT relation.oid
    FROM pg_class AS relation
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = :schema
      AND relation.relname = ANY(:relations)
)
SELECT (
    SELECT count(*)
    FROM pg_constraint
    WHERE conrelid IN (SELECT oid FROM admitted_relations)
      AND contype <> 'n'
      AND NOT conenforced
) + (
    SELECT count(*)
    FROM pg_trigger
    WHERE tgrelid IN (SELECT oid FROM admitted_relations)
      AND NOT tgisinternal
) + (
    SELECT count(*)
    FROM pg_policy
    WHERE polrelid IN (SELECT oid FROM admitted_relations)
)
"""


async def runtime_ingress_issuance_state_schema_matches_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(runtime_ingress_issuance_state_schema_matches_contract_sync)


def runtime_ingress_issuance_state_schema_matches_contract_sync(connection: Connection) -> bool:
    return _runtime_ingress_predecessor_projection_matches(connection, successor=False)


def _runtime_ingress_predecessor_projection_matches(
    connection: Connection, *, successor: bool
) -> bool:
    parameters = {"schema": APPLICATION_SCHEMA, "relations": list(_RELATIONS)}
    for _label, statement, expected_count, expected_digest in _RUNTIME_INGRESS_ISSUANCE_PROJECTIONS:
        facts = [list(row) for row in connection.execute(text(statement), parameters)]
        if successor:
            addition = {
                "columns": "expires_at",
                "constraints": "ck_issued_plan_envelopes_exact_expiry",
                "indexes": "ix_issued_plan_envelopes_selected_expiry",
            }.get(_label)
            facts = [
                row for row in facts if (row[0], row[1]) != ("issued_plan_envelopes", addition)
            ]
        if (
            len(facts) != expected_count
            or sha256(canonical_json(facts)).hexdigest() != expected_digest
        ):
            return False
    unexpected_count = cast(int, connection.scalar(text(_UNEXPECTED_OBJECTS), parameters))
    return unexpected_count == 0


async def webhook_body_identity_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(webhook_body_identity_schema_matches_contract_sync)


def webhook_body_identity_schema_matches_contract_sync(connection: Connection) -> bool:
    parameters = {"schema": APPLICATION_SCHEMA}
    for _label, statement, expected_count, expected_digest in _WEBHOOK_BODY_IDENTITY_PROJECTIONS:
        facts = [list(row) for row in connection.execute(text(statement), parameters)]
        if (
            len(facts) != expected_count
            or sha256(canonical_json(facts)).hexdigest() != expected_digest
        ):
            return False
    return True
