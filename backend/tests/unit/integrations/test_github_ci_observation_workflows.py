import asyncio
from dataclasses import replace

import pytest

from ci_coordinator.ci_economics.observation_workflows import ObservationWorkflowPage
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_observation_workflows import GitHubCiObservationWorkflows
from ci_coordinator.integrations.github.contracts import GitHubPaginationEvidence, GitHubResponse
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from ._economics_source_support import SCOPE, Provider, repository, response

PATH = "/repos/acme/service/actions/workflows"


def choice(workflow_id: int = 1) -> dict[str, object]:
    return {
        "id": workflow_id,
        "name": "Full Check",
        "path": ".github/workflows/full.yml",
        "state": "active",
    }


async def read(
    page: GitHubResponse, *, number: int = 1
) -> tuple[ObservationWorkflowPage | ProviderAttemptDeferred, Provider]:
    provider = Provider([response(repository()), page])
    result = await GitHubCiObservationWorkflows(provider).workflow_page(SCOPE, page_number=number)
    assert provider.installations == [SCOPE.installation_id]
    assert len(provider.requests) == 2 and not provider.responses
    request = provider.requests[1]
    assert request.method == "GET" and request.path == PATH
    assert (
        request.operation == "workflow_catalog.list_workflows"
        and request.api_version == GITHUB_API_VERSION
    )
    assert request.body is None
    assert {item.name: item.value for item in request.query} == {
        "page": str(number),
        "per_page": "100",
    }
    return result, provider


@pytest.mark.parametrize("count", [0, 1, 100])
async def test_bounded_catalogue_returns_numeric_identities(count: int) -> None:
    result, _ = await read(
        response({"total_count": count, "workflows": [choice(i + 1) for i in range(count)]})
    )
    assert isinstance(result, ObservationWorkflowPage)
    assert result.scope == SCOPE and result.page_number == 1
    assert result.termination == "exhausted" and result.provider_total == count
    assert tuple(item.workflow_id for item in result.workflows) == tuple(range(1, count + 1))


@pytest.mark.parametrize(
    "path", ["dynamic/pages/pages-build-deployment", ".github/workflows/_child.yaml"]
)
async def test_provider_paths_are_display_metadata_not_execution_admission(path: str) -> None:
    row = {
        **choice(MAX_SAFE_JSON_INTEGER),
        "path": path,
        "name": "<b>Not markup</b>",
        "state": "future_provider_state",
    }
    result, _ = await read(response({"total_count": 1, "workflows": [row]}))
    assert isinstance(result, ObservationWorkflowPage)
    assert result.workflows[0].workflow_id == MAX_SAFE_JSON_INTEGER
    assert result.workflows[0].path == path and result.workflows[0].name == row["name"]
    assert result.workflows[0].state == row["state"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", None),
        ("id", True),
        ("id", "1"),
        ("id", 1.0),
        ("id", 0),
        ("id", -1),
        ("id", MAX_SAFE_JSON_INTEGER + 1),
        ("name", None),
        ("name", " "),
        ("name", "x" * 257),
        ("path", False),
        ("path", "x" * 1025),
        ("state", ""),
        ("state", "x" * 65),
        ("name", "a\nb"),
    ],
)
async def test_invalid_metadata_is_not_a_silently_missing_workflow(
    field: str, value: object
) -> None:
    result, _ = await read(response({"total_count": 1, "workflows": [{**choice(), field: value}]}))
    assert result == ProviderAttemptDeferred("provider_malformed")


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        {"total_count": True, "workflows": []},
        {"total_count": 0, "workflows": {}},
        {"total_count": 2, "workflows": [choice(), choice()]},
        {"total_count": 101, "workflows": [choice(i + 1) for i in range(101)]},
    ],
)
async def test_malformed_population_never_becomes_an_empty_catalogue(value: object) -> None:
    result, _ = await read(response(value))
    assert result == ProviderAttemptDeferred("provider_malformed")


@pytest.mark.parametrize(
    "number,termination", [(1, "next_page"), (19, "next_page"), (20, "truncated")]
)
@pytest.mark.parametrize("path", [PATH, "/repositories/202/actions/workflows"])
async def test_next_link_is_observed_but_not_eagerly_followed(
    number: int, termination: str, path: str
) -> None:
    value = response({"total_count": 2100, "workflows": [choice(i + 1) for i in range(100)]})
    page = replace(
        value,
        pagination=GitHubPaginationEvidence(
            False,
            1,
            2100,
            f"https://api.github.com{path}?page={number + 1}&per_page=100",
            "next_page",
        ),
    )
    result, _ = await read(page, number=number)
    assert isinstance(result, ObservationWorkflowPage) and result.termination == termination


@pytest.mark.parametrize(
    "link",
    [
        f"https://other.example{PATH}?page=2&per_page=100",
        "https://api.github.com/repos/other/repo/actions/workflows?page=2&per_page=100",
        f"https://api.github.com{PATH}?page=3&per_page=100",
        f"https://api.github.com{PATH}?page=2&per_page=50",
        f"https://api.github.com{PATH}?page=2&per_page=100&page=2",
    ],
)
async def test_hostile_pagination_is_unavailable_not_an_unbounded_read(link: str) -> None:
    value = response({"total_count": 101, "workflows": [choice(i + 1) for i in range(100)]})
    result, _ = await read(
        replace(value, pagination=GitHubPaginationEvidence(False, 1, 101, link, "next_page"))
    )
    assert result == ProviderAttemptDeferred("provider_incomplete")


@pytest.mark.parametrize("number,total", [(1, 2), (2, 0)])
async def test_changing_population_is_truncated_not_a_complete_snapshot(
    number: int, total: int
) -> None:
    result, _ = await read(response({"total_count": total, "workflows": []}), number=number)
    assert isinstance(result, ObservationWorkflowPage) and result.termination == "truncated"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
async def test_provider_failure_is_not_deletion_or_empty_success(status: int) -> None:
    result, _ = await read(replace(response({"message": "not available"}), status=status))
    assert result == ProviderAttemptDeferred("provider_unavailable")


@pytest.mark.parametrize("operand", ["api-version", "body-size", "count"])
async def test_response_envelope_binds_api_size_and_pagination_count(operand: str) -> None:
    value = response({"total_count": 0, "workflows": []})
    if operand == "api-version":
        value = replace(value, api_version="2022-11-28")
    elif operand == "body-size":
        value = replace(value, body=b" " * 1_048_577)
    else:
        value = replace(value, pagination=GitHubPaginationEvidence(True, 1, 1, None, "exhausted"))
    result, _ = await read(value)
    assert isinstance(result, ProviderAttemptDeferred)


async def test_numeric_repository_mismatch_stops_before_listing() -> None:
    provider = Provider([response({**repository(), "id": SCOPE.repository_id + 1})])
    result = await GitHubCiObservationWorkflows(provider).workflow_page(SCOPE, page_number=1)
    assert result == ProviderAttemptDeferred("provider_binding_mismatch")
    assert len(provider.requests) == 1


@pytest.mark.parametrize("number", [0, True, 21])
async def test_out_of_budget_page_never_gets_a_transport(number: int) -> None:
    provider = Provider([])
    with pytest.raises(ValueError):
        await GitHubCiObservationWorkflows(provider).workflow_page(SCOPE, page_number=number)
    assert not provider.installations and not provider.requests


@pytest.mark.parametrize("stage", ["repository", "workflows"])
async def test_cancellation_remains_cancellation_without_additional_provider_reads(
    stage: str,
) -> None:
    error = asyncio.CancelledError()
    provider = Provider([error] if stage == "repository" else [response(repository()), error])
    with pytest.raises(asyncio.CancelledError) as raised:
        await GitHubCiObservationWorkflows(provider).workflow_page(SCOPE, page_number=1)
    assert raised.value is error
    assert len(provider.requests) == (1 if stage == "repository" else 2)
