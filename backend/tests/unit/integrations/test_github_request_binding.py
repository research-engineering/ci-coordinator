from dataclasses import replace

import pytest

from ci_coordinator.integrations.github.contracts import GitHubQueryParameter, GitHubRequest
from ci_coordinator.integrations.github.request_admission import get_request_matches

_QUERY = (GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100"))
_REQUEST = GitHubRequest("operation", "GET", "/repositories/1", "2026-03-10", _QUERY)


def test_get_binding_accepts_all_expected_coordinates() -> None:
    assert get_request_matches(
        _REQUEST,
        operation="operation",
        path="/repositories/1",
        api_version="2026-03-10",
        query=_QUERY,
    )


@pytest.mark.parametrize(
    "candidate",
    [
        replace(_REQUEST, operation="other"),
        replace(_REQUEST, method="POST"),
        replace(_REQUEST, path="/repositories/2"),
        replace(_REQUEST, api_version=None),
        replace(_REQUEST, query=tuple(reversed(_QUERY))),
        replace(_REQUEST, body=b""),
    ],
)
def test_get_binding_rejects_each_independent_substitution(candidate: GitHubRequest) -> None:
    assert not get_request_matches(
        candidate,
        operation="operation",
        path="/repositories/1",
        api_version="2026-03-10",
        query=_QUERY,
    )
