"""Catalog projections shared only by the three production-cutover capabilities."""

from sqlalchemy.engine import Connection

from ci_coordinator.persistence.catalog_observation import CatalogRows as CatalogRows
from ci_coordinator.persistence.catalog_observation import catalog_rows as catalog_rows
from ci_coordinator.persistence.catalog_observation import column_rows as column_rows
from ci_coordinator.persistence.catalog_observation import relation_rows as relation_rows
from ci_coordinator.persistence.catalog_observation import trigger_rows as trigger_rows


def constraint_rows(connection: Connection, relations: tuple[str, ...]) -> CatalogRows:
    return catalog_rows(
        connection,
        """
        SELECT c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid, true),
               k.condeferrable, k.condeferred, k.convalidated, k.conenforced
        FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema AND c.relname = ANY(:relations) AND k.contype <> 'n'
        ORDER BY c.relname, k.conname
    """,
        relations,
    )


def index_rows(connection: Connection, relations: tuple[str, ...]) -> CatalogRows:
    return catalog_rows(
        connection,
        """
        SELECT c.relname, ic.relname, i.indisunique, i.indisprimary,
               i.indisvalid, i.indisready, i.indislive, pg_get_indexdef(ic.oid, 0, false)
        FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_constraint k ON k.conindid = i.indexrelid
        WHERE n.nspname = :schema AND c.relname = ANY(:relations) AND k.oid IS NULL
        ORDER BY c.relname, ic.relname
    """,
        relations,
    )


def routine_rows(connection: Connection, names: tuple[str, ...]) -> CatalogRows:
    return catalog_rows(
        connection,
        """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid),
               pg_get_function_result(p.oid), l.lanname, p.prosecdef,
               p.provolatile, p.proisstrict, p.proleakproof,
               p.proowner = n.nspowner, p.proconfig::text, btrim(p.prosrc)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_language l ON l.oid = p.prolang
        WHERE n.nspname = :schema AND p.proname = ANY(:relations)
        ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)
    """,
        names,
    )


def unexpected_relation_objects(connection: Connection, relations: tuple[str, ...]) -> CatalogRows:
    return catalog_rows(
        connection,
        """
        WITH owned AS (
            SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema AND c.relname = ANY(:relations)
        )
        SELECT
            (SELECT count(*) FROM pg_policy WHERE polrelid IN (SELECT oid FROM owned)),
            (SELECT count(*) FROM pg_rewrite WHERE ev_class IN (SELECT oid FROM owned)),
            (SELECT count(*) FROM pg_inherits WHERE inhrelid IN (SELECT oid FROM owned)
                OR inhparent IN (SELECT oid FROM owned)),
            (SELECT count(*) FROM pg_attribute WHERE attrelid IN (SELECT oid FROM owned)
                AND attnum > 0 AND NOT attisdropped
                AND (attidentity <> '' OR attgenerated <> '')),
            (SELECT count(*) FROM pg_index WHERE indrelid IN (SELECT oid FROM owned)
                AND NOT (indisvalid AND indisready AND indislive))
    """,
        relations,
    )
