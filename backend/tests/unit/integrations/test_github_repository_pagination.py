from typing import cast
from urllib.parse import urlencode

import pytest

from ci_coordinator.integrations.github._routes import repository_path_matches
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubQueryParameter,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import next_page_number

NAMED = "/repos/acme/service/actions/runs"
NUMERIC = "/repositories/202/actions/runs"
CREATED = "2026-06-12T00:00:00Z..2026-06-19T00:00:00Z"
QUERY = urlencode({"created": CREATED, "page": "2", "per_page": "100"})


@pytest.mark.parametrize("path", [NAMED, NUMERIC])
def test_exact_bound_resource_alias_preserves_every_query_operand(path: str) -> None:
    pagination = GitHubPaginationEvidence(
        False, 1, 926, f"https://api.github.com{path}?{QUERY}", "next_page"
    )
    assert (
        next_page_number(
            pagination,
            expected_path=NAMED,
            current_page=1,
            repository_id=202,
            required_query=(GitHubQueryParameter("created", CREATED),),
        )
        == 2
    )


@pytest.mark.parametrize("repository_id", [True, 202.0, "202", 0, -1, 9_007_199_254_740_992])
@pytest.mark.parametrize("path", [NAMED, NUMERIC])
def test_invalid_optional_identity_never_admits_an_alias(repository_id: object, path: str) -> None:
    assert not repository_path_matches(path, NAMED, repository_id=cast(int, repository_id))


@pytest.mark.parametrize(
    "path",
    [
        "/repositories/203/actions/runs",
        "/repositories/0202/actions/runs",
        "/repositories/202/actions/workflows",
        "/repos/other/service/actions/runs",
        "/repos/acme/other/actions/runs",
        "/repositories/202/actions/runs/",
    ],
)
def test_alias_cannot_change_repository_or_operation(path: str) -> None:
    assert not repository_path_matches(path, NAMED, repository_id=202)


def test_absent_binding_preserves_exact_paths_only() -> None:
    assert repository_path_matches(NAMED, NAMED)
    assert not repository_path_matches(NUMERIC, NAMED)
    assert not repository_path_matches(NUMERIC, "/orgs/acme/actions/runs", repository_id=202)


@pytest.mark.parametrize(
    "url",
    [
        f"http://api.github.com{NUMERIC}?{QUERY}",
        f"https://foreign.example{NUMERIC}?{QUERY}",
        f"https://api.github.com:443{NUMERIC}?{QUERY}",
        f"https://user@api.github.com{NUMERIC}?{QUERY}",
        f"https://api.github.com{NUMERIC}?{QUERY}#fragment",
        f"https://api.github.com{NUMERIC}?{QUERY}&page=2",
        f"https://api.github.com{NUMERIC}?{QUERY}&extra=1",
        f"https://api.github.com{NUMERIC}?{QUERY.replace('page=2', 'page=3')}",
        f"https://api.github.com{NUMERIC}?{QUERY.replace('per_page=100', 'per_page=99')}",
        f"https://api.github.com{NUMERIC}?{QUERY.replace('2026-06-12', '2026-06-13')}",
    ],
)
def test_numeric_alias_does_not_weaken_url_or_pagination_guards(url: str) -> None:
    pagination = GitHubPaginationEvidence(False, 1, 926, url, "next_page")
    assert (
        next_page_number(
            pagination,
            expected_path=NAMED,
            current_page=1,
            repository_id=202,
            required_query=(GitHubQueryParameter("created", CREATED),),
        )
        is None
    )
