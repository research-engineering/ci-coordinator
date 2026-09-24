"""Fail-closed GitHub diff adaptation for repository context."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from ci_coordinator.integrations.github._routes import (
    GitHubPage,
    GitHubRepository,
    repository_path_matches,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubPaginationEvidence,
    GitHubSuccess,
)
from ci_coordinator.integrations.github.diff_client import DiffClient
from ci_coordinator.integrations.github.repository_context_decoding import (
    PullRequestSnapshot,
    parse_compared_files,
    parse_pull_request_files,
    parse_pull_request_snapshot,
)
from ci_coordinator.repo_context.diff_builder import (
    DiffBuildInput,
    DiffFileChangeInput,
    build_diff_context,
)
from ci_coordinator.repo_context.diff_model import DiffContext, DiffSource, RepositoryEpoch

_DIFF_PAGE_SIZE = 100
_COMPARE_FILE_LIMIT = 300


async def load_diff(
    epoch: RepositoryEpoch,
    repository: GitHubRepository,
    client: DiffClient,
    *,
    max_diff_files: int,
    max_diff_pages: int,
    max_response_json_bytes: int,
) -> DiffContext:
    if epoch.event_name == "pull_request":
        return await _load_pull_request_diff(
            epoch,
            repository,
            client,
            max_diff_files=max_diff_files,
            max_diff_pages=max_diff_pages,
            max_response_json_bytes=max_response_json_bytes,
        )
    return await _load_comparison_diff(
        epoch,
        repository,
        client,
        max_diff_files=max_diff_files,
        max_response_json_bytes=max_response_json_bytes,
    )


async def _load_pull_request_diff(
    epoch: RepositoryEpoch,
    repository: GitHubRepository,
    client: DiffClient,
    *,
    max_diff_files: int,
    max_diff_pages: int,
    max_response_json_bytes: int,
) -> DiffContext:
    pull_request_number = _pull_request_number(epoch)
    if pull_request_number is None:
        return invalid_diff(epoch, max_diff_files)
    expected_snapshot = PullRequestSnapshot(
        number=pull_request_number,
        base_sha=epoch.base_sha,
        head_sha=epoch.head_sha,
    )
    snapshot_before = await _load_pull_request_snapshot(
        repository,
        pull_request_number,
        client,
        max_response_json_bytes=max_response_json_bytes,
    )
    if snapshot_before != expected_snapshot:
        return invalid_diff(epoch, max_diff_files)

    files: list[DiffFileChangeInput] = []
    expected_count: int | None = None
    page_number = 1
    pages = 0
    expected_path = f"{repository.path}/pulls/{pull_request_number}/files"
    while True:
        try:
            outcome = await client.list_pull_request_files(
                repository,
                pull_request_number,
                page=GitHubPage(page_number, _DIFF_PAGE_SIZE),
            )
        except Exception:
            return invalid_diff(epoch, max_diff_files, files=tuple(files), pages=pages)
        pages += 1
        if not isinstance(outcome, (GitHubSuccess, GitHubIncomplete)):
            return invalid_diff(epoch, max_diff_files, files=tuple(files), pages=pages)
        page_files = parse_pull_request_files(
            outcome.response.body,
            max_json_bytes=max_response_json_bytes,
        )
        if page_files is None:
            return invalid_diff(epoch, max_diff_files, files=tuple(files), pages=pages)
        files.extend(page_files)
        if len(files) > max_diff_files:
            return invalid_diff(
                epoch,
                max_diff_files,
                files=tuple(files[:max_diff_files]),
                pages=pages,
                file_count=len(files),
            )
        count_valid, observed_count = _item_count(outcome.response.pagination)
        if not count_valid:
            return invalid_diff(epoch, max_diff_files, files=tuple(files), pages=pages)
        if observed_count is not None:
            if expected_count is None:
                expected_count = observed_count
            elif expected_count != observed_count:
                return invalid_diff(epoch, max_diff_files, files=tuple(files), pages=pages)
        if isinstance(outcome, GitHubSuccess):
            if not _terminal_pagination(outcome.response.pagination) or (
                expected_count is not None and expected_count != len(files)
            ):
                return invalid_diff(epoch, max_diff_files, files=tuple(files), pages=pages)
            snapshot_after = await _load_pull_request_snapshot(
                repository,
                pull_request_number,
                client,
                max_response_json_bytes=max_response_json_bytes,
            )
            if snapshot_after != expected_snapshot:
                return invalid_diff(epoch, max_diff_files, files=tuple(files), pages=pages)
            candidate = _complete_diff(epoch, max_diff_files, tuple(files), pages)
            if candidate.full_ci_invalidating:
                return candidate
            exact = await _load_comparison_diff(
                epoch,
                repository,
                client,
                max_diff_files=max_diff_files,
                max_response_json_bytes=max_response_json_bytes,
                expected_file_count=len(files),
            )
            if exact.full_ci_invalidating or exact.files != candidate.files:
                return invalid_diff(
                    epoch,
                    max_diff_files,
                    files=tuple(files),
                    pages=pages,
                )
            return candidate
        next_page = _next_page_number(
            outcome.response.pagination,
            expected_path=expected_path,
            current_page=page_number,
            repository_id=epoch.repository_id,
        )
        if next_page is None or pages >= max_diff_pages:
            return invalid_diff(epoch, max_diff_files, files=tuple(files), pages=pages)
        page_number = next_page


async def current_pull_request_matches(
    epoch: RepositoryEpoch,
    repository: GitHubRepository,
    client: DiffClient,
    *,
    max_response_json_bytes: int,
) -> bool:
    if epoch.event_name != "pull_request":
        return True
    number = _pull_request_number(epoch)
    if number is None:
        return False
    observed = await _load_pull_request_snapshot(
        repository, number, client, max_response_json_bytes=max_response_json_bytes
    )
    return observed == PullRequestSnapshot(number, epoch.base_sha, epoch.head_sha)


def _pull_request_number(epoch: RepositoryEpoch) -> int | None:
    match = re.fullmatch(r"refs/pull/([1-9][0-9]{0,18})/merge", epoch.ref)
    return None if match is None else int(match[1])


async def _load_pull_request_snapshot(
    repository: GitHubRepository,
    pull_request_number: int,
    client: DiffClient,
    *,
    max_response_json_bytes: int,
) -> PullRequestSnapshot | None:
    try:
        outcome = await client.get_pull_request(repository, pull_request_number)
    except Exception:
        return None
    expected_path = f"{repository.path}/pulls/{pull_request_number}"
    if (
        not isinstance(outcome, GitHubSuccess)
        or outcome.request.operation != "diff.get_pull_request"
        or outcome.request.path != expected_path
        or outcome.request.query
        or outcome.response.pagination != GitHubPaginationEvidence.not_paginated()
    ):
        return None
    return parse_pull_request_snapshot(
        outcome.response.body,
        max_json_bytes=max_response_json_bytes,
    )


async def _load_comparison_diff(
    epoch: RepositoryEpoch,
    repository: GitHubRepository,
    client: DiffClient,
    *,
    max_diff_files: int,
    max_response_json_bytes: int,
    expected_file_count: int | None = None,
) -> DiffContext:
    try:
        outcome = await client.compare(repository, epoch.base_sha, epoch.head_sha)
    except Exception:
        return invalid_diff(epoch, max_diff_files)
    expected_path = f"{repository.path}/compare/{epoch.base_sha}...{epoch.head_sha}"
    if (
        not isinstance(outcome, GitHubSuccess)
        or outcome.request.operation != "diff.compare"
        or outcome.request.method != "GET"
        or outcome.request.path != expected_path
        or outcome.request.query
        or outcome.request.body is not None
        or not _terminal_pagination(outcome.response.pagination)
    ):
        return invalid_diff(epoch, max_diff_files)
    compared = parse_compared_files(
        outcome.response.body,
        epoch=epoch,
        max_json_bytes=max_response_json_bytes,
    )
    if compared is None or len(compared) > _COMPARE_FILE_LIMIT:
        return invalid_diff(epoch, max_diff_files)
    if len(compared) == _COMPARE_FILE_LIMIT and expected_file_count != _COMPARE_FILE_LIMIT:
        return invalid_diff(
            epoch,
            max_diff_files,
            files=compared[:max_diff_files],
            pages=1,
            file_count=len(compared),
        )
    if len(compared) > max_diff_files:
        return invalid_diff(
            epoch,
            max_diff_files,
            files=compared[:max_diff_files],
            pages=1,
            file_count=len(compared),
        )
    return _complete_diff(epoch, max_diff_files, compared, 1)


def invalid_diff(
    epoch: RepositoryEpoch,
    max_diff_files: int,
    *,
    files: tuple[DiffFileChangeInput, ...] = (),
    pages: int = 0,
    file_count: int | None = None,
) -> DiffContext:
    return build_diff_context(
        DiffBuildInput(
            base_sha=epoch.base_sha,
            head_sha=epoch.head_sha,
            files=files,
            source=DiffSource(
                provider="github",
                complete=False,
                page_count=pages,
                file_count=len(files) if file_count is None else file_count,
                max_files=max_diff_files,
            ),
        )
    )


def _complete_diff(
    epoch: RepositoryEpoch,
    max_diff_files: int,
    files: tuple[DiffFileChangeInput, ...],
    pages: int,
) -> DiffContext:
    return build_diff_context(
        DiffBuildInput(
            base_sha=epoch.base_sha,
            head_sha=epoch.head_sha,
            files=files,
            source=DiffSource(
                provider="github",
                complete=True,
                page_count=pages,
                file_count=len(files),
                max_files=max_diff_files,
            ),
        )
    )


def _item_count(pagination: GitHubPaginationEvidence) -> tuple[bool, int | None]:
    value = pagination.item_count
    if value is None:
        return True, None
    if type(value) is not int or value < 0:
        return False, None
    return True, value


def _terminal_pagination(pagination: GitHubPaginationEvidence) -> bool:
    return (
        type(pagination.pages_observed) is int
        and pagination.pages_observed == 1
        and pagination.next_page is None
        and pagination.termination in {"not_paginated", "exhausted"}
    )


def _next_page_number(
    pagination: GitHubPaginationEvidence,
    *,
    expected_path: str,
    current_page: int,
    repository_id: int | None = None,
) -> int | None:
    next_page = pagination.next_page
    if (
        type(pagination.pages_observed) is not int
        or pagination.pages_observed != 1
        or pagination.termination != "next_page"
        or type(next_page) is not str
    ):
        return None
    try:
        target = urlsplit(next_page)
        parameters = parse_qsl(target.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if (
        not repository_path_matches(target.path, expected_path, repository_id=repository_id)
        or target.fragment
        or target.username is not None
        or target.password is not None
        or len(parameters) != 2
    ):
        return None
    query = dict(parameters)
    if len(query) != 2 or query.get("per_page") != str(_DIFF_PAGE_SIZE):
        return None
    raw_page = query.get("page")
    if raw_page is None or not raw_page.isdecimal() or raw_page != str(current_page + 1):
        return None
    return current_page + 1
