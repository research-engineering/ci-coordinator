from sqlalchemy import text
from sqlalchemy.engine import Connection

from ci_coordinator.persistence.schema import APPLICATION_SCHEMA

type CatalogRows = tuple[tuple[object, ...], ...]


def catalog_rows(connection: Connection, statement: str, relations: tuple[str, ...]) -> CatalogRows:
    return tuple(
        tuple(row)
        for row in connection.execute(
            text(statement), {"schema": APPLICATION_SCHEMA, "relations": list(relations)}
        )
    )


def column_rows(connection: Connection, relations: tuple[str, ...]) -> CatalogRows:
    return catalog_rows(
        connection,
        """
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
               NOT a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE n.nspname = :schema AND c.relname = ANY(:relations)
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
    """,
        relations,
    )


def relation_rows(connection: Connection, relations: tuple[str, ...]) -> CatalogRows:
    return catalog_rows(
        connection,
        """
        SELECT c.relname, c.relkind, c.relpersistence, c.relrowsecurity,
               c.relforcerowsecurity, c.relowner = n.nspowner, t.typowner = n.nspowner
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type t ON t.oid = c.reltype
        WHERE n.nspname = :schema AND c.relname = ANY(:relations) ORDER BY c.relname
    """,
        relations,
    )


def trigger_rows(connection: Connection, relations: tuple[str, ...]) -> CatalogRows:
    return catalog_rows(
        connection,
        """
        SELECT c.relname, t.tgname, p.proname, pn.nspname, p.prosecdef,
               t.tgenabled, t.tgtype, p.proowner = n.nspowner, t.tgqual IS NULL,
               t.tgnargs, octet_length(t.tgargs), t.tgattr::text, t.tgparentid = 0,
               t.tgconstraint = 0, t.tgdeferrable, t.tginitdeferred
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        JOIN pg_namespace pn ON pn.oid = p.pronamespace
        WHERE n.nspname = :schema AND c.relname = ANY(:relations)
          AND NOT t.tgisinternal ORDER BY c.relname, t.tgname
    """,
        relations,
    )
