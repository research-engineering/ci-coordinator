from dataclasses import replace

import pytest

from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_history_provider import _bound_not_found
from ci_coordinator.integrations.github.contracts import (
    GitHubFailure,
    GitHubPaginationEvidence,
    GitHubQueryParameter,
    GitHubRequest,
    GitHubUnavailable,
)

from ._economics_source_support import PATH, response

_REQUEST = GitHubRequest("actions.get_workflow_run_attempt", "GET", PATH, GITHUB_API_VERSION)


@pytest.mark.parametrize(
    "operand",
    [
        "none",
        "method",
        "operation",
        "path",
        "query",
        "body",
        "request_api",
        "response_api",
        "status",
        "failure_kind",
        "missing_response",
        "oversized_body",
        "pagination",
        "page_count",
    ],
)
def test_missing_attempt_receipt_depends_on_every_transport_binding_operand(operand: str) -> None:
    request = _REQUEST
    result = replace(response({"message": "Not Found"}), status=404)
    match operand:
        case "method":
            request = replace(request, method="POST")
        case "operation":
            request = replace(request, operation="repositories.get_by_id")
        case "path":
            request = replace(request, path=PATH.replace("attempts/2", "attempts/1"))
        case "query":
            request = replace(request, query=(GitHubQueryParameter("page", "1"),))
        case "body":
            request = replace(request, body=b"{}")
        case "request_api":
            request = replace(request, api_version="2022-11-28")
        case "response_api":
            result = replace(result, api_version="2022-11-28")
        case "status":
            result = replace(result, status=403)
        case "oversized_body":
            result = replace(result, body=b"x" * 1_048_577)
        case "pagination":
            result = replace(
                result, pagination=GitHubPaginationEvidence(False, 1, None, None, "unknown")
            )
        case "page_count":
            result = replace(result, pagination=replace(result.pagination, pages_observed=2))
    failure = GitHubFailure(
        "forbidden" if operand == "failure_kind" else "not_found",
        request,
        "bounded missing-attempt observation",
        None if operand == "missing_response" else result,
    )
    assert _bound_not_found(
        GitHubUnavailable(failure),
        operation=_REQUEST.operation,
        path=PATH,
        api_version=GITHUB_API_VERSION,
    ) is (operand == "none")
