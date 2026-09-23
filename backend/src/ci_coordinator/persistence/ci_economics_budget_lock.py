from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import canonical_json


async def lock_budget_scope(
    connection: AsyncConnection, scope: RepositoryScope, *, shared: bool = False
) -> None:
    if type(scope) is not RepositoryScope or type(shared) is not bool:
        raise TypeError("budget locking requires exact scope and lock mode")
    digest = sha256(
        b"ci-coordinator-budget-lock/v1\0"
        + canonical_json(
            {
                "installationId": scope.installation_id,
                "repositoryId": scope.repository_id,
            }
        )
    ).digest()
    # The bigint namespace is disjoint from repository-authority two-int locks.
    key = int.from_bytes(digest[:8], "big", signed=True)
    statement = (
        "SELECT pg_advisory_xact_lock_shared(:key)"
        if shared
        else "SELECT pg_advisory_xact_lock(:key)"
    )
    await connection.execute(text(statement), {"key": key})
