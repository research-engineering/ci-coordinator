from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal

import pytest

from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubOutcome,
    GitHubPaginationEvidence,
    GitHubQueryParameter,
    GitHubRequest,
    GitHubSuccess,
    GitHubTransportFailure,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.diff_client import DiffClient
from ci_coordinator.integrations.github.repository_context_diff import load_diff
from ci_coordinator.repo_context.diff_model import DiffContext, RepositoryEpoch

from .test_github_repository_context import (
    BASE_SHA,
    HEAD_SHA,
    _file,
    _json,
    _pull_request_metadata,
    _response,
    _Transport,
)

type EventName = Literal["pull_request", "push", "merge_group"]
_EVENTS = ("pull_request", "push", "merge_group")
_OTHER_SHA = "c" * 40


@pytest.mark.parametrize("event", _EVENTS)
@pytest.mark.parametrize(
    "files", [[], [_file("src/module.py")]], ids=["empty-tree-diff", "changed"]
)
def test_bound_ahead_comparison_needs_no_force_push_flag(
    event: EventName, files: list[dict[str, object]]
) -> None:
    payload = _ahead()
    payload["files"] = files

    result = _load(payload, event=event)

    assert result.full_ci_invalidating is False
    assert result.source.complete is True
    assert result.changed_paths == (() if not files else ("src/module.py",))
    assert result.base_sha == BASE_SHA
    assert result.head_sha == HEAD_SHA


@pytest.mark.parametrize("event", _EVENTS)
def test_identical_requires_the_same_bound_commit_and_an_empty_comparison(event: EventName) -> None:
    result = _load(_identical(), event=event, head_sha=BASE_SHA)

    assert result.full_ci_invalidating is False
    assert result.source.complete is True
    assert result.files == ()


@pytest.mark.parametrize("event", _EVENTS)
@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_pr_three_dot_is_not_a_push_or_merge_group_tree_transition(
    event: EventName, status: str
) -> None:
    payload = _ahead()
    payload.update(
        status=status,
        behind_by=2,
        merge_base_commit={"sha": HEAD_SHA if status == "behind" else _OTHER_SHA},
    )
    if status == "behind":
        payload.update(ahead_by=0, total_commits=0, commits=[], files=[])

    result = _load(payload, event=event)

    if event == "pull_request":
        assert result.full_ci_invalidating is False
        assert result.source.complete is True
        assert result.changed_paths == (() if status == "behind" else ("src/module.py",))
    else:
        _assert_invalid(result)


@pytest.mark.parametrize("event", _EVENTS)
@pytest.mark.parametrize(
    ("key", "replacement"),
    [
        pytest.param("base_commit", {"sha": _OTHER_SHA}, id="base-substitution"),
        pytest.param("base_commit", {"sha": HEAD_SHA}, id="base-is-head"),
        pytest.param("commits", [{"sha": _OTHER_SHA}], id="head-substitution"),
        pytest.param("merge_base_commit", {"sha": _OTHER_SHA}, id="merge-base-substitution"),
        pytest.param("merge_base_commit", {"sha": HEAD_SHA}, id="merge-base-is-head"),
        pytest.param("status", "behind", id="status-behind"),
        pytest.param("status", "diverged", id="status-diverged"),
        pytest.param("status", "identical", id="status-identical"),
        pytest.param("status", "unknown", id="status-unknown"),
        pytest.param("status", [], id="status-malformed"),
        pytest.param("ahead_by", 0, id="ahead-zero"),
        pytest.param("ahead_by", 2, id="ahead-disagrees-with-total"),
        pytest.param("behind_by", 1, id="ahead-with-behind-count"),
        pytest.param("total_commits", 0, id="total-zero"),
        pytest.param("total_commits", 2, id="total-disagrees-with-ahead"),
        pytest.param("commits", [], id="missing-head"),
        pytest.param("commits", [{"sha": BASE_SHA}], id="base-in-head-commits"),
        pytest.param("commits", [{"sha": HEAD_SHA}] * 2, id="too-many-commits"),
        pytest.param("commits", [None], id="malformed-commit"),
        pytest.param("commits", [{"sha": "b" * 39}], id="malformed-head-sha"),
        pytest.param("commits", {}, id="commits-not-list"),
        pytest.param("base_commit", {"sha": "A" * 40}, id="malformed-base-sha"),
        pytest.param("merge_base_commit", {}, id="missing-merge-base-sha"),
        pytest.param("files", {}, id="files-not-list"),
    ],
)
def test_each_comparison_operand_is_independently_bound(
    event: EventName, key: str, replacement: object
) -> None:
    payload = _ahead()
    payload[key] = replacement

    _assert_invalid(_load(payload, event=event))


@pytest.mark.parametrize("event", _EVENTS)
@pytest.mark.parametrize(
    "key",
    [
        "base_commit",
        "merge_base_commit",
        "status",
        "ahead_by",
        "behind_by",
        "total_commits",
        "commits",
        "files",
    ],
)
def test_missing_comparison_metadata_never_becomes_complete(event: EventName, key: str) -> None:
    payload = _ahead()
    del payload[key]

    _assert_invalid(_load(payload, event=event))


@pytest.mark.parametrize("key", ["ahead_by", "behind_by", "total_commits"])
@pytest.mark.parametrize("value", [None, True, -1, 1.0, "1", 2**53])
def test_comparison_counts_require_nonnegative_safe_integers(key: str, value: object) -> None:
    payload = _ahead()
    payload[key] = value

    _assert_invalid(_load(payload))


@pytest.mark.parametrize("merge_base", [BASE_SHA, HEAD_SHA])
def test_pr_divergence_cannot_substitute_either_endpoint_for_its_merge_base(
    merge_base: str,
) -> None:
    payload = _ahead()
    payload.update(status="diverged", behind_by=1, merge_base_commit={"sha": merge_base})

    _assert_invalid(_load(payload, event="pull_request"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("merge_base_commit", {"sha": BASE_SHA}),
        ("behind_by", 0),
        ("status", "identical"),
        ("files", [_file("src/module.py")]),
    ],
)
def test_pr_behind_requires_consistent_empty_head_side(key: str, value: object) -> None:
    payload = _identical()
    payload.update(status="behind", behind_by=2, merge_base_commit={"sha": HEAD_SHA})
    payload[key] = value

    _assert_invalid(_load(payload, event="pull_request"))


@pytest.mark.parametrize("event", _EVENTS)
def test_identical_status_cannot_hide_distinct_requested_endpoints(event: EventName) -> None:
    _assert_invalid(_load(_identical(), event=event))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("files", [_file("src/module.py")]),
        ("behind_by", 1),
        ("merge_base_commit", {"sha": _OTHER_SHA}),
        ("commits", [{"sha": HEAD_SHA}]),
    ],
)
def test_identical_status_cannot_hide_inconsistent_metadata(key: str, value: object) -> None:
    payload = _identical()
    payload[key] = value

    _assert_invalid(_load(payload, head_sha=BASE_SHA))


@pytest.mark.parametrize("first_sha", [None, "invalid", BASE_SHA, HEAD_SHA])
def test_nonterminal_commit_metadata_is_not_silently_discarded(first_sha: object) -> None:
    payload = _ahead()
    payload.update(ahead_by=2, total_commits=2, commits=[{"sha": first_sha}, {"sha": HEAD_SHA}])

    _assert_invalid(_load(payload))


@pytest.mark.parametrize("total", [249, 250, 251])
def test_unpaged_commit_cap_does_not_change_head_binding_or_file_completeness(total: int) -> None:
    payload = _ahead()
    commits = [{"sha": f"{index:040x}"} for index in range(1, min(total, 250))]
    commits.append({"sha": HEAD_SHA})
    payload.update(ahead_by=total, total_commits=total, commits=commits)

    result = _load(payload)

    assert result.source.complete is True
    assert result.full_ci_invalidating is False
    assert result.changed_paths == ("src/module.py",)
    commits[-1] = {"sha": _OTHER_SHA}
    _assert_invalid(_load(payload))


def test_matching_ahead_and_total_counts_do_not_hide_a_short_commit_list() -> None:
    payload = _ahead()
    payload.update(ahead_by=2, total_commits=2)

    _assert_invalid(_load(payload))


def test_comparison_keeps_the_caller_file_cap() -> None:
    payload = _ahead()
    payload["files"] = [_file("src/first.py"), _file("src/second.py")]

    result = _load(payload, max_diff_files=1)

    assert result.source.complete is False
    assert result.full_ci_invalidating is True
    assert result.source.file_count == 2
    assert len(result.files) == 1
    assert result.invalidating_reasons == (
        "diff_file_count_mismatch",
        "diff_file_limit_exceeded",
        "diff_source_incomplete",
    )


@pytest.mark.parametrize("size", [300, 301])
def test_comparison_keeps_the_provider_file_cap(size: int) -> None:
    payload = _ahead()
    payload["files"] = [_file(f"src/file-{index}.py") for index in range(size)]

    _assert_invalid(_load(payload))


def test_comparison_keeps_the_response_byte_cap() -> None:
    body = _json(_ahead())

    assert _load(body, max_json_bytes=len(body)).source.complete is True
    _assert_invalid(_load(body, max_json_bytes=len(body) - 1))


@pytest.mark.parametrize("body", [b"{}", b"null", b"[]", b'{"files":[]}'])
def test_files_without_comparison_identity_are_invalid(body: bytes) -> None:
    _assert_invalid(_load(body))


def test_duplicate_comparison_metadata_is_invalid_even_with_valid_files() -> None:
    body = _json(_ahead())[:-1] + b',"status":"ahead"}'

    _assert_invalid(_load(body))


@pytest.mark.parametrize(
    "pagination",
    [
        GitHubPaginationEvidence(False, 1, None, "https://api.github.com/next", "next_page"),
        GitHubPaginationEvidence(True, 2, None, None, "exhausted"),
        GitHubPaginationEvidence(True, 1, None, None, "unknown"),
    ],
)
def test_unproved_pagination_never_admits_comparison(pagination: GitHubPaginationEvidence) -> None:
    _assert_invalid(_load(_ahead(), pagination=pagination))


@pytest.mark.parametrize("substitution", ["operation", "method", "path", "query", "body"])
def test_comparison_requires_the_exact_unpaged_request(
    substitution: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = DiffClient.compare
    request = GitHubRequest(
        operation="diff.compare",
        method="GET",
        path=f"/repos/acme/repository/compare/{BASE_SHA}...{HEAD_SHA}",
        api_version=GITHUB_API_VERSION,
    )
    replacements = {
        "operation": replace(request, operation="diff.get_pull_request"),
        "method": replace(request, method="POST"),
        "path": replace(request, path=f"/repos/acme/other/compare/{BASE_SHA}...{HEAD_SHA}"),
        "query": replace(request, query=(GitHubQueryParameter("page", "1"),)),
        "body": replace(request, body=b"{}"),
    }

    async def substituted(
        self: DiffClient, repository: GitHubRepository, base: str, head: str
    ) -> GitHubOutcome:
        outcome = await original(self, repository, base, head)
        assert isinstance(outcome, GitHubSuccess)
        return replace(outcome, request=replacements[substitution])

    monkeypatch.setattr(DiffClient, "compare", substituted)

    _assert_invalid(_load(_ahead()))


@pytest.mark.parametrize("event", _EVENTS)
def test_comparison_preserves_caller_cancellation(event: EventName) -> None:
    with pytest.raises(asyncio.CancelledError):
        _load(asyncio.CancelledError(), event=event)


@pytest.mark.parametrize(
    "failure",
    [ValueError("adapter failure"), GitHubTransportFailure("unavailable", "provider unavailable")],
)
def test_comparison_failures_never_become_empty_complete_diffs(
    failure: BaseException | GitHubTransportFailure,
) -> None:
    _assert_invalid(_load(failure))


def _ahead() -> dict[str, object]:
    return {
        "base_commit": {"sha": BASE_SHA},
        "merge_base_commit": {"sha": BASE_SHA},
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
        "total_commits": 1,
        "commits": [{"sha": HEAD_SHA}],
        "files": [_file("src/module.py")],
    }


def _identical() -> dict[str, object]:
    return {
        "base_commit": {"sha": BASE_SHA},
        "merge_base_commit": {"sha": BASE_SHA},
        "status": "identical",
        "ahead_by": 0,
        "behind_by": 0,
        "total_commits": 0,
        "commits": [],
        "files": [],
    }


def _load(
    payload: dict[str, object] | bytes | BaseException | GitHubTransportFailure,
    *,
    event: EventName = "push",
    head_sha: str = HEAD_SHA,
    max_diff_files: int = 1000,
    max_json_bytes: int = 1_048_576,
    pagination: GitHubPaginationEvidence | None = None,
) -> DiffContext:
    def handler(request: GitHubRequest) -> GitHubTransportResult | BaseException:
        if request.operation == "diff.get_pull_request":
            return _response(_pull_request_metadata(head_sha=head_sha))
        if request.operation == "diff.list_pull_request_files":
            files = (
                payload.get("files", []) if isinstance(payload, dict) else [_file("src/module.py")]
            )
            return _response(_json(files))
        assert request.operation == "diff.compare"
        assert request.path == f"/repos/acme/repository/compare/{BASE_SHA}...{head_sha}"
        assert request.query == ()
        if isinstance(payload, (BaseException, GitHubTransportFailure)):
            return payload
        return _response(
            payload if isinstance(payload, bytes) else _json(payload), pagination=pagination
        )

    return asyncio.run(
        load_diff(
            RepositoryEpoch(
                101,
                202,
                "acme",
                "repository",
                event,
                "refs/pull/7/merge" if event == "pull_request" else "refs/heads/main",
                BASE_SHA,
                head_sha,
            ),
            GitHubRepository("acme", "repository"),
            DiffClient(_Transport(handler), api_version=GITHUB_API_VERSION),
            max_diff_files=max_diff_files,
            max_diff_pages=30,
            max_response_json_bytes=max_json_bytes,
        )
    )


def _assert_invalid(result: DiffContext) -> None:
    assert result.source.complete is False
    assert result.full_ci_invalidating is True
    assert result.invalidating_reasons == ("diff_source_incomplete",)
