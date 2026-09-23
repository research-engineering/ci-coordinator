from __future__ import annotations

import asyncio
import base64
import json
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
from ci_coordinator.integrations.github.workflow_inventory import GitHubWorkflowInventoryLoader
from ci_coordinator.workflow_authority import WorkflowAuthorityRepository

SCOPE = RepositoryScope(11, 22)
REVISION = "a" * 40
WORKFLOW_PATH = ".github/workflows/ci.yml"
WORKFLOW = b"name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"


@dataclass
class _Transport:
    requests: list[GitHubRequest] = field(default_factory=list)
    next_path: str | None = None

    async def send(self, request: GitHubRequest) -> GitHubResponse:
        self.requests.append(request)
        if request.operation == "workflow_catalog.list_workflows":
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
                    "path": WORKFLOW_PATH,
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
