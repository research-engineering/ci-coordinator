"""Ports owned by the read-only provider inventory use case."""

from __future__ import annotations

from typing import Protocol

from ci_coordinator.provider_inventory.model import (
    InstallationCatalogResult,
    InstallationPageReadResult,
    InstallationReadResult,
    InventoryGrant,
    RepositoryPageResult,
    RepositoryReadResult,
)


class ProviderInventoryUseCase(Protocol):
    async def list_installations(
        self,
        *,
        actor: str,
        page: int = 1,
        per_page: int = 30,
    ) -> InstallationCatalogResult: ...

    async def list_repositories(
        self,
        *,
        actor: str,
        installation_id: int,
        page: int,
        per_page: int,
    ) -> RepositoryPageResult: ...


class ProviderInventoryAuthorizer(Protocol):
    def authorized_installations(self, actor: str) -> InventoryGrant | None: ...

    def permits_all_workbench_repositories(self, actor: str, installation_id: int) -> bool: ...

    def workbench_repository_ids(
        self,
        actor: str,
        installation_id: int,
    ) -> frozenset[int] | None: ...


class ProviderInstallationReader(Protocol):
    async def get_installation(self, installation_id: int) -> InstallationReadResult: ...

    async def list_installations(
        self,
        *,
        page: int,
        per_page: int,
    ) -> InstallationPageReadResult: ...


class ProviderRepositoryReader(Protocol):
    async def list_repositories(
        self,
        installation_id: int,
        *,
        page: int,
        per_page: int,
    ) -> RepositoryReadResult: ...
