from sqlalchemy import String, column, func, select, text, values
from sqlalchemy.ext.asyncio import AsyncConnection

MAX_OBSERVATION_LOCK_BATCH = 1_000


async def lock_workflow_observation(connection: AsyncConnection, delivery_id: str) -> None:
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _lock_key(delivery_id)},
    )


async def try_lock_workflow_observations(
    connection: AsyncConnection, delivery_ids: tuple[str, ...]
) -> tuple[str, ...]:
    if type(delivery_ids) is not tuple or len(delivery_ids) > MAX_OBSERVATION_LOCK_BATCH:
        raise ValueError("observation lock batch is outside its admitted bound")
    rows = [(delivery_id, _lock_key(delivery_id)) for delivery_id in delivery_ids]
    if len(set(delivery_ids)) != len(delivery_ids):
        raise ValueError("observation lock batch requires distinct delivery identities")
    if not rows:
        return ()
    candidates = values(
        column("delivery_id", String(128)),
        column("lock_key", String()),
        name="observation_lock_candidates",
    ).data(sorted(rows))
    result = await connection.execute(
        select(
            candidates.c.delivery_id,
            func.pg_try_advisory_xact_lock(func.hashtextextended(candidates.c.lock_key, 0)),
        ).order_by(candidates.c.delivery_id)
    )
    return tuple(delivery_id for delivery_id, acquired in result if acquired is True)


def _lock_key(delivery_id: str) -> str:
    if type(delivery_id) is not str or not 1 <= len(delivery_id) <= 128 or "\x00" in delivery_id:
        raise ValueError("observation lock requires a PostgreSQL delivery identity")
    delivery_id.encode("utf-8", errors="strict")
    return f"ci-workflow-observation/v1:{delivery_id}"
