import asyncio

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity.activity import ActivityUnavailable
from ci_coordinator.operator_controls.auth import (
    ControlPlaneScopeAuthorizer,
    RepositoryAccessUnavailable,
)
from ci_coordinator.persistence.activity_repository import PostgresActivityStore


class RuntimeActivityRepositoryAccess:
    def __init__(self, repository: ControlPlaneScopeAuthorizer) -> None:
        self._repository = repository

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        try:
            return await self._repository.allows_scope(actor=actor, scope=scope)
        except RepositoryAccessUnavailable as error:
            raise ActivityUnavailable("activity repository authority unavailable") from error


class ActivityCleanup:
    def __init__(self, store: PostgresActivityStore) -> None:
        self._store = store

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        if not abort_signal.is_set():
            await self._store.cleanup()
