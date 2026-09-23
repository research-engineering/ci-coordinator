from __future__ import annotations

import re
from datetime import date, datetime

from ci_coordinator.ci_economics.discovery import (
    DISCOVERY_PAGE_SIZE,
    MAX_DISCOVERY_PAGES,
    DiscoveryPageTermination,
    ProviderObservationPage,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    non_negative_safe_integer,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github._routes import (
    GitHubPage,
    workflow_run_attempt_path,
    workflow_run_created_query,
    workflow_run_path,
)
from ci_coordinator.integrations.github.actions_client import ActionsClient
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_economics_admission import (
    bound_economics_response,
    load_economics_repository,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubSuccess,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    next_page_number,
    pagination_count_matches,
    terminal_pagination,
)
from ci_coordinator.integrations.github.transport import InstallationTransportFactory
from ci_coordinator.kernel import hash_object

_CREATED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")


class GitHubCiEconomicsSources:
    def __init__(
        self,
        transport_factory: InstallationTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if (
            type(api_version) is not str
            or len(api_version) != 10
            or date.fromisoformat(api_version).isoformat() != api_version
        ):
            raise ValueError("source resolution API version must be an ISO calendar date")
        self._transport_factory = transport_factory
        self._api_version = api_version

    async def resolve_attempt(
        self,
        scope: RepositoryScope,
        workflow_run_id: int,
        run_attempt: int,
    ) -> ProviderRunCollectionSource | ProviderAttemptDeferred:
        if type(scope) is not RepositoryScope:
            raise TypeError("source resolution requires an exact repository scope")
        if any(positive_safe_integer(value) is None for value in (workflow_run_id, run_attempt)):
            raise ValueError("source resolution requires positive safe run and attempt IDs")
        client = ActionsClient(
            self._transport_factory.for_installation(scope.installation_id),
            api_version=self._api_version,
        )
        repository = await load_economics_repository(client, scope, api_version=self._api_version)
        if isinstance(repository, ProviderAttemptDeferred):
            return repository
        attempt_body = bound_economics_response(
            await client.get_workflow_run_attempt(repository, workflow_run_id, run_attempt),
            operation="actions.get_workflow_run_attempt",
            path=workflow_run_attempt_path(repository, workflow_run_id, run_attempt),
            api_version=self._api_version,
        )
        if isinstance(attempt_body, ProviderAttemptDeferred):
            return attempt_body
        attempt_source = _decode_source(
            json_object_or_none(attempt_body),
            scope,
            self._api_version,
            expected_run_id=workflow_run_id,
            expected_attempt=run_attempt,
        )
        if isinstance(attempt_source, ProviderAttemptDeferred):
            return attempt_source
        run_body = bound_economics_response(
            await client.get_workflow_run(repository, workflow_run_id),
            operation="actions.get_workflow_run",
            path=workflow_run_path(repository, workflow_run_id),
            api_version=self._api_version,
        )
        if isinstance(run_body, ProviderAttemptDeferred):
            return run_body
        run_source = _decode_source(
            json_object_or_none(run_body),
            scope,
            self._api_version,
            expected_run_id=workflow_run_id,
        )
        if isinstance(run_source, ProviderAttemptDeferred):
            return run_source
        if (
            run_source.attempt.head_sha != attempt_source.attempt.head_sha
            or run_source.attempt.run_attempt < run_attempt
        ):
            return ProviderAttemptDeferred("provider_binding_mismatch")
        return _provider_source(
            attempt_source.attempt, run_source.run_created_at, self._api_version
        )

    async def discover_page(
        self,
        scope: RepositoryScope,
        window: RunDiscoveryWindow,
        *,
        page_number: int,
    ) -> ProviderRunDiscoveryPage | ProviderAttemptDeferred:
        result = await self._read_discovery_page(scope, window, page_number=page_number)
        return result if isinstance(result, ProviderAttemptDeferred) else result[0]

    async def discover_observation_page(
        self,
        scope: RepositoryScope,
        window: RunDiscoveryWindow,
        *,
        page_number: int,
    ) -> ProviderObservationPage | ProviderAttemptDeferred:
        result = await self._read_discovery_page(scope, window, page_number=page_number)
        if isinstance(result, ProviderAttemptDeferred):
            return result
        page, raw_workflow_ids = result
        workflow_ids: list[int] = []
        for raw in raw_workflow_ids:
            workflow_id = positive_safe_integer(raw)
            if workflow_id is None:
                return ProviderAttemptDeferred("provider_malformed")
            workflow_ids.append(workflow_id)
        return ProviderObservationPage(page, tuple(workflow_ids))

    async def _read_discovery_page(
        self,
        scope: RepositoryScope,
        window: RunDiscoveryWindow,
        *,
        page_number: int,
    ) -> tuple[ProviderRunDiscoveryPage, tuple[object, ...]] | ProviderAttemptDeferred:
        if type(scope) is not RepositoryScope or type(window) is not RunDiscoveryWindow:
            raise TypeError("discovery requires an exact scope and window")
        if type(page_number) is not int or not 1 <= page_number <= MAX_DISCOVERY_PAGES:
            raise ValueError("discovery page number exceeds its bound")
        client = ActionsClient(
            self._transport_factory.for_installation(scope.installation_id),
            api_version=self._api_version,
        )
        repository = await load_economics_repository(client, scope, api_version=self._api_version)
        if isinstance(repository, ProviderAttemptDeferred):
            return repository
        page = GitHubPage(page_number, DISCOVERY_PAGE_SIZE)
        path = f"{repository.path}/actions/runs"
        required_query = (workflow_run_created_query(window),)
        outcome = await client.list_repository_workflow_runs(repository, window, page=page)
        body = bound_economics_response(
            outcome,
            operation="actions.list_repository_workflow_runs",
            path=path,
            api_version=self._api_version,
            query=(*required_query, *page.query()),
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
        runs = value.get("workflow_runs")
        if total is None or type(runs) is not list or len(runs) > DISCOVERY_PAGE_SIZE:
            return ProviderAttemptDeferred("provider_malformed")
        sources: list[ProviderRunCollectionSource] = []
        workflow_ids: list[object] = []
        for run in runs:
            source = _decode_source(run, scope, self._api_version)
            if isinstance(source, ProviderAttemptDeferred):
                return source
            sources.append(source)
            value = object_or_none(run)
            if value is None:
                return ProviderAttemptDeferred("provider_malformed")
            workflow_ids.append(value.get("workflow_id"))
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
                    page_size=DISCOVERY_PAGE_SIZE,
                    required_query=required_query,
                    repository_id=scope.repository_id,
                )
                is None
            ):
                return ProviderAttemptDeferred("provider_incomplete")
            termination = "truncated" if page_number == MAX_DISCOVERY_PAGES else "next_page"
        elif terminal_pagination(pagination):
            end_offset = (page_number - 1) * DISCOVERY_PAGE_SIZE + len(sources)
            termination = "exhausted" if end_offset == total else "truncated"
        else:
            return ProviderAttemptDeferred("provider_incomplete")
        try:
            return (
                ProviderRunDiscoveryPage(
                    scope, window, page_number, total, tuple(sources), termination
                ),
                tuple(workflow_ids),
            )
        except ValueError:
            return ProviderAttemptDeferred("provider_malformed")


def _decode_source(
    raw: object,
    scope: RepositoryScope,
    api_version: str,
    *,
    expected_run_id: int | None = None,
    expected_attempt: int | None = None,
) -> ProviderRunCollectionSource | ProviderAttemptDeferred:
    value = object_or_none(raw)
    repository = object_or_none(value.get("repository")) if value is not None else None
    if value is None or repository is None:
        return ProviderAttemptDeferred("provider_malformed")
    repository_id = positive_safe_integer(repository.get("id"))
    workflow_run_id = positive_safe_integer(value.get("id"))
    run_attempt = positive_safe_integer(value.get("run_attempt"))
    if repository_id is None or workflow_run_id is None or run_attempt is None:
        return ProviderAttemptDeferred("provider_malformed")
    if (
        repository_id != scope.repository_id
        or (expected_run_id is not None and workflow_run_id != expected_run_id)
        or (expected_attempt is not None and run_attempt != expected_attempt)
    ):
        return ProviderAttemptDeferred("provider_binding_mismatch")
    created_at = value.get("created_at")
    head_sha = value.get("head_sha")
    if (
        type(created_at) is not str
        or _CREATED_AT.fullmatch(created_at) is None
        or type(head_sha) is not str
    ):
        return ProviderAttemptDeferred("provider_malformed")
    try:
        created = datetime.fromisoformat(created_at)
        attempt = AttemptIdentity(scope, workflow_run_id, run_attempt, head_sha)
        return _provider_source(attempt, created, api_version)
    except ValueError:
        return ProviderAttemptDeferred("provider_malformed")


def _provider_source(
    attempt: AttemptIdentity, created: datetime, api_version: str
) -> ProviderRunCollectionSource:
    return ProviderRunCollectionSource(
        attempt,
        created,
        api_version,
        hash_object(
            {
                "schemaVersion": "github-economics-source-evidence/v1",
                "attempt": attempt.canonical_mapping(),
                "runCreatedAt": created.isoformat(),
                "providerApiVersion": api_version,
            }
        ),
    )
