"""Bounded pagination evidence for GitHub Actions reconciliation."""

from __future__ import annotations

from typing import Final
from urllib.parse import parse_qsl, urlsplit

from ci_coordinator.integrations.github._routes import repository_path_matches
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_BASE_URL
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubQueryParameter,
)

JOBS_PAGE_SIZE: Final = 100
_MAX_NEXT_PAGE_URL_BYTES: Final = 2_048


def terminal_pagination(pagination: GitHubPaginationEvidence) -> bool:
    """Return whether evidence proves a single terminal provider page."""
    return (
        pagination.complete is True
        and type(pagination.pages_observed) is int
        and pagination.pages_observed == 1
        and pagination.next_page is None
        and pagination.termination in {"not_paginated", "exhausted"}
    )


def pagination_count_matches(
    pagination: GitHubPaginationEvidence,
    expected_total: int,
) -> bool:
    """Validate an optional provider count against the decoded total."""
    item_count = pagination.item_count
    return item_count is None or (type(item_count) is int and item_count == expected_total)


def next_page_number(
    pagination: GitHubPaginationEvidence,
    *,
    expected_path: str,
    current_page: int,
    page_size: int = JOBS_PAGE_SIZE,
    required_query: tuple[GitHubQueryParameter, ...] = (),
    repository_id: int | None = None,
) -> int | None:
    """Return the exact sequential page only when all Link evidence is trustworthy."""
    next_page = pagination.next_page
    if (
        pagination.complete is not False
        or type(pagination.pages_observed) is not int
        or pagination.pages_observed != 1
        or pagination.termination != "next_page"
        or type(next_page) is not str
        or not next_page.isascii()
        or len(next_page.encode("ascii")) > _MAX_NEXT_PAGE_URL_BYTES
    ):
        return None

    if (
        type(required_query) is not tuple
        or any(type(item) is not GitHubQueryParameter for item in required_query)
        or len({item.name for item in required_query}) != len(required_query)
        or {"page", "per_page"}.intersection(item.name for item in required_query)
    ):
        return None

    try:
        target = urlsplit(next_page)
        api_origin = urlsplit(GITHUB_API_BASE_URL)
        parameters = parse_qsl(
            target.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2 + len(required_query),
        )
        target_port = target.port
    except (UnicodeError, ValueError):
        return None

    if (
        target.scheme != api_origin.scheme
        or target.hostname != api_origin.hostname
        or target_port is not None
        or target.username is not None
        or target.password is not None
        or not repository_path_matches(target.path, expected_path, repository_id=repository_id)
        or target.fragment
        or len(parameters) != 2 + len(required_query)
    ):
        return None

    query = dict(parameters)
    expected_page = current_page + 1
    expected_query = {
        **{item.name: item.value for item in required_query},
        "per_page": str(page_size),
        "page": str(expected_page),
    }
    if len(query) != len(expected_query) or query != expected_query:
        return None
    return expected_page
