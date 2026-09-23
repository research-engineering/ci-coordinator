from datetime import datetime

from sqlalchemy import and_, func, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.schema import ci_observation_subscriptions


def observation_scope_predicate(scope: RepositoryScope) -> ColumnElement[bool]:
    if type(scope) is not RepositoryScope:
        raise TypeError("observation storage requires exact repository scope")
    table = ci_observation_subscriptions
    return and_(
        table.c.installation_id == scope.installation_id,
        table.c.repository_id == scope.repository_id,
    )


async def lock_observation_scope(
    connection: AsyncConnection, scope: RepositoryScope, *, try_only: bool = False
) -> bool:
    if type(scope) is not RepositoryScope or type(try_only) is not bool:
        raise TypeError("observation lock requires exact scope and mode")
    statement = (
        "SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"
        if try_only
        else "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
    )
    result = await connection.scalar(
        text(statement),
        {"key": f"ci-observation-scope/v1:{scope.installation_id}:{scope.repository_id}"},
    )
    return result is True if try_only else True


async def lock_observation_quota(connection: AsyncConnection) -> None:
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('ci-observation-config-quota/v1', 0))")
    )


async def load_locked_subscription(
    connection: AsyncConnection,
    scope: RepositoryScope,
    *,
    skip_locked: bool = False,
) -> RowMapping | None:
    if type(skip_locked) is not bool:
        raise TypeError("observation row lock mode must be an exact boolean")
    return (
        (
            await connection.execute(
                select(ci_observation_subscriptions)
                .where(observation_scope_predicate(scope))
                .with_for_update(skip_locked=skip_locked)
            )
        )
        .mappings()
        .one_or_none()
    )


async def observation_database_time(connection: AsyncConnection) -> datetime:
    value = await connection.scalar(select(func.clock_timestamp()))
    if type(value) is not datetime:
        raise ValueError("observation database time unavailable")
    return utc_time(value)
