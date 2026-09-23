"""Catalog attestation for durable control-plane identity state."""

from __future__ import annotations

from typing import Final

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA
from ci_coordinator.persistence.catalog_observation import (
    column_rows,
    relation_rows,
)

_RELATIONS: Final = ("control_plane_logout_replays", "control_plane_sessions")
_EXPECTED_COLUMNS: Final = (
    ("control_plane_logout_replays", "issuer", "character varying(512)", False, None),
    ("control_plane_logout_replays", "jti", "character varying(512)", False, None),
    (
        "control_plane_logout_replays",
        "retain_until",
        "timestamp with time zone",
        False,
        None,
    ),
    ("control_plane_sessions", "handle_digest", "bytea", False, None),
    ("control_plane_sessions", "issuer", "character varying(512)", False, None),
    ("control_plane_sessions", "subject", "character varying(512)", False, None),
    ("control_plane_sessions", "keycloak_sid", "character varying(512)", False, None),
    ("control_plane_sessions", "actor_id", "character varying(82)", False, None),
    ("control_plane_sessions", "roles", "smallint", False, None),
    (
        "control_plane_sessions",
        "preferred_username",
        "character varying(256)",
        True,
        None,
    ),
    ("control_plane_sessions", "display_name", "character varying(512)", True, None),
    ("control_plane_sessions", "profile_digest", "character varying(64)", False, None),
    ("control_plane_sessions", "issued_at", "timestamp with time zone", False, None),
    ("control_plane_sessions", "expires_at", "timestamp with time zone", False, None),
)
_EXPECTED_COLUMN_STORAGE: Final = (
    ("control_plane_logout_replays", "issuer", "x", "", "", ""),
    ("control_plane_logout_replays", "jti", "x", "", "", ""),
    ("control_plane_logout_replays", "retain_until", "p", "", "", ""),
    ("control_plane_sessions", "handle_digest", "x", "", "", ""),
    ("control_plane_sessions", "issuer", "x", "", "", ""),
    ("control_plane_sessions", "subject", "x", "", "", ""),
    ("control_plane_sessions", "keycloak_sid", "x", "", "", ""),
    ("control_plane_sessions", "actor_id", "x", "", "", ""),
    ("control_plane_sessions", "roles", "p", "", "", ""),
    ("control_plane_sessions", "preferred_username", "x", "", "", ""),
    ("control_plane_sessions", "display_name", "x", "", "", ""),
    ("control_plane_sessions", "profile_digest", "x", "", "", ""),
    ("control_plane_sessions", "issued_at", "p", "", "", ""),
    ("control_plane_sessions", "expires_at", "p", "", "", ""),
)
_EXPECTED_CONSTRAINT_DEFINITIONS: Final = {
    (
        "control_plane_logout_replays",
        "ck_control_plane_logout_replays_issuer",
    ): ("CHECK (octet_length(issuer::text) >= 1 AND octet_length(issuer::text) <= 512)"),
    (
        "control_plane_logout_replays",
        "ck_control_plane_logout_replays_jti",
    ): "CHECK (octet_length(jti::text) >= 1 AND octet_length(jti::text) <= 512)",
    (
        "control_plane_logout_replays",
        "ck_control_plane_logout_replays_retain_until",
    ): "CHECK (isfinite(retain_until))",
    (
        "control_plane_logout_replays",
        "pk_control_plane_logout_replays",
    ): "PRIMARY KEY (issuer, jti)",
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_actor_id",
    ): "CHECK (actor_id::text ~ '^keycloak-human:v1:[0-9a-f]{64}$'::text)",
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_display_name",
    ): (
        "CHECK (display_name IS NULL OR octet_length(display_name::text) >= 1 AND "
        "octet_length(display_name::text) <= 512)"
    ),
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_handle_digest",
    ): "CHECK (octet_length(handle_digest) = 32)",
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_issuer",
    ): ("CHECK (octet_length(issuer::text) >= 1 AND octet_length(issuer::text) <= 512)"),
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_keycloak_sid",
    ): (
        "CHECK (octet_length(keycloak_sid::text) >= 1 AND octet_length(keycloak_sid::text) <= 512)"
    ),
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_preferred_username",
    ): (
        "CHECK (preferred_username IS NULL OR "
        "octet_length(preferred_username::text) >= 1 AND "
        "octet_length(preferred_username::text) <= 256)"
    ),
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_profile_digest",
    ): "CHECK (profile_digest::text ~ '^[0-9a-f]{64}$'::text)",
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_roles",
    ): "CHECK (roles >= 0 AND roles <= 31)",
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_subject",
    ): ("CHECK (octet_length(subject::text) >= 1 AND octet_length(subject::text) <= 512)"),
    (
        "control_plane_sessions",
        "ck_control_plane_sessions_time_order",
    ): (
        "CHECK (isfinite(issued_at) AND isfinite(expires_at) AND expires_at > issued_at "
        "AND expires_at <= (issued_at + '00:15:00'::interval))"
    ),
    (
        "control_plane_sessions",
        "pk_control_plane_sessions",
    ): "PRIMARY KEY (handle_digest)",
}
_EXPECTED_INDEXES: Final = frozenset(
    {
        (
            "control_plane_logout_replays",
            "ix_control_plane_logout_replays_retain_until",
            False,
            False,
            True,
            True,
            True,
            "CREATE INDEX ix_control_plane_logout_replays_retain_until ON "
            "ci_coordinator.control_plane_logout_replays USING btree "
            "(retain_until, issuer, jti)",
        ),
        (
            "control_plane_sessions",
            "ix_control_plane_sessions_expires_at",
            False,
            False,
            True,
            True,
            True,
            "CREATE INDEX ix_control_plane_sessions_expires_at ON "
            "ci_coordinator.control_plane_sessions USING btree "
            "(expires_at, handle_digest)",
        ),
        (
            "control_plane_sessions",
            "ix_control_plane_sessions_identity_issued",
            False,
            False,
            True,
            True,
            True,
            "CREATE INDEX ix_control_plane_sessions_identity_issued ON "
            "ci_coordinator.control_plane_sessions USING btree "
            "(issuer, subject, issued_at DESC, handle_digest DESC)",
        ),
        (
            "control_plane_sessions",
            "ix_control_plane_sessions_issuer_sid",
            False,
            False,
            True,
            True,
            True,
            "CREATE INDEX ix_control_plane_sessions_issuer_sid ON "
            "ci_coordinator.control_plane_sessions USING btree (issuer, keycloak_sid)",
        ),
    }
)
_EXPECTED_ROUTINE: Final = (
    "reject_control_plane_session_mutation",
    "",
    "trigger",
    "plpgsql",
    False,
    "v",
    False,
    False,
    (
        " BEGIN RAISE EXCEPTION 'control-plane session identity is immutable' "
        "USING ERRCODE = '42501'; END; "
    ),
    True,
)


async def control_plane_identity_schema_matches_contract(
    connection: AsyncConnection,
) -> bool:
    return await connection.run_sync(control_plane_identity_schema_matches_contract_sync)


def control_plane_identity_schema_matches_contract_sync(connection: Connection) -> bool:
    columns = column_rows(connection, _RELATIONS)
    column_storage = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT relation.relname, attribute.attname, attribute.attstorage::text, "
                "attribute.attcompression::text, attribute.attidentity::text, "
                "attribute.attgenerated::text FROM pg_attribute AS attribute "
                "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname IN :relations "
                "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
                "ORDER BY relation.relname, attribute.attnum"
            ).bindparams(bindparam("relations", expanding=True)),
            {"schema": APPLICATION_SCHEMA, "relations": _RELATIONS},
        )
    )
    constraints = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT relation.relname, constraint_metadata.conname, "
                "constraint_metadata.contype::text, "
                "pg_get_constraintdef(constraint_metadata.oid, true), "
                "constraint_metadata.condeferrable, constraint_metadata.condeferred, "
                "constraint_metadata.convalidated, constraint_metadata.connoinherit "
                "FROM pg_constraint AS constraint_metadata "
                "JOIN pg_class AS relation ON relation.oid = constraint_metadata.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relname IN :relations "
                "AND constraint_metadata.contype <> 'n'"
            ).bindparams(bindparam("relations", expanding=True)),
            {"schema": APPLICATION_SCHEMA, "relations": _RELATIONS},
        )
    )
    indexes = frozenset(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT relation.relname, index_relation.relname, "
                "index_metadata.indisunique, index_metadata.indisprimary, "
                "index_metadata.indisvalid, index_metadata.indisready, "
                "index_metadata.indislive, pg_get_indexdef(index_metadata.indexrelid) "
                "FROM pg_index AS index_metadata "
                "JOIN pg_class AS relation ON relation.oid = index_metadata.indrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "JOIN pg_class AS index_relation ON index_relation.oid = index_metadata.indexrelid "
                "LEFT JOIN pg_constraint AS constraint_metadata "
                "ON constraint_metadata.conindid = index_metadata.indexrelid "
                "WHERE namespace.nspname = :schema AND relation.relname IN :relations "
                "AND constraint_metadata.oid IS NULL"
            ).bindparams(bindparam("relations", expanding=True)),
            {"schema": APPLICATION_SCHEMA, "relations": _RELATIONS},
        )
    )
    tables = relation_rows(connection, _RELATIONS)
    triggers = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT relation.relname, trigger_metadata.tgname, function_metadata.proname, "
                "function_metadata.prosecdef, trigger_metadata.tgenabled, "
                "trigger_metadata.tgtype, function_metadata.proowner = namespace.nspowner "
                "FROM pg_trigger AS trigger_metadata "
                "JOIN pg_class AS relation ON relation.oid = trigger_metadata.tgrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "JOIN pg_proc AS function_metadata "
                "ON function_metadata.oid = trigger_metadata.tgfoid "
                "WHERE namespace.nspname = :schema AND relation.relname IN :relations "
                "AND NOT trigger_metadata.tgisinternal ORDER BY relation.relname, "
                "trigger_metadata.tgname"
            ).bindparams(bindparam("relations", expanding=True)),
            {"schema": APPLICATION_SCHEMA, "relations": _RELATIONS},
        )
    )
    routine = connection.execute(
        text(
            "SELECT routine.proname, pg_get_function_identity_arguments(routine.oid), "
            "pg_get_function_result(routine.oid), language.lanname, routine.prosecdef, "
            "routine.provolatile, routine.proisstrict, routine.proleakproof, routine.prosrc, "
            "routine.proowner = namespace.nspowner FROM pg_proc AS routine "
            "JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace "
            "JOIN pg_language AS language ON language.oid = routine.prolang "
            "WHERE namespace.nspname = :schema "
            "AND routine.proname = 'reject_control_plane_session_mutation'"
        ),
        {"schema": APPLICATION_SCHEMA},
    ).one_or_none()
    unexpected_policies = connection.execute(
        text(
            "SELECT count(*) FROM pg_policy AS policy_metadata "
            "JOIN pg_class AS relation ON relation.oid = policy_metadata.polrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = :schema AND relation.relname IN :relations"
        ).bindparams(bindparam("relations", expanding=True)),
        {"schema": APPLICATION_SCHEMA, "relations": _RELATIONS},
    ).scalar_one()
    return (
        columns == _EXPECTED_COLUMNS
        and column_storage == _EXPECTED_COLUMN_STORAGE
        and {(row[0], row[1]) for row in constraints} == set(_EXPECTED_CONSTRAINT_DEFINITIONS)
        and all(
            row[2] == ("p" if row[1].startswith("pk_") else "c")
            and _EXPECTED_CONSTRAINT_DEFINITIONS[(row[0], row[1])] == row[3]
            and row[4] is False
            and row[5] is False
            and row[6] is True
            and row[7] is (row[2] in {"f", "p", "u"})
            for row in constraints
        )
        and indexes == _EXPECTED_INDEXES
        and tables
        == (
            ("control_plane_logout_replays", "r", "p", False, False, True, True),
            ("control_plane_sessions", "r", "p", False, False, True, True),
        )
        and triggers
        == (
            (
                "control_plane_sessions",
                "tr_control_plane_sessions_immutable",
                "reject_control_plane_session_mutation",
                False,
                "O",
                19,
                True,
            ),
        )
        and routine == _EXPECTED_ROUTINE
        and unexpected_policies == 0
    )
