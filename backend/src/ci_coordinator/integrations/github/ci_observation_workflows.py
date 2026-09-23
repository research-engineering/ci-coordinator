from ci_coordinator.ci_economics.discovery import DiscoveryPageTermination
from ci_coordinator.ci_economics.observation_workflows import (
    MAX_OBSERVATION_WORKFLOW_PAGES,
    OBSERVATION_WORKFLOW_PAGE_SIZE,
    ObservationWorkflowChoice,
    ObservationWorkflowPage,
    require_workflow_page,
)
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    non_negative_safe_integer,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github._routes import GitHubPage
from ci_coordinator.integrations.github.actions_client import ActionsClient
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_economics_admission import (
    bound_economics_response,
    load_economics_repository,
)
from ci_coordinator.integrations.github.contracts import GitHubIncomplete, GitHubSuccess
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    next_page_number,
    pagination_count_matches,
    terminal_pagination,
)
from ci_coordinator.integrations.github.transport import InstallationTransportFactory
from ci_coordinator.integrations.github.workflow_catalog_client import WorkflowCatalogClient


class GitHubCiObservationWorkflows:
    def __init__(self, transport_factory: InstallationTransportFactory) -> None:
        self._transport_factory = transport_factory

    async def workflow_page(
        self, scope: RepositoryScope, *, page_number: int
    ) -> ObservationWorkflowPage | ProviderAttemptDeferred:
        if type(scope) is not RepositoryScope:
            raise TypeError("workflow catalogue requires an exact scope")
        require_workflow_page(page_number)
        transport = self._transport_factory.for_installation(scope.installation_id)
        repository = await load_economics_repository(
            ActionsClient(transport, api_version=GITHUB_API_VERSION),
            scope,
            api_version=GITHUB_API_VERSION,
        )
        if isinstance(repository, ProviderAttemptDeferred):
            return repository
        page = GitHubPage(page_number, OBSERVATION_WORKFLOW_PAGE_SIZE)
        path = f"{repository.path}/actions/workflows"
        client = WorkflowCatalogClient(transport, api_version=GITHUB_API_VERSION)
        outcome = await client.list_workflows(repository, page=page)
        body = bound_economics_response(
            outcome,
            operation="workflow_catalog.list_workflows",
            path=path,
            api_version=GITHUB_API_VERSION,
            query=page.query(),
            paginated=True,
        )
        if isinstance(body, ProviderAttemptDeferred):
            return body
        if not isinstance(outcome, (GitHubSuccess, GitHubIncomplete)):
            return ProviderAttemptDeferred("provider_unavailable")
        value = json_object_or_none(body)
        if value is None:
            return ProviderAttemptDeferred("provider_malformed")
        total = non_negative_safe_integer(value.get("total_count"))
        rows = value.get("workflows")
        if total is None or type(rows) is not list or len(rows) > OBSERVATION_WORKFLOW_PAGE_SIZE:
            return ProviderAttemptDeferred("provider_malformed")
        workflows: list[ObservationWorkflowChoice] = []
        for raw in rows:
            choice = _choice(raw)
            if choice is None:
                return ProviderAttemptDeferred("provider_malformed")
            workflows.append(choice)
        pagination = outcome.response.pagination
        if not pagination_count_matches(pagination, total):
            return ProviderAttemptDeferred("provider_malformed")
        termination: DiscoveryPageTermination
        if isinstance(outcome, GitHubIncomplete):
            if (
                next_page_number(
                    pagination,
                    expected_path=path,
                    current_page=page_number,
                    page_size=OBSERVATION_WORKFLOW_PAGE_SIZE,
                    repository_id=scope.repository_id,
                )
                is None
            ):
                return ProviderAttemptDeferred("provider_incomplete")
            termination = (
                "truncated" if page_number == MAX_OBSERVATION_WORKFLOW_PAGES else "next_page"
            )
        elif terminal_pagination(pagination):
            end_offset = (page_number - 1) * OBSERVATION_WORKFLOW_PAGE_SIZE + len(workflows)
            termination = "exhausted" if end_offset == total else "truncated"
        else:
            return ProviderAttemptDeferred("provider_incomplete")
        try:
            return ObservationWorkflowPage(scope, page_number, total, tuple(workflows), termination)
        except (TypeError, ValueError):
            return ProviderAttemptDeferred("provider_malformed")


def _choice(raw: object) -> ObservationWorkflowChoice | None:
    value = object_or_none(raw)
    if value is None:
        return None
    workflow_id = positive_safe_integer(value.get("id"))
    name, path, state = (value.get(key) for key in ("name", "path", "state"))
    if (
        workflow_id is None
        or not isinstance(name, str)
        or not isinstance(path, str)
        or not isinstance(state, str)
    ):
        return None
    try:
        return ObservationWorkflowChoice(workflow_id, name, path, state)
    except (TypeError, ValueError):
        return None
