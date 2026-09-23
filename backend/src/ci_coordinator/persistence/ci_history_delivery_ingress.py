from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence._schema_ci_history_delivery import (
    ci_history_delivery_inbox as inbox,
)
from ci_coordinator.persistence.ci_history_delivery_codec import (
    decode_history_delivery,
    history_delivery_fields,
)
from ci_coordinator.persistence.errors import PersistenceError


async def ensure_history_delivery_inbox(
    connection: AsyncConnection, source_row: Mapping[str, object] | RowMapping
) -> bool:
    if source_row.get("observation_kind") == "workflow_job":
        return True
    try:
        source = decode_history_delivery(source_row)
        if source.hint is None:
            return True
        expected = history_delivery_fields(source)
    except (TypeError, ValueError) as error:
        raise PersistenceError("history delivery source cannot be admitted") from error
    inserted = await connection.scalar(
        insert(inbox)
        .values(**expected, delivered_generation=0, delivered_at=None)
        .on_conflict_do_nothing(index_elements=[inbox.c.delivery_id])
        .returning(inbox.c.delivery_id)
    )
    if inserted is not None:
        return type(inserted) is str and inserted == source.delivery_id
    prior = (
        (await connection.execute(select(inbox).where(inbox.c.delivery_id == source.delivery_id)))
        .mappings()
        .one_or_none()
    )
    return prior is not None and all(prior[name] == value for name, value in expected.items())
