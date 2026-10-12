"""One bounded installation-token read of a scoped repository's creation instant."""

from datetime import date

from ci_coordinator.ci_economics.history_administration import (
    RepositoryCreationObserved,
    RepositoryCreationUnavailable,
)
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github._routes import repository_id_path
from ci_coordinator.integrations.github.actions_client import ActionsClient
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_economics_admission import bound_economics_response
from ci_coordinator.integrations.github.provider_inventory_decoding import (
    decode_repository_created_at,
)
from ci_coordinator.integrations.github.transport import InstallationTransportFactory


class GitHubHistoryRepositoryCreation:
    def __init__(
        self,
        transport_factory: InstallationTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if (
            type(api_version) is not str
            or date.fromisoformat(api_version).isoformat() != api_version
        ):
            raise ValueError("history API version must be an ISO calendar date")
        self._transport_factory = transport_factory
        self._api_version = api_version

    async def read_repository_created_at(
        self, scope: RepositoryScope
    ) -> RepositoryCreationObserved | RepositoryCreationUnavailable:
        if type(scope) is not RepositoryScope:
            raise TypeError("repository creation read requires an exact scope")
        client = ActionsClient(
            self._transport_factory.for_installation(
                scope.installation_id, repository_id=scope.repository_id
            ),
            api_version=self._api_version,
        )
        body = bound_economics_response(
            await client.get_repository_by_id(scope.repository_id),
            operation="repositories.get_by_id",
            path=repository_id_path(scope.repository_id),
            api_version=self._api_version,
        )
        if isinstance(body, ProviderAttemptDeferred):
            reason = body.reason
            if reason == "provider_binding_mismatch":
                return RepositoryCreationUnavailable("provider_binding_mismatch")
            if reason == "provider_malformed":
                return RepositoryCreationUnavailable("provider_malformed")
            return RepositoryCreationUnavailable("provider_unavailable")
        repository = json_object_or_none(body)
        if repository is None:
            return RepositoryCreationUnavailable("provider_malformed")
        repository_id = positive_safe_integer(repository.get("id"))
        if repository_id is None:
            return RepositoryCreationUnavailable("provider_malformed")
        if repository_id != scope.repository_id:
            return RepositoryCreationUnavailable("provider_binding_mismatch")
        raw_created_at = repository.get("created_at")
        if raw_created_at is None:
            return RepositoryCreationObserved(scope, None)
        created_at = decode_repository_created_at(raw_created_at)
        if created_at is None:
            return RepositoryCreationUnavailable("provider_malformed")
        return RepositoryCreationObserved(scope, created_at)
