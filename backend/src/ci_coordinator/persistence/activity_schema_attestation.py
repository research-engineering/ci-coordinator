from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.activity_schema_contract import ACTIVITY_CATALOG
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)


async def activity_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(activity_schema_matches_contract_sync)


def activity_schema_matches_contract_sync(connection: Connection) -> bool:
    if not ci_economics_schema_matches_contract_sync(connection, contract=ACTIVITY_CATALOG):
        return False
    identities = connection.execute(
        text(
            "SELECT t.relname, a.attname, a.attidentity, a.attgenerated "
            "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class t ON t.oid = a.attrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'ci_coordinator' "
            "AND t.relname IN ('activity_events', 'activity_diagnostic_buckets') "
            "AND (a.attidentity <> '' OR a.attgenerated <> '') ORDER BY 1, 2"
        )
    ).all()
    sequences = connection.execute(
        text(
            "SELECT s.relname, s.relkind, s.relpersistence, s.relowner = n.nspowner, "
            "q.seqtypid::regtype::text, q.seqstart, q.seqincrement, q.seqmin, q.seqmax, "
            "q.seqcache, q.seqcycle, d.deptype, a.attname "
            "FROM pg_catalog.pg_class s JOIN pg_catalog.pg_namespace n ON n.oid = s.relnamespace "
            "JOIN pg_catalog.pg_sequence q ON q.seqrelid = s.oid "
            "JOIN pg_catalog.pg_depend d ON d.objid = s.oid "
            "AND d.classid = 'pg_catalog.pg_class'::regclass "
            "AND d.refclassid = 'pg_catalog.pg_class'::regclass "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = d.refobjid "
            "AND a.attnum = d.refobjsubid "
            "WHERE n.nspname = 'ci_coordinator' "
            "AND d.refobjid IN ('ci_coordinator.activity_events'::regclass, "
            "'ci_coordinator.activity_diagnostic_buckets'::regclass) ORDER BY 1"
        )
    ).all()
    return [tuple(row) for row in identities] == [("activity_events", "sequence", "d", "")] and [
        tuple(row) for row in sequences
    ] == [
        (
            "activity_events_sequence_seq",
            "S",
            "p",
            True,
            "bigint",
            1,
            1,
            1,
            9223372036854775807,
            1,
            False,
            "i",
            "sequence",
        )
    ]
