from typing import Final

OWNED_RELATION_SQL: Final = (
    "SELECT relation.relkind, relation.relpersistence, relation.relrowsecurity, "
    "relation.relforcerowsecurity, relation.relowner = namespace.nspowner, "
    "row_type.typowner = namespace.nspowner FROM pg_class AS relation "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "JOIN pg_type AS row_type ON row_type.oid = relation.reltype "
    "WHERE namespace.nspname = :schema AND relation.relname = :relation"
)

RELATION_COLUMNS_SQL: Final = (
    "SELECT attribute.attname, "
    "format_type(attribute.atttypid, attribute.atttypmod), "
    "NOT attribute.attnotnull, "
    "pg_get_expr(default_value.adbin, default_value.adrelid) "
    "FROM pg_attribute AS attribute "
    "JOIN pg_class AS relation ON relation.oid = attribute.attrelid "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "LEFT JOIN pg_attrdef AS default_value ON default_value.adrelid = relation.oid "
    "AND default_value.adnum = attribute.attnum "
    "WHERE namespace.nspname = :schema AND relation.relname = :relation "
    "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
    "ORDER BY attribute.attnum"
)

RELATION_CONSTRAINTS_SQL: Final = (
    "SELECT conname, contype, pg_get_constraintdef(pg_constraint.oid, true), "
    "condeferrable, condeferred, convalidated FROM pg_constraint "
    "JOIN pg_class AS relation ON relation.oid = conrelid "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "WHERE namespace.nspname = :schema AND relation.relname = :relation "
    "AND contype <> 'n'"
)

RELATION_TRIGGERS_SQL: Final = (
    "SELECT trigger_metadata.tgname, function_metadata.proname, "
    "function_metadata.prosecdef, trigger_metadata.tgenabled, "
    "trigger_metadata.tgtype, function_metadata.proowner = namespace.nspowner "
    "FROM pg_trigger AS trigger_metadata "
    "JOIN pg_class AS relation ON relation.oid = trigger_metadata.tgrelid "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "JOIN pg_proc AS function_metadata "
    "ON function_metadata.oid = trigger_metadata.tgfoid "
    "WHERE namespace.nspname = :schema AND relation.relname = :relation "
    "AND NOT trigger_metadata.tgisinternal"
)
