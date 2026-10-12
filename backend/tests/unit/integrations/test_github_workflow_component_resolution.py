from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine, Iterable

import httpx2 as httpx
import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.workflow_component_resolution import (
    COMPONENT_INVENTORY_MAXIMUM_PAGES,
    COMPONENT_INVENTORY_PAGE_SIZE,
    GitHubComponentRevisionResolver,
)
from ci_coordinator.workflow_discovery.component_passport import (
    ComponentLocationOutcome,
    ComponentReadFailure,
    ComponentRepositoryLocation,
    ComponentRepositoryScope,
    ComponentRevisionObserved,
    ComponentRevisionOutcome,
    ComponentWorkflowBlob,
)

from ._github_app_transport_support import _factory, _private_key, _token_response

CALLEE = RepositoryScope(77, 601)
REPOSITORY_PATH = "/repos/example-org/shared"
COMMIT_SHA = "1" * 40
TAG_OBJECT_SHA = "2" * 40
ROOT_TREE_SHA = "3" * 40
GITHUB_TREE_SHA = "4" * 40
WORKFLOWS_TREE_SHA = "5" * 40
BUILD_BLOB_SHA = "6" * 40


def test_tag_resolution_reads_only_the_authorized_callee_repository() -> None:
    requests: list[str] = []
    grants: list[object] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            grants.append(json.loads(request.content))
        return _callee_response(request)

    result = asyncio.run(_read(handler, ref="v1", workflow_files=("build.yml", "absent.yml")))

    assert result == ComponentRevisionObserved(
        COMMIT_SHA,
        (
            ComponentWorkflowBlob("absent.yml", None),
            ComponentWorkflowBlob("build.yml", BUILD_BLOB_SHA),
        ),
    )
    assert grants == [
        {"repository_ids": [601], "permissions": {"metadata": "read"}},
        {"repository_ids": [601], "permissions": {"contents": "read"}},
    ]
    assert [path for path in requests if not path.endswith("/access_tokens")] == [
        "/repositories/601",
        f"{REPOSITORY_PATH}/git/ref/tags/v1",
        f"{REPOSITORY_PATH}/git/commits/{COMMIT_SHA}",
        f"{REPOSITORY_PATH}/git/trees/{ROOT_TREE_SHA}",
        f"{REPOSITORY_PATH}/git/trees/{GITHUB_TREE_SHA}",
        f"{REPOSITORY_PATH}/git/trees/{WORKFLOWS_TREE_SHA}",
    ]


def test_branch_is_read_only_after_the_tag_is_absent() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/git/ref/tags/main"):
            return _not_found()
        return _callee_response(request)

    result = asyncio.run(_read(handler, ref="main", workflow_files=()))

    assert result == ComponentRevisionObserved(COMMIT_SHA, ())
    assert f"{REPOSITORY_PATH}/git/ref/heads/main" in requests
    assert not any("/git/trees/" in path for path in requests)


@pytest.mark.parametrize(
    ("tag", "branch", "expected"),
    [
        ("annotated", "absent", "annotated_tag_not_dereferenced"),
        ("absent", "absent", "ref_not_found"),
        ("rate_limited", "absent", "rate_limited"),
    ],
)
def test_unresolvable_refs_are_typed_failures(tag: str, branch: str, expected: str) -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        path = request.url.path
        if path.endswith("/git/ref/tags/v1"):
            if tag == "annotated":
                return _json_response(
                    {"ref": "refs/tags/v1", "object": {"type": "tag", "sha": TAG_OBJECT_SHA}}
                )
            if tag == "rate_limited":
                return httpx.Response(
                    429,
                    headers={
                        "x-github-api-version-selected": GITHUB_API_VERSION,
                        "retry-after": "1",
                    },
                    json={"message": "slow down"},
                )
            return _not_found()
        if path.endswith("/git/ref/heads/v1") and branch == "absent":
            return _not_found()
        return _callee_response(request)

    result = asyncio.run(_read(handler, ref="v1", workflow_files=("build.yml",)))

    assert isinstance(result, ComponentReadFailure)
    assert result.reason == expected
    assert not any("/git/commits/" in path for path in requests)
    assert (f"{REPOSITORY_PATH}/git/ref/heads/v1" in requests) == (tag == "absent")


def test_exact_commit_ref_skips_reference_reads() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _callee_response(request)

    result = asyncio.run(_read(handler, ref=COMMIT_SHA, workflow_files=("build.yml",)))

    assert isinstance(result, ComponentRevisionObserved)
    assert result.commit_sha == COMMIT_SHA
    assert not any("/git/ref/" in path for path in requests)


def test_missing_workflow_directory_yields_absent_callee_blobs() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/git/trees/{ROOT_TREE_SHA}"):
            return _tree(ROOT_TREE_SHA, [])
        return _callee_response(request)

    result = asyncio.run(_read(handler, ref="v1", workflow_files=("build.yml",)))

    assert result == ComponentRevisionObserved(
        COMMIT_SHA, (ComponentWorkflowBlob("build.yml", None),)
    )


def test_rebound_repository_identity_stops_before_git_reads() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _callee_response(request)

    result = asyncio.run(
        _read(handler, ref="v1", workflow_files=(), repository="example-org/renamed")
    )

    assert result == ComponentReadFailure("provider_binding_mismatch")
    assert not any("/git/" in path for path in requests)


def test_location_matches_case_insensitively_across_pages() -> None:
    pages: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        assert request.url.path == "/installation/repositories"
        page = int(request.url.params["page"])
        pages.append(str(request.url.params))
        if page == 1:
            return _inventory_page(range(1_000, 1_100), total=101, next_page=2)
        return _inventory_page([601], total=101, start=100)

    result = asyncio.run(_locate(handler, names=("shared",), owner="Example-Org"))

    assert result == ComponentRepositoryLocation(
        (ComponentRepositoryScope("Shared", CALLEE),),
        True,
    )
    assert pages == ["page=1&per_page=100", "page=2&per_page=100"]


def test_location_stops_reading_once_every_name_is_found() -> None:
    pages: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        pages.append(int(request.url.params["page"]))
        return _inventory_page([601, *range(1_000, 1_099)], total=500, next_page=2)

    result = asyncio.run(_locate(handler, names=("shared",), owner="example-org"))

    assert result == ComponentRepositoryLocation(
        (ComponentRepositoryScope("Shared", CALLEE),),
        True,
    )
    assert pages == [1]


def test_location_reports_an_incomplete_search_at_its_page_bound() -> None:
    pages: list[int] = []
    total = COMPONENT_INVENTORY_PAGE_SIZE * (COMPONENT_INVENTORY_MAXIMUM_PAGES + 1)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        page = int(request.url.params["page"])
        pages.append(page)
        start = (page - 1) * COMPONENT_INVENTORY_PAGE_SIZE
        return _inventory_page(
            range(10_000 + start, 10_000 + start + COMPONENT_INVENTORY_PAGE_SIZE),
            total=total,
            start=start,
            next_page=page + 1,
        )

    result = asyncio.run(_locate(handler, names=("shared",), owner="example-org"))

    assert result == ComponentRepositoryLocation((), False)
    assert pages == list(range(1, COMPONENT_INVENTORY_MAXIMUM_PAGES + 1))


def test_inventory_failure_is_not_absence() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        return httpx.Response(503, json={"message": "unavailable"})

    result = asyncio.run(_locate(handler, names=("shared",), owner="example-org"))

    assert result == ComponentReadFailure("installation_inventory_unavailable")


type _Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


async def _read(
    handler: _Handler,
    *,
    ref: str,
    workflow_files: tuple[str, ...],
    repository: str = "Example-Org/Shared",
) -> ComponentRevisionOutcome:
    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        return await GitHubComponentRevisionResolver(factory).read_revision(
            scope=CALLEE,
            repository=repository,
            ref=ref,
            workflow_files=workflow_files,
        )
    finally:
        await factory.aclose()


async def _locate(
    handler: _Handler,
    *,
    names: tuple[str, ...],
    owner: str,
) -> ComponentLocationOutcome:
    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        return await GitHubComponentRevisionResolver(factory).locate_repositories(
            installation_id=77,
            owner=owner,
            names=names,
        )
    finally:
        await factory.aclose()


def _callee_response(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/access_tokens"):
        return _token_response()
    if path == "/repositories/601":
        return _json_response(
            {
                "id": 601,
                "owner": {"login": "example-org"},
                "name": "shared",
                "full_name": "example-org/shared",
                "default_branch": "main",
            }
        )
    if path.startswith(f"{REPOSITORY_PATH}/git/ref/"):
        ref = path.removeprefix(f"{REPOSITORY_PATH}/git/ref/")
        return _json_response(
            {"ref": f"refs/{ref}", "object": {"type": "commit", "sha": COMMIT_SHA}}
        )
    if path == f"{REPOSITORY_PATH}/git/commits/{COMMIT_SHA}":
        return _json_response({"sha": COMMIT_SHA, "tree": {"sha": ROOT_TREE_SHA}})
    if path == f"{REPOSITORY_PATH}/git/trees/{ROOT_TREE_SHA}":
        return _tree(ROOT_TREE_SHA, [_tree_entry(".github", GITHUB_TREE_SHA)])
    if path == f"{REPOSITORY_PATH}/git/trees/{GITHUB_TREE_SHA}":
        return _tree(GITHUB_TREE_SHA, [_tree_entry("workflows", WORKFLOWS_TREE_SHA)])
    if path == f"{REPOSITORY_PATH}/git/trees/{WORKFLOWS_TREE_SHA}":
        return _tree(
            WORKFLOWS_TREE_SHA,
            [
                {
                    "path": "build.yml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": BUILD_BLOB_SHA,
                    "size": 10,
                }
            ],
        )
    raise AssertionError(f"unexpected GitHub request: {path}")


def _inventory_page(
    identities: Iterable[int],
    *,
    total: int,
    start: int = 0,
    next_page: int | None = None,
) -> httpx.Response:
    repositories = [
        {
            "id": identity,
            "node_id": f"R_{identity}",
            "owner": {"id": 101, "login": "example-org"},
            "name": "Shared" if identity == 601 else f"repo-{identity}",
            "full_name": f"example-org/{'Shared' if identity == 601 else f'repo-{identity}'}",
            "visibility": "private",
            "default_branch": "main",
            "created_at": "2020-01-01T12:34:56Z",
            "archived": False,
            "disabled": False,
            "fork": False,
        }
        for identity in identities
    ]
    assert start + len(repositories) <= total
    headers = {
        "content-type": "application/json",
        "x-github-api-version-selected": GITHUB_API_VERSION,
    }
    if next_page is not None:
        headers["link"] = (
            "<https://api.github.com/installation/repositories"
            f'?page={next_page}&per_page={COMPONENT_INVENTORY_PAGE_SIZE}>; rel="next"'
        )
    return httpx.Response(
        200,
        headers=headers,
        content=json.dumps({"total_count": total, "repositories": repositories}).encode(),
    )


def _tree(sha: str, entries: list[dict[str, object]]) -> httpx.Response:
    return _json_response({"sha": sha, "truncated": False, "tree": entries})


def _tree_entry(name: str, sha: str) -> dict[str, object]:
    return {"path": name, "mode": "040000", "type": "tree", "sha": sha}


def _json_response(value: object) -> httpx.Response:
    return httpx.Response(
        200,
        headers={
            "content-type": "application/json",
            "x-github-api-version-selected": GITHUB_API_VERSION,
        },
        content=json.dumps(value, separators=(",", ":")).encode(),
    )


def _not_found() -> httpx.Response:
    return httpx.Response(
        404,
        headers={"x-github-api-version-selected": GITHUB_API_VERSION},
        json={"message": "Not Found"},
    )
