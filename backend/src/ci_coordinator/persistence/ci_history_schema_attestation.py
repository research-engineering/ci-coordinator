from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence._schema_ci_history_control import ci_history_defaults
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_history_retention_codec import decode_history_defaults
from ci_coordinator.persistence.ci_history_schema_contract import HISTORY_CATALOG


async def ci_history_schema_matches_contract(connection: AsyncConnection) -> bool:
    return await connection.run_sync(ci_history_schema_matches_contract_sync)


def ci_history_schema_matches_contract_sync(connection: Connection) -> bool:
    return ci_economics_schema_matches_contract_sync(
        connection, contract=HISTORY_CATALOG
    ) and history_default_is_valid_sync(connection)


def history_default_is_valid_sync(connection: Connection) -> bool:
    rows = connection.execute(select(ci_history_defaults).limit(2)).mappings().all()
    if len(rows) != 1:
        return False
    try:
        decode_history_defaults(rows[0])
    except (TypeError, ValueError):
        return False
    return True
