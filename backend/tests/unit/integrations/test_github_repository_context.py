from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportFailure,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.repository_context import (
    DEPENDENCY_GRAPH_PATH,
    GitHubRepositoryContextProvider,
    RepositoryContextLimits,
)
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.repo_context import MAX_DEPENDENCY_GRAPH_BYTES, PolicySnapshot

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
POLICY_HASH = "1" * 64
type Handler = Callable[[GitHubRequest], GitHubTransportResult | BaseException]
type ResultHandler = Callable[[GitHubRequest], GitHubTransportResult]


@dataclass
class _Transport:
    handler: Handler
    requests: list[GitHubRequest] = field(default_factory=list)

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        self.requests.append(request)
        result = self.handler(request)
        if isinstance(result, BaseException):
            raise result
        return result


@dataclass
class _Factory:
    transport: _Transport
    installation_ids: list[int] = field(default_factory=list)

    def for_installation(self, installation_id: int) -> _Transport:
        self.installation_ids.append(installation_id)
        return self.transport


def test_happy_path_binds_every_contents_read_to_exact_head() -> None:
    transport = _Transport(_valid_handler())
    factory = _Factory(transport)
    result = asyncio.run(GitHubRepositoryContextProvider(factory).load(_request(), _policy()))

    content_requests = [
        request
        for request in transport.requests
        if request.operation == "workflow_catalog.get_content"
    ]
    assert result.planning_input.full_ci_invalidating is False
    assert result.dependency_graph.fresh is True
    assert factory.installation_ids == [101]
    assert transport.requests[0].operation == "repositories.get_by_id"
    assert transport.requests[0].path == "/repositories/202"
    assert all(
        request.path.startswith("/repos/acme/repository/") for request in transport.requests[1:]
    )
    assert [request.query[0].value for request in content_requests] == [HEAD_SHA]


def test_repository_id_mismatch_stops_before_owner_name_lookup() -> None:
    transport = _Transport(lambda _: _response(_repository_identity(repository_id=203)))

    result = asyncio.run(_provider(transport).load(_request(), _policy()))

    assert result.diff.full_ci_invalidating is True
    assert result.dependency_graph.fresh is False
    assert [request.path for request in transport.requests] == ["/repositories/202"]


@pytest.mark.parametrize(
    ("owner", "name"),
    [("other", "repository"), ("acme", "renamed")],
    ids=["owner-reuse", "name-reuse"],
)
def test_stale_owner_or_name_is_rejected_before_reused_lookup(
    owner: str,
    name: str,
) -> None:
    transport = _Transport(lambda _: _response(_repository_identity(owner=owner, name=name)))

    result = asyncio.run(_provider(transport).load(_request(), _policy()))

    assert result.planning_input.full_ci_invalidating is True
    assert [request.path for request in transport.requests] == ["/repositories/202"]


def test_current_renamed_repository_uses_resolved_canonical_full_name() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "repositories.get_by_id":
            return _response(_repository_identity(name="renamed"))
        return _valid_handler()(request)

    transport = _Transport(handler)

    result = asyncio.run(_provider(transport).load(_request(repository="renamed"), _policy()))

    assert result.planning_input.full_ci_invalidating is False
    assert all(
        request.path.startswith("/repos/acme/renamed/") for request in transport.requests[1:]
    )


@pytest.mark.parametrize(
    "failure",
    [
        GitHubTransportFailure(kind="unavailable", message="provider unavailable"),
        ValueError("adapter failure"),
    ],
    ids=["provider-outcome", "adapter-exception"],
)
def test_repository_resolution_errors_map_to_full_ci_fallback(
    failure: GitHubTransportResult | BaseException,
) -> None:
    transport = _Transport(lambda _: failure)

    result = asyncio.run(_provider(transport).load(_request(), _policy()))

    assert result.planning_input.full_ci_invalidating is True
    assert [request.path for request in transport.requests] == ["/repositories/202"]


def test_repository_resolution_preserves_caller_cancellation() -> None:
    transport = _Transport(lambda _: asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_provider(transport).load(_request(), _policy()))

    assert [request.path for request in transport.requests] == ["/repositories/202"]


@pytest.mark.parametrize("prefix", ["/repos/acme/repository", "/repositories/202"])
def test_pull_request_diff_reads_a_known_next_page(prefix: str) -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.list_pull_request_files":
            page = next(item.value for item in request.query if item.name == "page")
            if page == "1":
                return _response(
                    _json([_file("src/first.py")]),
                    pagination=GitHubPaginationEvidence(
                        complete=False,
                        pages_observed=1,
                        item_count=None,
                        next_page=(
                            f"https://api.github.com{prefix}/pulls/7/files?page=2&per_page=100"
                        ),
                        termination="next_page",
                    ),
                )
            return _response(
                _json([_file("src/second.py")]),
                pagination=GitHubPaginationEvidence(
                    complete=True,
                    pages_observed=1,
                    item_count=None,
                    next_page=None,
                    termination="exhausted",
                ),
            )
        if request.operation == "diff.compare":
            return _response(_json({"files": [_file("src/first.py"), _file("src/second.py")]}))
        return _valid_handler()(request)

    transport = _Transport(handler)
    result = asyncio.run(_provider(transport).load(_request(event_name="pull_request"), _policy()))

    pages = [
        next(item.value for item in request.query if item.name == "page")
        for request in transport.requests
        if request.operation == "diff.list_pull_request_files"
    ]
    assert pages == ["1", "2"]
    assert result.diff.source.complete is True
    assert result.diff.source.page_count == 2
    assert result.diff.changed_paths == ("src/first.py", "src/second.py")
    assert [
        request.operation for request in transport.requests if request.operation.startswith("diff.")
    ] == [
        "diff.get_pull_request",
        "diff.list_pull_request_files",
        "diff.list_pull_request_files",
        "diff.get_pull_request",
        "diff.compare",
    ]
    metadata_requests = [
        request for request in transport.requests if request.operation == "diff.get_pull_request"
    ]
    assert [request.path for request in metadata_requests] == [
        "/repos/acme/repository/pulls/7",
        "/repos/acme/repository/pulls/7",
    ]


@pytest.mark.parametrize(
    ("number", "base_sha", "head_sha"),
    [
        (8, BASE_SHA, HEAD_SHA),
        (7, "c" * 40, HEAD_SHA),
        (7, BASE_SHA, "c" * 40),
    ],
    ids=["number", "base-sha", "head-sha"],
)
def test_pull_request_metadata_must_match_the_exact_request_epoch(
    number: int,
    base_sha: str,
    head_sha: str,
) -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.get_pull_request":
            return _response(
                _pull_request_metadata(number=number, base_sha=base_sha, head_sha=head_sha)
            )
        return _valid_handler()(request)

    transport = _Transport(handler)
    result = asyncio.run(_provider(transport).load(_request(event_name="pull_request"), _policy()))

    assert result.diff.source.complete is False
    assert result.diff.full_ci_invalidating is True
    assert result.diff.invalidating_reasons == ("diff_source_incomplete",)
    file_requests = [
        request
        for request in transport.requests
        if request.operation == "diff.list_pull_request_files"
    ]
    assert file_requests == []


def test_pull_request_update_during_pagination_invalidates_collected_files() -> None:
    metadata_reads = 0

    def handler(request: GitHubRequest) -> GitHubTransportResult:
        nonlocal metadata_reads
        if request.operation == "diff.get_pull_request":
            metadata_reads += 1
            head_sha = HEAD_SHA if metadata_reads == 1 else "c" * 40
            return _response(_pull_request_metadata(head_sha=head_sha))
        return _valid_handler()(request)

    transport = _Transport(handler)
    result = asyncio.run(_provider(transport).load(_request(event_name="pull_request"), _policy()))

    assert metadata_reads == 2
    assert result.diff.source.complete is False
    assert result.diff.source.page_count == 1
    assert result.diff.full_ci_invalidating is True
    assert result.diff.invalidating_reasons == ("diff_source_incomplete",)


def test_pull_request_files_must_equal_the_immutable_sha_comparison() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.compare":
            return _response(_json({"files": [_file("src/intermediate.py")]}))
        return _valid_handler()(request)

    result = asyncio.run(
        _provider(_Transport(handler)).load(
            _request(event_name="pull_request"),
            _policy(),
        )
    )

    assert result.diff.source.complete is False
    assert result.diff.full_ci_invalidating is True
    assert result.diff.invalidating_reasons == ("diff_source_incomplete",)


def test_compare_file_limit_is_ambiguous_without_a_complete_pull_request_listing() -> None:
    compared = [_file(f"src/file-{index:03}.py") for index in range(300)]

    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.compare":
            return _response(_json({"files": compared}))
        return _valid_handler()(request)

    result = asyncio.run(_provider(_Transport(handler)).load(_request(), _policy()))

    assert result.diff.source.complete is False
    assert result.diff.full_ci_invalidating is True
    assert result.diff.invalidating_reasons == ("diff_source_incomplete",)


def test_compare_below_provider_file_limit_is_complete_without_synthetic_count() -> None:
    compared = [_file(f"src/file-{index:03}.py") for index in range(299)]

    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.compare":
            return _response(_json({"files": compared}))
        return _valid_handler()(request)

    result = asyncio.run(_provider(_Transport(handler)).load(_request(), _policy()))

    assert result.diff.source.complete is True
    assert result.diff.source.file_count == 299
    assert result.diff.full_ci_invalidating is False


def test_complete_pull_request_listing_disambiguates_exact_compare_file_limit() -> None:
    compared = [_file(f"src/file-{index:03}.py") for index in range(300)]

    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.list_pull_request_files":
            page = int(next(item.value for item in request.query if item.name == "page"))
            start = (page - 1) * 100
            page_files = compared[start : start + 100]
            if page < 3:
                pagination = GitHubPaginationEvidence(
                    complete=False,
                    pages_observed=1,
                    item_count=None,
                    next_page=(
                        "https://api.github.com/repos/acme/repository/pulls/7/files?"
                        f"page={page + 1}&per_page=100"
                    ),
                    termination="next_page",
                )
            else:
                pagination = GitHubPaginationEvidence(
                    complete=True,
                    pages_observed=1,
                    item_count=None,
                    next_page=None,
                    termination="exhausted",
                )
            return _response(_json(page_files), pagination=pagination)
        if request.operation == "diff.compare":
            return _response(_json({"files": compared}))
        return _valid_handler()(request)

    result = asyncio.run(
        _provider(_Transport(handler)).load(_request(event_name="pull_request"), _policy())
    )

    assert result.diff.source.complete is True
    assert result.diff.source.file_count == 300
    assert result.diff.source.page_count == 3
    assert result.diff.full_ci_invalidating is False


@pytest.mark.parametrize("failed_metadata_read", [1, 2], ids=["before-files", "after-files"])
def test_pull_request_metadata_provider_failure_never_produces_complete_evidence(
    failed_metadata_read: int,
) -> None:
    metadata_reads = 0

    def handler(request: GitHubRequest) -> GitHubTransportResult:
        nonlocal metadata_reads
        if request.operation == "diff.get_pull_request":
            metadata_reads += 1
            if metadata_reads == failed_metadata_read:
                return GitHubTransportFailure(kind="unavailable", message="provider unavailable")
        return _valid_handler()(request)

    result = asyncio.run(
        _provider(_Transport(handler)).load(_request(event_name="pull_request"), _policy())
    )

    assert metadata_reads == failed_metadata_read
    assert result.diff.source.complete is False
    assert result.diff.full_ci_invalidating is True
    assert result.diff.invalidating_reasons == ("diff_source_incomplete",)


@pytest.mark.parametrize(
    "metadata",
    [
        b'{"number":7,"base":{"sha":"' + BASE_SHA.encode() + b'"}}',
        b'{"number":7,"number":8,"base":{"sha":"'
        + BASE_SHA.encode()
        + b'"},"head":{"sha":"'
        + HEAD_SHA.encode()
        + b'"}}',
        b'{"number":7,"base":{"sha":"'
        + BASE_SHA.encode()
        + b'"},"head":{"sha":"'
        + HEAD_SHA.encode()
        + b'"},"metadata":NaN}',
    ],
    ids=["missing-head", "duplicate-number", "non-finite-metadata"],
)
def test_malformed_pull_request_metadata_is_incomplete(metadata: bytes) -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.get_pull_request":
            return _response(metadata)
        return _valid_handler()(request)

    result = asyncio.run(
        _provider(_Transport(handler)).load(_request(event_name="pull_request"), _policy())
    )

    assert result.diff.source.complete is False
    assert result.diff.full_ci_invalidating is True
    assert result.diff.invalidating_reasons == ("diff_source_incomplete",)


def test_capped_pull_request_pagination_is_full_ci_invalidating() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.list_pull_request_files":
            return _response(
                _json([_file("src/first.py")]),
                pagination=GitHubPaginationEvidence(
                    complete=False,
                    pages_observed=1,
                    item_count=None,
                    next_page=(
                        "https://api.github.com/repos/acme/repository/pulls/7/files?"
                        "page=2&per_page=100"
                    ),
                    termination="next_page",
                ),
            )
        return _valid_handler()(request)

    transport = _Transport(handler)
    result = asyncio.run(
        _provider(transport, limits=RepositoryContextLimits(max_diff_pages=1)).load(
            _request(event_name="pull_request"),
            _policy(),
        )
    )

    assert result.diff.full_ci_invalidating is True
    assert result.diff.invalidating_reasons == ("diff_source_incomplete",)
    diff_requests = [
        request
        for request in transport.requests
        if request.operation == "diff.list_pull_request_files"
    ]
    assert len(diff_requests) == 1


def test_duplicate_provider_json_keys_invalidates_diff() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "diff.compare":
            return _response(b'{"files":[],"files":[]}')
        return _valid_handler()(request)

    result = asyncio.run(_provider(_Transport(handler)).load(_request(), _policy()))

    assert result.diff.full_ci_invalidating is True
    assert result.diff.invalidating_reasons == ("diff_source_incomplete",)


def test_committed_dependency_graph_uses_exact_retrieval_revision() -> None:
    transport = _Transport(_valid_handler())
    result = asyncio.run(_provider(transport).load(_request(), _policy()))

    assert result.dependency_graph.fresh is True
    assert result.dependency_graph.provenance.retrieved_for_sha == HEAD_SHA


def test_large_dependency_graph_keeps_its_own_provider_response_budget() -> None:
    graph = _large_graph()
    response = _contents_response(DEPENDENCY_GRAPH_PATH, graph)
    limits = RepositoryContextLimits()

    assert 524_288 < len(graph) <= MAX_DEPENDENCY_GRAPH_BYTES
    assert limits.max_response_json_bytes < len(response)
    assert len(response) <= limits.max_content_response_json_bytes

    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "workflow_catalog.get_content" and request.path.endswith(
            DEPENDENCY_GRAPH_PATH
        ):
            return _response(response)
        return _valid_handler()(request)

    result = asyncio.run(_provider(_Transport(handler)).load(_request(), _policy()))

    assert result.dependency_graph.fresh is True
    assert result.planning_input.full_ci_invalidating is False


def test_unavailable_provider_outcome_is_only_full_ci_evidence() -> None:
    transport = _Transport(
        lambda _: GitHubTransportFailure(kind="unavailable", message="provider unavailable")
    )
    result = asyncio.run(_provider(transport).load(_request(), _policy()))

    assert result.diff.full_ci_invalidating is True
    assert result.dependency_graph.fresh is False
    assert result.planning_input.full_ci_invalidating is True


def _provider(
    transport: _Transport,
    *,
    limits: RepositoryContextLimits | None = None,
) -> GitHubRepositoryContextProvider:
    return GitHubRepositoryContextProvider(
        _Factory(transport),
        limits=RepositoryContextLimits() if limits is None else limits,
    )


def _request(event_name: str = "push", *, repository: str = "repository") -> PlanRequest:
    if event_name == "pull_request":
        return PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            101,
            202,
            "acme",
            repository,
            "pull_request",
            "refs/pull/7/merge",
            BASE_SHA,
            HEAD_SHA,
            7001,
            1,
            pull_request_number=7,
            execution_sha=HEAD_SHA,
        )
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        101,
        202,
        "acme",
        repository,
        "push",
        "refs/heads/main",
        BASE_SHA,
        HEAD_SHA,
        7001,
        1,
        execution_sha=HEAD_SHA,
    )


def _policy() -> PolicySnapshot:
    return PolicySnapshot(
        epoch_id="0" * 64,
        compiled_policy_hash="0" * 64,
        policy_hash=POLICY_HASH,
        dependency_graph_source="generated",
        global_risk_paths=(),
        risk_classes=("backend",),
    )


def _valid_handler() -> ResultHandler:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if request.operation == "repositories.get_by_id":
            return _response(_repository_identity())
        if request.operation == "diff.get_pull_request":
            return _response(_pull_request_metadata())
        if request.operation == "diff.list_pull_request_files":
            return _response(_json([_file("src/module.py")]))
        if request.operation == "diff.compare":
            return _response(_json({"files": [_file("src/module.py")]}))
        if request.operation == "workflow_catalog.get_content" and request.path.endswith(
            DEPENDENCY_GRAPH_PATH
        ):
            return _response(_contents_response(DEPENDENCY_GRAPH_PATH, _graph()))
        raise AssertionError(f"unexpected GitHub request {request.operation} {request.path}")

    return handler


def _repository_identity(
    *,
    repository_id: int = 202,
    owner: str = "acme",
    name: str = "repository",
) -> bytes:
    return _json(
        {
            "id": repository_id,
            "name": name,
            "full_name": f"{owner}/{name}",
            "owner": {"login": owner},
        }
    )


def _pull_request_metadata(
    *,
    number: int = 7,
    base_sha: str = BASE_SHA,
    head_sha: str = HEAD_SHA,
) -> bytes:
    return _json(
        {
            "number": number,
            "base": {"sha": base_sha},
            "head": {"sha": head_sha},
        }
    )


def _response(
    body: bytes,
    *,
    pagination: GitHubPaginationEvidence | None = None,
) -> GitHubResponse:
    return GitHubResponse(
        status=200,
        api_version=GITHUB_API_VERSION,
        headers=(),
        body=body,
        pagination=pagination or GitHubPaginationEvidence.not_paginated(),
    )


def _contents_response(path: str, content: bytes, *, encoded: str | None = None) -> bytes:
    return _json(
        {
            "type": "file",
            "encoding": "base64",
            "path": path,
            "size": len(content),
            "content": encoded
            if encoded is not None
            else base64.b64encode(content).decode("ascii"),
        }
    )


def _graph() -> bytes:
    return _json(
        {
            "provenance": {
                "source": "generated",
                "schemaVersion": "dependency-graph/v1",
                "generator": "test-generator@v1",
            },
            "invalidatesWhenChanged": [],
            "globalRiskPaths": [],
            "nodes": [
                {
                    "path": "src/module.py",
                    "dependents": [],
                    "riskClasses": ["backend"],
                }
            ],
        }
    )


def _large_graph() -> bytes:
    nodes = [
        {
            "path": f"backend/src/package_{index:05d}/module_{index:05d}.py",
            "dependents": [],
            "riskClasses": [],
        }
        for index in range(11_600)
    ]
    nodes.append(
        {
            "path": "src/module.py",
            "dependents": [],
            "riskClasses": ["backend"],
        }
    )
    return _json(
        {
            "provenance": {
                "source": "generated",
                "schemaVersion": "dependency-graph/v1",
                "generator": "test-generator@v1",
            },
            "invalidatesWhenChanged": [],
            "globalRiskPaths": [],
            "nodes": nodes,
        }
    )


def _file(path: str) -> dict[str, object]:
    return {
        "filename": path,
        "status": "modified",
        "additions": 1,
        "deletions": 0,
        "patch": "line",
    }


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")
