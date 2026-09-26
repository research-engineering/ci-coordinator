"""GitHub adapter for exact read-only installation and repository inventory."""

from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._rate_limits import retry_after_seconds
from ci_coordinator.integrations.github._routes import GitHubPage, path_value
from ci_coordinator.integrations.github.app_client import AppIdentityClient
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubSuccess,
    GitHubUnavailable,
)
from ci_coordinator.integrations.github.provider_inventory_decoding import (
    decode_installation,
    decode_installation_page,
    decode_repository_page,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    next_page_number,
    terminal_pagination,
)
from ci_coordinator.integrations.github.request_admission import (
    get_request_matches as _request_matches,
)
from ci_coordinator.provider_inventory import (
    InstallationPageReadResult,
    InstallationReadResult,
    ProviderInventoryUnavailable,
    RepositoryReadResult,
)

_REPOSITORIES_PATH = "/installation/repositories"


class RepositoryInventoryClient(GitHubProtocolClient):
    async def list_repositories(
        self,
        page: GitHubPage,
    ) -> GitHubSuccess | GitHubIncomplete | GitHubUnavailable:
        return await self._get(
            operation="provider_inventory.list_repositories",
            path=_REPOSITORIES_PATH,
            query=page.query(),
        )


class GitHubProviderInventory:
    def __init__(
        self,
        transport_factory: GitHubAppTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if type(api_version) is not str or not api_version:
            raise ValueError("provider inventory API version must be non-empty")
        self._transport_factory = transport_factory
        self._api_version = api_version

    async def list_installations(self, *, page: int, per_page: int) -> InstallationPageReadResult:
        github_page = GitHubPage(page, per_page)
        path = "/app/installations"
        outcome = await AppIdentityClient(
            self._transport_factory.for_app(),
            api_version=self._api_version,
        ).list_installations(github_page)
        if isinstance(outcome, GitHubUnavailable):
            return _unavailable(outcome)
        if not isinstance(outcome, (GitHubSuccess, GitHubIncomplete)) or not _request_matches(
            outcome.request,
            operation="app_identity.list_installations",
            path=path,
            query=github_page.query(),
            api_version=self._api_version,
        ):
            return ProviderInventoryUnavailable("provider_binding_mismatch")
        if isinstance(outcome, GitHubIncomplete):
            if (
                next_page_number(
                    outcome.response.pagination,
                    expected_path=path,
                    current_page=page,
                    page_size=per_page,
                )
                is None
            ):
                return ProviderInventoryUnavailable("malformed_provider_response")
            has_next_page = True
        else:
            if not terminal_pagination(outcome.response.pagination):
                return ProviderInventoryUnavailable("malformed_provider_response")
            has_next_page = False
        result = decode_installation_page(
            outcome.response.body,
            page=page,
            per_page=per_page,
            has_next_page=has_next_page,
        )
        return result or ProviderInventoryUnavailable("malformed_provider_response")

    async def get_installation(self, installation_id: int) -> InstallationReadResult:
        path = f"/app/installations/{path_value(str(installation_id))}"
        outcome = await AppIdentityClient(
            self._transport_factory.for_app(),
            api_version=self._api_version,
        ).get_installation(installation_id)
        if isinstance(outcome, GitHubUnavailable):
            return _unavailable(outcome)
        if not isinstance(outcome, GitHubSuccess) or not _request_matches(
            outcome.request,
            operation="app_identity.get_installation",
            path=path,
            query=(),
            api_version=self._api_version,
        ):
            return ProviderInventoryUnavailable("provider_binding_mismatch")
        if not terminal_pagination(outcome.response.pagination):
            return ProviderInventoryUnavailable("malformed_provider_response")
        installation = decode_installation(outcome.response.body)
        if installation is None:
            return ProviderInventoryUnavailable("malformed_provider_response")
        if installation.installation_id != installation_id:
            return ProviderInventoryUnavailable("provider_binding_mismatch")
        return installation

    async def list_repositories(
        self,
        installation_id: int,
        *,
        page: int,
        per_page: int,
    ) -> RepositoryReadResult:
        github_page = GitHubPage(page, per_page)
        outcome = await RepositoryInventoryClient(
            self._transport_factory.for_installation(installation_id),
            api_version=self._api_version,
        ).list_repositories(github_page)
        if isinstance(outcome, GitHubUnavailable):
            return _unavailable(outcome)
        if not isinstance(outcome, (GitHubSuccess, GitHubIncomplete)) or not _request_matches(
            outcome.request,
            operation="provider_inventory.list_repositories",
            path=_REPOSITORIES_PATH,
            query=github_page.query(),
            api_version=self._api_version,
        ):
            return ProviderInventoryUnavailable("provider_binding_mismatch")
        if isinstance(outcome, GitHubIncomplete):
            has_next_page = (
                next_page_number(
                    outcome.response.pagination,
                    expected_path=_REPOSITORIES_PATH,
                    current_page=page,
                    page_size=per_page,
                )
                is not None
            )
            if not has_next_page:
                return ProviderInventoryUnavailable("malformed_provider_response")
        else:
            if not terminal_pagination(outcome.response.pagination):
                return ProviderInventoryUnavailable("malformed_provider_response")
            has_next_page = False
        repository_page = decode_repository_page(
            outcome.response.body,
            installation_id=installation_id,
            page=page,
            per_page=per_page,
            has_next_page=has_next_page,
        )
        return repository_page or ProviderInventoryUnavailable("malformed_provider_response")


def _unavailable(outcome: GitHubUnavailable) -> ProviderInventoryUnavailable:
    failure = outcome.failure
    if failure.kind == "rate_limited":
        return ProviderInventoryUnavailable(
            "rate_limited",
            retry_after_seconds(failure),
        )
    if failure.kind == "not_found":
        return ProviderInventoryUnavailable("not_found")
    if failure.kind in {
        "missing_api_version_provenance",
        "api_version_provenance_mismatch",
    }:
        return ProviderInventoryUnavailable("provider_binding_mismatch")
    return ProviderInventoryUnavailable("unavailable")
