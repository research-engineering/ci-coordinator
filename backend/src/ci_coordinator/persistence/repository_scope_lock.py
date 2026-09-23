"""One PostgreSQL transaction lock domain for repository authority mutations."""

from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import canonical_json

_LOCK_DOMAIN = b"ci-coordinator-repository-authority-lock/v1\0"


async def lock_repository_scope(
    connection: AsyncConnection,
    scope: RepositoryScope,
) -> None:
    class_id, object_id = repository_scope_lock_key(scope)
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(:class_id, :object_id)"),
        {"class_id": class_id, "object_id": object_id},
    )


def repository_scope_lock_key(scope: RepositoryScope) -> tuple[int, int]:
    if type(scope) is not RepositoryScope:
        raise TypeError("repository authority lock scope must be exact")
    digest = sha256(_LOCK_DOMAIN + canonical_json(_scope_mapping(scope))).digest()
    return (
        int.from_bytes(digest[:4], byteorder="big", signed=True),
        int.from_bytes(digest[4:8], byteorder="big", signed=True),
    )


def _scope_mapping(scope: RepositoryScope) -> dict[str, int]:
    return {
        "installationId": scope.installation_id,
        "repositoryId": scope.repository_id,
    }
