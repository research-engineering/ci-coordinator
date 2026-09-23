from __future__ import annotations

import asyncio
import math

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.provider_inventory import GitHubProviderInventory
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable
from ci_coordinator.provider_inventory import ProviderInventoryUnavailable

_PAGE_SIZE = 100
_MAXIMUM_PAGES = 100


class GitHubRepositoryAccess:
    def __init__(
        self, transport_factory: GitHubAppTransportFactory, *, timeout_seconds: float = 10.0
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("membership timeout must be positive and finite")
        self._inventory = GitHubProviderInventory(transport_factory)
        self._timeout_seconds = timeout_seconds

    async def allows_repository(self, scope: RepositoryScope) -> bool:
        deadline = asyncio.timeout(self._timeout_seconds)
        try:
            async with deadline:
                return await self._observe(scope)
        except TimeoutError:
            if not deadline.expired():
                raise
            raise RepositoryAccessUnavailable() from None

    async def _observe(self, scope: RepositoryScope) -> bool:
        installation = await self._inventory.get_installation(scope.installation_id)
        if isinstance(installation, ProviderInventoryUnavailable):
            return _unavailable(installation)
        if installation.state != "active":
            return False
        for page in range(1, _MAXIMUM_PAGES + 1):
            result = await self._inventory.list_repositories(
                scope.installation_id, page=page, per_page=_PAGE_SIZE
            )
            if isinstance(result, ProviderInventoryUnavailable):
                return _unavailable(result)
            if any(
                repository.owner_id != installation.account_id for repository in result.repositories
            ):
                raise RepositoryAccessUnavailable()
            for repository in result.repositories:
                if repository.scope == scope:
                    return not repository.disabled
            if not result.has_next_page:
                return False
        raise RepositoryAccessUnavailable()


def _unavailable(result: ProviderInventoryUnavailable) -> bool:
    if result.reason == "not_found":
        return False
    raise RepositoryAccessUnavailable()
