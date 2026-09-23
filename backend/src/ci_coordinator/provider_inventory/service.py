"""Authorization-first orchestration for bounded provider inventory reads."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ci_coordinator.kernel import Clock
from ci_coordinator.provider_inventory.model import (
    InstallationCatalog,
    InstallationCatalogResult,
    InstallationFailure,
    InstallationSummary,
    ProviderInstallationPage,
    ProviderInventoryForbidden,
    ProviderInventoryIneligible,
    ProviderInventoryUnavailable,
    ProviderRepositoryPage,
    RepositoryPage,
    RepositoryPageResult,
)
from ci_coordinator.provider_inventory.ports import (
    ProviderInstallationReader,
    ProviderInventoryAuthorizer,
    ProviderRepositoryReader,
)


class ProviderInventoryService:
    def __init__(
        self,
        *,
        authorizer: ProviderInventoryAuthorizer,
        installations: ProviderInstallationReader,
        repositories: ProviderRepositoryReader,
        clock: Clock,
    ) -> None:
        self._authorizer = authorizer
        self._installations = installations
        self._repositories = repositories
        self._clock = clock

    async def list_installations(
        self,
        *,
        actor: str,
        page: int = 1,
        per_page: int = 30,
    ) -> InstallationCatalogResult:
        grant = self._authorizer.authorized_installations(actor)
        if grant is None:
            return ProviderInventoryForbidden()
        ProviderInstallationPage(page, per_page, False, ())
        if grant.mode == "app":
            provider_page = await self._installations.list_installations(
                page=page, per_page=per_page
            )
            if isinstance(provider_page, ProviderInventoryUnavailable):
                return provider_page
            if (provider_page.page, provider_page.per_page) != (page, per_page):
                return ProviderInventoryUnavailable("provider_binding_mismatch")
            return InstallationCatalog(
                observed_at=self._clock.now(),
                complete=True,
                installations=provider_page.installations,
                failures=(),
                page=page,
                per_page=per_page,
                has_next_page=provider_page.has_next_page,
            )
        start = (page - 1) * per_page
        ordered_installation_ids = grant.installation_ids[start : start + per_page]
        results = await asyncio.gather(
            *(self._installations.get_installation(value) for value in ordered_installation_ids)
        )
        installations: list[InstallationSummary] = []
        failures: list[InstallationFailure] = []
        for installation_id, result in zip(ordered_installation_ids, results, strict=True):
            if isinstance(result, ProviderInventoryUnavailable):
                failures.append(
                    InstallationFailure(
                        installation_id,
                        result.reason,
                        result.retry_after_seconds,
                    )
                )
            else:
                if result.installation_id != installation_id:
                    failures.append(
                        InstallationFailure(installation_id, "provider_binding_mismatch")
                    )
                else:
                    installations.append(result)
        return InstallationCatalog(
            observed_at=self._clock.now(),
            complete=not failures,
            installations=tuple(installations),
            failures=tuple(failures),
            page=page,
            per_page=per_page,
            has_next_page=start + per_page < len(grant.installation_ids),
        )

    async def list_repositories(
        self,
        *,
        actor: str,
        installation_id: int,
        page: int,
        per_page: int,
    ) -> RepositoryPageResult:
        authorized_repository_ids = self._authorizer.workbench_repository_ids(
            actor,
            installation_id,
        )
        if authorized_repository_ids is None:
            return ProviderInventoryForbidden()
        installation = await self._installations.get_installation(installation_id)
        if isinstance(installation, ProviderInventoryUnavailable):
            return installation
        if installation.installation_id != installation_id:
            return ProviderInventoryUnavailable("provider_binding_mismatch")
        if installation.state == "suspended":
            return ProviderInventoryIneligible("suspended")
        if installation.state == "unsupported":
            return ProviderInventoryIneligible("unsupported_account_type")
        provider_page = await self._repositories.list_repositories(
            installation_id,
            page=page,
            per_page=per_page,
        )
        if isinstance(provider_page, ProviderInventoryUnavailable):
            return provider_page
        if not isinstance(provider_page, ProviderRepositoryPage):
            raise RuntimeError("unsupported provider repository outcome")
        if (provider_page.page, provider_page.per_page) != (page, per_page) or any(
            repository.owner_id != installation.account_id
            or repository.scope.installation_id != installation_id
            for repository in provider_page.repositories
        ):
            return ProviderInventoryUnavailable("provider_binding_mismatch")
        app_scope = self._authorizer.permits_all_workbench_repositories(actor, installation_id)
        repositories = tuple(
            replace(
                repository,
                workbench_authorized=(
                    not repository.disabled
                    if app_scope
                    else repository.scope.repository_id in authorized_repository_ids
                ),
            )
            for repository in provider_page.repositories
        )
        return RepositoryPage(
            installation=installation,
            observed_at=self._clock.now(),
            page=provider_page.page,
            per_page=provider_page.per_page,
            total_count=provider_page.total_count,
            has_next_page=provider_page.has_next_page,
            repositories=repositories,
        )
