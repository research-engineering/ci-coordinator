from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest

from ci_coordinator.ci_economics.discovery import (
    ProviderObservationPage,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_VERSION,
    GITHUB_MAXIMUM_RESPONSE_BODY_BYTES,
)
from ci_coordinator.integrations.github.ci_economics_sources import GitHubCiEconomicsSources
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubQueryParameter,
    GitHubRequest,
    GitHubResponse,
)
from ci_coordinator.integrations.github.installation_request_admission import (
    installation_request_is_admitted,
)

from ._economics_source_support import SCOPE, Provider, repository, response, run

WINDOW = RunDiscoveryWindow(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC))
CREATED = "2026-09-01T00:00:00Z..2026-09-08T00:00:00Z"
PATH = "/repos/acme/service/actions/runs"
REQUEST = GitHubRequest(
    "actions.list_repository_workflow_runs",
    "GET",
    PATH,
    GITHUB_API_VERSION,
    query=(
        GitHubQueryParameter("created", CREATED),
        GitHubQueryParameter("page", "1"),
        GitHubQueryParameter("per_page", "100"),
    ),
)


def _next_page(page: int) -> str:
    return (
        "https://api.github.com"
        + PATH
        + "?"
        + urlencode({"created": CREATED, "page": str(page), "per_page": "100"})
    )


def _page(*, count: int = 100, total: int = 101, next_page: str | None = None) -> GitHubResponse:
    value = response(
        {"total_count": total, "workflow_runs": [{**run(), "id": i + 1} for i in range(count)]}
    )
    if next_page is None:
        return value
    return replace(
        value, pagination=GitHubPaginationEvidence(False, 1, None, next_page, "next_page")
    )


def _discover(
    value: GitHubResponse, *, page_number: int = 1
) -> ProviderRunDiscoveryPage | ProviderAttemptDeferred:
    provider = Provider([response(repository()), value])
    result = asyncio.run(
        GitHubCiEconomicsSources(provider).discover_page(
            SCOPE,
            WINDOW,
            page_number=page_number,
        )
    )
    assert provider.installations == [SCOPE.installation_id]
    assert provider.requests[-1].query == (
        GitHubQueryParameter("created", CREATED),
        GitHubQueryParameter("page", str(page_number)),
        GitHubQueryParameter("per_page", "100"),
    )
    return result


@pytest.mark.parametrize("count", [0, 1, 100])
def test_terminal_page_exposes_its_exact_scope_window_and_population(count: int) -> None:
    result = _discover(_page(count=count, total=count))
    assert isinstance(result, ProviderRunDiscoveryPage)
    assert result.termination == "exhausted"
    assert result.scope == SCOPE and result.window == WINDOW and result.page_number == 1
    assert result.provider_total == len(result.sources) == count
    assert all(source.attempt.run_attempt == 2 for source in result.sources)


@pytest.mark.parametrize("path", [PATH, "/repositories/202/actions/runs"])
def test_nonterminal_page_preserves_exact_next_page_without_scanning_it(path: str) -> None:
    result = _discover(_page(next_page=_next_page(2).replace(PATH, path)))
    assert isinstance(result, ProviderRunDiscoveryPage)
    assert result.termination == "next_page" and result.provider_total == 101
    assert len(result.sources) == 100


def test_realistic_hundred_run_page_can_exceed_one_mebibyte() -> None:
    value = _page(count=100, total=100)
    payload = json.loads(value.body)
    for item in payload["workflow_runs"]:
        item["head_commit"] = {"message": "x" * 14_000}
        item["workflow_id"] = 404
    body = json.dumps(payload).encode()
    assert 1_048_576 < len(body) < GITHUB_MAXIMUM_RESPONSE_BODY_BYTES
    result = _discover(replace(value, body=body))
    assert isinstance(result, ProviderRunDiscoveryPage)
    assert result.termination == "exhausted" and len(result.sources) == 100
    provider = Provider([response(repository()), replace(value, body=body)])
    observed = asyncio.run(
        GitHubCiEconomicsSources(provider).discover_observation_page(SCOPE, WINDOW, page_number=1)
    )
    assert isinstance(observed, ProviderObservationPage)
    assert observed.workflow_ids == (404,) * 100


@pytest.mark.parametrize("extra", [0, 1])
def test_discovery_keeps_the_transport_byte_limit_with_other_guards_satisfied(extra: int) -> None:
    value = _page(count=100, total=100)
    body = value.body.ljust(GITHUB_MAXIMUM_RESPONSE_BODY_BYTES + extra, b" ")
    assert json.loads(body) == json.loads(value.body)
    result = _discover(replace(value, body=body))
    if extra:
        assert result == ProviderAttemptDeferred("provider_malformed")
    else:
        assert isinstance(result, ProviderRunDiscoveryPage) and len(result.sources) == 100


@pytest.mark.parametrize(
    "next_page", [None, _next_page(11)], ids=["provider-stop", "remaining-link"]
)
def test_provider_search_ceiling_remains_truncated(next_page: str | None) -> None:
    result = _discover(_page(total=1001, next_page=next_page), page_number=10)
    assert isinstance(result, ProviderRunDiscoveryPage)
    assert result.termination == "truncated" and result.provider_total == 1001


def test_early_terminal_page_is_not_mistaken_for_complete_population() -> None:
    result = _discover(_page(count=1, total=5))
    assert isinstance(result, ProviderRunDiscoveryPage)
    assert result.termination == "truncated"


@pytest.mark.parametrize(
    "target",
    [
        _next_page(3),
        _next_page(2).replace("api.github.com", "other.example"),
        _next_page(2).replace("/acme/service/", "/acme/other/"),
        _next_page(2).replace("2026-09-01", "2026-09-02"),
        _next_page(2) + "&created=" + CREATED,
        _next_page(2) + "&status=success",
        _next_page(2).replace("per_page=100", "per_page=99"),
    ],
    ids=["page-gap", "origin", "repository", "window", "duplicate", "filter", "size"],
)
def test_bad_next_link_cannot_extend_source_authority(target: str) -> None:
    assert _discover(_page(next_page=target)) == ProviderAttemptDeferred("provider_incomplete")


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"total_count": True, "workflow_runs": []},
        {"total_count": 0, "workflow_runs": [run()]},
        {"total_count": 2, "workflow_runs": [run(), run()]},
        {"total_count": 101, "workflow_runs": [run()] * 101},
        {"total_count": 1, "workflow_runs": [{**run(), "created_at": "2026-08-31T23:59:59Z"}]},
        {"total_count": 1, "workflow_runs": [{**run(), "created_at": "2026-09-08T00:00:01Z"}]},
    ],
    ids=["missing", "bool-total", "excess-count", "duplicate-run", "oversize", "before", "after"],
)
def test_invalid_population_is_not_a_discovery_result(value: object) -> None:
    assert _discover(response(value)) == ProviderAttemptDeferred("provider_malformed")


@pytest.mark.parametrize("page_number", [True, 0, 11])
def test_page_bound_is_checked_before_installation_selection(page_number: int) -> None:
    provider = Provider([])
    with pytest.raises(ValueError):
        asyncio.run(
            GitHubCiEconomicsSources(provider).discover_page(SCOPE, WINDOW, page_number=page_number)
        )
    assert not provider.installations


def test_only_the_closed_discovery_request_selects_installation_credentials() -> None:
    assert installation_request_is_admitted(REQUEST)
    variants = (
        replace(REQUEST, method="POST"),
        replace(REQUEST, body=b"{}"),
        replace(REQUEST, path=PATH + "/7"),
        replace(REQUEST, query=REQUEST.query[1:]),
        replace(REQUEST, query=tuple(reversed(REQUEST.query))),
        replace(REQUEST, query=(*REQUEST.query, REQUEST.query[0])),
        replace(REQUEST, query=(*REQUEST.query, GitHubQueryParameter("status", "success"))),
    )
    assert all(not installation_request_is_admitted(candidate) for candidate in variants)


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("created", CREATED.replace("09-01", "08-31")),
        ("created", CREATED.replace("T00:00:00Z", "T00:00:00+00:00")),
        ("created", CREATED.replace("2026-09-01", "2026-09-09")),
        ("created", CREATED + " status:success"),
        ("created", ""),
        ("page", "11"),
        ("page", "01"),
        ("page", "0"),
        ("per_page", "99"),
    ],
)
def test_discovery_query_cannot_widen_provider_authority(field_name: str, value: str) -> None:
    query = tuple(
        GitHubQueryParameter(item.name, value) if item.name == field_name else item
        for item in REQUEST.query
    )
    assert not installation_request_is_admitted(replace(REQUEST, query=query))
