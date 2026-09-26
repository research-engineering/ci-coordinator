from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github import workflow_inventory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubRequest,
    GitHubResponse,
)
from ci_coordinator.integrations.github.workflow_catalog_client import WorkflowCatalogClient
from ci_coordinator.integrations.github.workflow_inventory import (
    DEFAULT_WORKFLOW_INVENTORY_LIMITS,
    GitHubWorkflowInventoryLoader,
    WorkflowInventoryLimits,
)
from ci_coordinator.repo_context import (
    DefaultBranchWorkflow,
    ProviderWorkflowInventory,
    ProviderWorkflowInventoryEvidence,
    parse_workflow_capability,
)
from ci_coordinator.workflow_authority import WorkflowAuthorityRepository

SCOPE = RepositoryScope(11, 22)
REVISION = "a" * 40
WORKFLOW_PATH = ".github/workflows/ci.yml"
WORKFLOW = b"name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
PLATFORM_PATHS = (
    "dynamic/dependabot/update-graph",
    "dynamic/github-code-scanning/codeql",
)
# Synthetic population: ten source rows and two non-authorizing platform rows.
_SYNTHETIC_WORKFLOWS = (
    (101, ".github/workflows/workflow-01.yml"),
    (102, ".github/workflows/workflow-02.yml"),
    (103, ".github/workflows/workflow-03.yml"),
    (104, ".github/workflows/workflow-04.yml"),
    (105, ".github/workflows/workflow-05.yml"),
    (106, ".github/workflows/workflow-06.yml"),
    (107, ".github/workflows/workflow-07.yml"),
    (108, ".github/workflows/workflow-08.yml"),
    (109, ".github/workflows/workflow-09.yml"),
    (110, ".github/workflows/workflow-10.yml"),
    (201, PLATFORM_PATHS[0]),
    (202, PLATFORM_PATHS[1]),
)


@dataclass
class _Transport:
    requests: list[GitHubRequest] = field(default_factory=list)
    next_path: str | None = None
    pages: tuple[GitHubResponse, ...] | None = None
    content_path: str = WORKFLOW_PATH
    cancel_operation: str | None = None

    async def send(self, request: GitHubRequest) -> GitHubResponse:
        self.requests.append(request)
        if request.operation == self.cancel_operation:
            raise asyncio.CancelledError
        if request.operation == "workflow_catalog.list_workflows":
            if self.pages is not None:
                page = next(item.value for item in request.query if item.name == "page")
                return self.pages[int(page) - 1]
            if self.next_path is not None:
                page = next(item.value for item in request.query if item.name == "page")
                if page == "1":
                    return replace(
                        _response(
                            {
                                "total_count": 2,
                                "workflows": [
                                    {"id": 101, "path": WORKFLOW_PATH, "state": "active"}
                                ],
                            }
                        ),
                        pagination=GitHubPaginationEvidence(
                            False,
                            1,
                            2,
                            f"https://api.github.com{self.next_path}?page=2&per_page=100",
                            "next_page",
                        ),
                    )
                assert page == "2", f"unexpected workflow page: {page}"
                return _response(
                    {
                        "total_count": 2,
                        "workflows": [
                            {"id": 102, "path": ".github/workflows/extra.yml", "state": "active"}
                        ],
                    },
                    item_count=2,
                )
            return _response(
                {
                    "total_count": 1,
                    "workflows": [{"id": 101, "path": WORKFLOW_PATH, "state": "active"}],
                },
                item_count=1,
            )
        if request.operation == "workflow_catalog.get_content":
            return _response(
                {
                    "type": "file",
                    "path": self.content_path,
                    "encoding": "base64",
                    "size": len(WORKFLOW),
                    "content": base64.b64encode(WORKFLOW).decode("ascii"),
                }
            )
        raise AssertionError(f"unexpected request: {request.operation}")


@pytest.mark.parametrize(
    "next_path",
    [None, "/repos/example/consumer/actions/workflows", "/repositories/22/actions/workflows"],
)
def test_inventory_loader_retains_the_exact_requested_repository_subject(
    next_path: str | None,
) -> None:
    transport = _Transport(next_path=next_path)
    loader = GitHubWorkflowInventoryLoader(
        WorkflowCatalogClient(transport, api_version=GITHUB_API_VERSION)
    )
    repository = WorkflowAuthorityRepository(SCOPE, "example", "consumer", "master")

    result = asyncio.run(
        loader.load(repository, revision_sha=REVISION, required_paths=(WORKFLOW_PATH,))
    )

    assert result is not None, "workflow inventory must be complete"
    assert (result.scope, result.owner, result.name, result.default_branch) == (
        SCOPE,
        "example",
        "consumer",
        "master",
    )
    assert result.inventory.revision_sha == REVISION
    assert all(
        request.path.startswith("/repos/example/consumer/") for request in transport.requests
    )
    listing = [
        request
        for request in transport.requests
        if request.operation == "workflow_catalog.list_workflows"
    ]
    assert [
        (request.path, tuple((item.name, item.value) for item in request.query))
        for request in listing
    ] == [
        ("/repos/example/consumer/actions/workflows", (("page", str(page)), ("per_page", "100")))
        for page in ([1] if next_path is None else [1, 2])
    ]


def test_inventory_pagination_oracle_rejects_a_skipped_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def skip_page(_pagination: GitHubPaginationEvidence, **_arguments: object) -> int:
        return 3

    path = "/repositories/22/actions/workflows"
    test_inventory_loader_retains_the_exact_requested_repository_subject(path)
    with monkeypatch.context() as patch:
        patch.setattr(workflow_inventory, "_next_page_number", skip_page)
        with pytest.raises(AssertionError, match="workflow inventory must be complete"):
            test_inventory_loader_retains_the_exact_requested_repository_subject(path)
    test_inventory_loader_retains_the_exact_requested_repository_subject(path)


@pytest.mark.parametrize("id_offset", [0, 1_000_000])
@pytest.mark.parametrize("partition", ["single", "platform-first", "platform-last", "empty-middle"])
def test_synthetic_mixed_population_preserves_source_inventory_and_hashes(
    id_offset: int, partition: str
) -> None:
    rows = [_row(identity + id_offset, path) for identity, path in _SYNTHETIC_WORKFLOWS]
    source_rows, platform_rows = rows[:10], rows[10:]
    content_path = ".github/workflows/workflow-04.yml"
    pages: tuple[GitHubResponse, ...] = (_page(rows, total=12),)
    if partition == "platform-first":
        pages = (_page(platform_rows, total=12, next_page=2), _page(source_rows, total=12))
    elif partition == "platform-last":
        pages = (_page(source_rows, total=12, next_page=2), _page(platform_rows, total=12))
    elif partition == "empty-middle":
        pages = (
            _page(platform_rows, total=12, next_page=2),
            _page([], total=12, next_page=3),
            _page(source_rows, total=12),
        )
    ordinary, _ = _load_catalogue((_page(source_rows, total=10),), content_path=content_path)
    mixed, transport = _load_catalogue(pages, content_path=content_path)
    capability = parse_workflow_capability(WORKFLOW, path=content_path, revision_sha=REVISION)
    assert capability is not None
    expected = ProviderWorkflowInventoryEvidence(
        SCOPE,
        "example",
        "consumer",
        "master",
        GITHUB_API_VERSION,
        ProviderWorkflowInventory(
            REVISION,
            tuple(
                DefaultBranchWorkflow(identity + id_offset, path, True)
                for identity, path in _SYNTHETIC_WORKFLOWS[:10]
            ),
            (capability,),
        ),
    )
    assert mixed == ordinary == expected
    assert mixed is not None
    assert mixed.evidence_digest == expected.evidence_digest
    assert mixed.stable_authority_digest == expected.stable_authority_digest
    assert [request.operation for request in transport.requests] == [
        *(["workflow_catalog.list_workflows"] * len(pages)),
        "workflow_catalog.get_content",
    ]
    content_request = transport.requests[-1]
    assert content_request.path == f"/repos/example/consumer/contents/{content_path}"
    assert [(item.name, item.value) for item in content_request.query] == [("ref", REVISION)]


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize(
    "duplicate",
    [
        (101, ".github/workflows/extra.yml"),
        (102, WORKFLOW_PATH),
        (201, PLATFORM_PATHS[1]),
        (202, PLATFORM_PATHS[0]),
        (101, PLATFORM_PATHS[1]),
    ],
    ids=["source-id", "source-path", "platform-id", "platform-path", "cross-kind-id"],
)
def test_duplicates_cover_the_complete_provider_population(
    duplicate: tuple[int, str], split: bool
) -> None:
    first = [_row(101, WORKFLOW_PATH), _row(201, PLATFORM_PATHS[0])]
    last = [_row(*duplicate)]
    pages = (
        (_page(first, total=3, next_page=2), _page(last, total=3))
        if split
        else (_page([*first, *last], total=3),)
    )
    result, transport = _load_catalogue(pages)
    assert result is None
    assert len(transport.requests) == len(pages)
    assert all(
        request.operation == "workflow_catalog.list_workflows" for request in transport.requests
    )


@pytest.mark.parametrize(
    "path",
    [
        "dynamic/dependabot/dependabot-updates",
        "dynamic/pages/pages-build-deployment",
        "dynamic/unknown/workflow",
        "dynamic/dependabot/update-graph/extra",
        "dynamic/dependabot/../update-graph",
        "dynamic/dependabot/%75pdate-graph",
        "Dynamic/dependabot/update-graph",
        ".github/workflows/not-yaml.txt",
        ".github/workflows/../outside.yml",
        "outside.yml",
    ],
)
def test_unknown_paths_are_not_silently_skipped(path: str) -> None:
    result, transport = _load_catalogue(
        (_page([_row(101, WORKFLOW_PATH), _row(201, path)], total=2),)
    )
    assert result is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", True),
        ("id", 0),
        ("id", -1),
        ("id", 1.0),
        ("id", "1"),
        ("id", 9_007_199_254_740_992),
        ("path", None),
        ("path", []),
        ("path", "\ud800"),
        ("state", None),
        ("state", False),
        ("state", ""),
        ("state", "x" * 65),
        ("state", "\ud800"),
    ],
)
def test_platform_rows_must_pass_the_same_primitive_guards(field: str, value: object) -> None:
    platform = {**_row(201, PLATFORM_PATHS[0]), field: value}
    result, transport = _load_catalogue((_page([_row(101, WORKFLOW_PATH), platform], total=2),))
    assert result is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize("row", [None, [], 1, {}, {"id": 201}])
def test_malformed_rows_are_not_missing_platform_workflows(row: object) -> None:
    result, transport = _load_catalogue((_page([_row(101, WORKFLOW_PATH), row], total=2),))
    assert result is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize("total,item_count", [(2, 2), (4, 4), (3, 2), (2001, 2001)])
def test_platform_rows_do_not_rewrite_provider_totals(total: int, item_count: int) -> None:
    rows = [_row(101, WORKFLOW_PATH), _row(201, PLATFORM_PATHS[0]), _row(202, PLATFORM_PATHS[1])]
    page = _page(rows, total=total)
    page = replace(page, pagination=replace(page.pagination, item_count=item_count))
    result, transport = _load_catalogue((page,))
    assert result is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"total_count": True, "workflows": []},
        {"total_count": -1, "workflows": []},
        {"total_count": 1.0, "workflows": []},
        {"total_count": 0, "workflows": {}},
    ],
)
def test_malformed_page_envelopes_remain_absent(value: object) -> None:
    result, transport = _load_catalogue((_response(value),))
    assert result is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize("change", ["total", "api-version", "foreign-link", "skipped-link"])
def test_mixed_pagination_preserves_totals_version_and_next_page_identity(change: str) -> None:
    first = _page([_row(201, PLATFORM_PATHS[0])], total=2, next_page=2)
    last = _page([_row(101, WORKFLOW_PATH)], total=2)
    expected_requests = 1
    if change == "total":
        last = _page([_row(101, WORKFLOW_PATH)], total=3)
        expected_requests = 2
    elif change == "api-version":
        first = replace(first, api_version="2022-11-28")
    else:
        next_link = (
            "https://api.github.com/repositories/23/actions/workflows?page=2&per_page=100"
            if change == "foreign-link"
            else "https://api.github.com/repositories/22/actions/workflows?page=3&per_page=100"
        )
        first = replace(first, pagination=replace(first.pagination, next_page=next_link))
    result, transport = _load_catalogue((first, last))
    assert result is None
    assert len(transport.requests) == expected_requests
    assert all(
        request.operation == "workflow_catalog.list_workflows" for request in transport.requests
    )


@pytest.mark.parametrize("row_limit,admitted", [(2, False), (3, True)])
def test_platform_rows_consume_the_workflow_budget(row_limit: int, admitted: bool) -> None:
    rows = [_row(101, WORKFLOW_PATH), _row(201, PLATFORM_PATHS[0]), _row(202, PLATFORM_PATHS[1])]
    result, _ = _load_catalogue(
        (_page(rows, total=3),), limits=WorkflowInventoryLimits(max_workflows=row_limit)
    )
    assert (result is not None) is admitted


@pytest.mark.parametrize(
    "page_limit,byte_delta,admitted", [(3, 0, True), (2, 0, False), (3, -1, False)]
)
def test_platform_only_and_empty_pages_consume_page_and_byte_budgets(
    page_limit: int, byte_delta: int, admitted: bool
) -> None:
    pages = (
        _page([_row(201, PLATFORM_PATHS[0]), _row(202, PLATFORM_PATHS[1])], total=3, next_page=2),
        _page([], total=3, next_page=3),
        _page([_row(101, WORKFLOW_PATH)], total=3),
    )
    result, transport = _load_catalogue(
        pages,
        limits=WorkflowInventoryLimits(
            max_pages=page_limit,
            max_total_index_bytes=sum(len(page.body) for page in pages) + byte_delta,
        ),
    )
    assert (result is not None) is admitted
    assert len(transport.requests) == (4 if admitted else page_limit)


@pytest.mark.parametrize("count,admitted", [(100, True), (101, False)])
def test_raw_page_size_is_not_the_projected_source_count(count: int, admitted: bool) -> None:
    rows = [
        _row(101, WORKFLOW_PATH),
        _row(201, PLATFORM_PATHS[0]),
        _row(202, PLATFORM_PATHS[1]),
        *(_row(1000 + index, f".github/workflows/extra-{index}.yml") for index in range(count - 3)),
    ]
    result, _ = _load_catalogue((_page(rows, total=count),))
    assert (result is not None) is admitted


@pytest.mark.parametrize("source_state", [None, "disabled_manually", "future_provider_state"])
def test_platform_active_state_cannot_supply_a_required_source(source_state: str | None) -> None:
    rows = [_row(201, PLATFORM_PATHS[0]), _row(202, PLATFORM_PATHS[1])]
    if source_state is not None:
        rows.append(_row(101, WORKFLOW_PATH, source_state))
    result, transport = _load_catalogue((_page(rows, total=len(rows)),))
    assert result is None
    assert len(transport.requests) == 1


def test_nonrequired_source_rows_and_future_platform_states_are_retained_correctly() -> None:
    rows = [
        _row(101, WORKFLOW_PATH),
        _row(102, ".github/workflows/extra.yml", "future_provider_state"),
        _row(201, PLATFORM_PATHS[0], "future_provider_state"),
        _row(202, PLATFORM_PATHS[1], "disabled_manually"),
    ]
    result, _ = _load_catalogue((_page(rows, total=4),))
    assert result is not None
    assert result.inventory.default_branch_workflows == (
        DefaultBranchWorkflow(101, WORKFLOW_PATH, True),
        DefaultBranchWorkflow(102, ".github/workflows/extra.yml", False),
    )


@pytest.mark.parametrize(
    "operation", ["workflow_catalog.list_workflows", "workflow_catalog.get_content"]
)
def test_mixed_inventory_preserves_cancellation(operation: str) -> None:
    rows = [_row(101, WORKFLOW_PATH), _row(201, PLATFORM_PATHS[0])]
    transport = _Transport(pages=(_page(rows, total=2),), cancel_operation=operation)
    loader = GitHubWorkflowInventoryLoader(
        WorkflowCatalogClient(transport, api_version=GITHUB_API_VERSION)
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            loader.load(
                WorkflowAuthorityRepository(SCOPE, "example", "consumer", "master"),
                revision_sha=REVISION,
                required_paths=(WORKFLOW_PATH,),
            )
        )
    assert len(transport.requests) == (1 if operation.endswith("list_workflows") else 2)


@pytest.mark.parametrize("path", PLATFORM_PATHS)
def test_platform_path_is_never_a_required_execution_path(path: str) -> None:
    transport = _Transport()
    loader = GitHubWorkflowInventoryLoader(
        WorkflowCatalogClient(transport, api_version=GITHUB_API_VERSION)
    )
    with pytest.raises(ValueError, match="required workflow paths"):
        asyncio.run(
            loader.load(
                WorkflowAuthorityRepository(SCOPE, "example", "consumer", "master"),
                revision_sha=REVISION,
                required_paths=(path,),
            )
        )
    assert not transport.requests


def _row(workflow_id: int, path: str, state: str = "active") -> dict[str, object]:
    return {"id": workflow_id, "path": path, "state": state}


def _page(rows: Sequence[object], *, total: int, next_page: int | None = None) -> GitHubResponse:
    page = _response({"total_count": total, "workflows": list(rows)}, item_count=total)
    if next_page is None:
        return page
    return replace(
        page,
        pagination=GitHubPaginationEvidence(
            False,
            1,
            total,
            f"https://api.github.com/repositories/22/actions/workflows?page={next_page}&per_page=100",
            "next_page",
        ),
    )


def _load_catalogue(
    pages: tuple[GitHubResponse, ...],
    *,
    content_path: str = WORKFLOW_PATH,
    limits: WorkflowInventoryLimits = DEFAULT_WORKFLOW_INVENTORY_LIMITS,
) -> tuple[ProviderWorkflowInventoryEvidence | None, _Transport]:
    transport = _Transport(pages=pages, content_path=content_path)
    result = asyncio.run(
        GitHubWorkflowInventoryLoader(
            WorkflowCatalogClient(transport, api_version=GITHUB_API_VERSION), limits=limits
        ).load(
            WorkflowAuthorityRepository(SCOPE, "example", "consumer", "master"),
            revision_sha=REVISION,
            required_paths=(content_path,),
        )
    )
    return result, transport


def _response(value: object, *, item_count: int | None = None) -> GitHubResponse:
    return GitHubResponse(
        status=200,
        api_version=GITHUB_API_VERSION,
        headers=(),
        body=json.dumps(value, separators=(",", ":")).encode(),
        pagination=GitHubPaginationEvidence(
            complete=True,
            pages_observed=1,
            item_count=item_count,
            next_page=None,
            termination="not_paginated",
        ),
    )
