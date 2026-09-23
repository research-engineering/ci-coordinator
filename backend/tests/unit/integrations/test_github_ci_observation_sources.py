import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ci_coordinator.ci_economics.discovery import (
    ProviderObservationPage,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.integrations.github.ci_economics_sources import GitHubCiEconomicsSources
from ci_coordinator.integrations.github.contracts import GitHubPaginationEvidence, GitHubResponse
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from ._economics_source_support import SCOPE, Provider, repository, response, run

WINDOW = RunDiscoveryWindow(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC))


def _discover(
    page: GitHubResponse,
    *,
    observation: bool = True,
) -> tuple[ProviderRunDiscoveryPage | ProviderObservationPage | ProviderAttemptDeferred, Provider]:
    provider = Provider([response(repository()), page])
    adapter = GitHubCiEconomicsSources(provider)
    result = asyncio.run(
        adapter.discover_observation_page(SCOPE, WINDOW, page_number=1)
        if observation
        else adapter.discover_page(SCOPE, WINDOW, page_number=1)
    )
    assert provider.installations == [SCOPE.installation_id]
    assert len(provider.requests) == 2
    assert not provider.responses
    return result, provider


@pytest.mark.parametrize("count", [0, 1, 100])
def test_observation_pairs_workflow_ids_in_source_order_without_changing_identity(
    count: int,
) -> None:
    rows = [{**run(), "id": count - i, "workflow_id": 10 + i % 2} for i in range(count)]
    value = response({"total_count": count, "workflow_runs": rows})
    observed, observed_provider = _discover(value)
    manual, manual_provider = _discover(value, observation=False)
    assert isinstance(observed, ProviderObservationPage)
    assert isinstance(manual, ProviderRunDiscoveryPage)
    assert observed.page == manual
    assert observed.workflow_ids == tuple(10 + i % 2 for i in range(count))
    assert tuple(source.attempt.workflow_run_id for source in observed.page.sources) == tuple(
        range(count, 0, -1)
    )
    assert observed_provider.requests == manual_provider.requests
    assert tuple(source.source_id for source in observed.page.sources) == tuple(
        source.source_id for source in manual.sources
    )


@pytest.mark.parametrize(
    "workflow_id", [None, True, False, 1.0, "10", 0, -1, MAX_SAFE_JSON_INTEGER + 1, [], {}]
)
def test_invalid_workflow_identity_is_not_silently_filtered_or_manual_breakage(
    workflow_id: object,
) -> None:
    value = response({"total_count": 1, "workflow_runs": [{**run(), "workflow_id": workflow_id}]})
    observed, _ = _discover(value)
    manual, _ = _discover(value, observation=False)
    assert observed == ProviderAttemptDeferred("provider_malformed")
    assert isinstance(manual, ProviderRunDiscoveryPage)


def test_missing_workflow_identity_is_distinct_from_empty_valid_population() -> None:
    missing, _ = _discover(response({"total_count": 1, "workflow_runs": [run()]}))
    empty, _ = _discover(response({"total_count": 0, "workflow_runs": []}))
    assert missing == ProviderAttemptDeferred("provider_malformed")
    assert isinstance(empty, ProviderObservationPage) and empty.workflow_ids == ()


@pytest.mark.parametrize("workflow_id", [1, MAX_SAFE_JSON_INTEGER])
def test_workflow_identity_inclusive_safe_extremes(workflow_id: int) -> None:
    result, _ = _discover(
        response({"total_count": 1, "workflow_runs": [{**run(), "workflow_id": workflow_id}]})
    )
    assert isinstance(result, ProviderObservationPage)
    assert result.workflow_ids == (workflow_id,)


@pytest.mark.parametrize(
    "operand", ["repository", "head", "created", "total", "link", "api-version"]
)
def test_observation_reuses_source_and_pagination_failure_admission(operand: str) -> None:
    row = {**run(), "workflow_id": 10}
    value: dict[str, object] = {"total_count": 1, "workflow_runs": [row]}
    if operand == "repository":
        row["repository"] = {"id": SCOPE.repository_id + 1}
    elif operand == "head":
        row["head_sha"] = "invalid"
    elif operand == "created":
        row["created_at"] = "2026-09-09T00:00:00Z"
    elif operand == "total":
        value["total_count"] = False
    page = response(value)
    if operand == "link":
        page = replace(
            page,
            pagination=GitHubPaginationEvidence(
                False, 1, None, "https://evil.example/runs?page=2", "next_page"
            ),
        )
    elif operand == "api-version":
        page = replace(page, api_version="2022-11-28")
    observed, _ = _discover(page)
    manual, _ = _discover(page, observation=False)
    assert isinstance(observed, ProviderAttemptDeferred)
    assert observed == manual
