from __future__ import annotations

from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github._routes import GitHubRepository, repository_id_path
from ci_coordinator.integrations.github.actions_client import ActionsClient
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_MAXIMUM_RESPONSE_BODY_BYTES,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubOutcome,
    GitHubQueryParameter,
    GitHubSuccess,
)
from ci_coordinator.integrations.github.reconciliation_observer_decoding import decode_repository
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    terminal_pagination,
)


async def load_economics_repository(
    client: ActionsClient, scope: RepositoryScope, *, api_version: str
) -> GitHubRepository | ProviderAttemptDeferred:
    body = bound_economics_response(
        await client.get_repository_by_id(scope.repository_id),
        operation="repositories.get_by_id",
        path=repository_id_path(scope.repository_id),
        api_version=api_version,
    )
    if isinstance(body, ProviderAttemptDeferred):
        return body
    repository = decode_repository(body)
    if repository is None:
        return ProviderAttemptDeferred("provider_malformed")
    if repository[0] != scope.repository_id:
        return ProviderAttemptDeferred("provider_binding_mismatch")
    return repository[1]


def bound_economics_response(
    outcome: GitHubOutcome,
    *,
    operation: str,
    path: str,
    api_version: str,
    query: tuple[GitHubQueryParameter, ...] = (),
    paginated: bool = False,
) -> bytes | ProviderAttemptDeferred:
    if not isinstance(outcome, GitHubSuccess) and not (
        paginated and isinstance(outcome, GitHubIncomplete)
    ):
        return ProviderAttemptDeferred("provider_unavailable")
    request = outcome.request
    if (
        request.method != "GET"
        or request.operation != operation
        or request.path != path
        or request.query != query
        or request.body is not None
        or request.api_version != api_version
        or outcome.response.api_version != api_version
    ):
        return ProviderAttemptDeferred("provider_binding_mismatch")
    body = outcome.response.body
    maximum_bytes = (
        GITHUB_MAXIMUM_RESPONSE_BODY_BYTES
        if operation == "actions.list_repository_workflow_runs"
        else 1_048_576
    )
    if (
        (not paginated and not terminal_pagination(outcome.response.pagination))
        or outcome.response.status != 200
        or type(body) is not bytes
        or len(body) > maximum_bytes
    ):
        return ProviderAttemptDeferred("provider_malformed")
    return body
