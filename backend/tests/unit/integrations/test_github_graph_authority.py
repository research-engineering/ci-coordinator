from __future__ import annotations

import asyncio

import pytest
from package_b_support import make_policy

from ci_coordinator.integrations.github.contracts import (
    GitHubRequest,
    GitHubTransportFailure,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.repository_context import DEPENDENCY_GRAPH_PATH
from ci_coordinator.planning_core import DeterministicPlan, plan
from ci_coordinator.repo_context import (
    DiffBuildInput,
    DiffContext,
    DiffFileChangeInput,
    DiffSource,
    RepositoryEpoch,
    build_diff_context,
)
from ci_coordinator.verification_core import verify

from .test_github_repository_context import (
    BASE_SHA,
    HEAD_SHA,
    _contents_response,
    _graph,
    _policy,
    _provider,
    _request,
    _response,
    _Transport,
    _valid_handler,
)


@pytest.mark.parametrize(
    "baseline",
    [
        b"{}",
        _graph().replace(b'"dependents":[]', b'"dependents":["src/module.py"]'),
        _graph().replace(b'"invalidatesWhenChanged":[]', b'"invalidatesWhenChanged":["ci/**"]'),
        _graph().replace(b"test-generator@v1", b"test-generator@v2"),
        _graph() + b"\n",
        None,
    ],
    ids=["malformed", "removed-edge", "removed-invalidator", "generator", "bytes", "unavailable"],
)
def test_candidate_graph_cannot_replace_the_base_authority(baseline: bytes | None) -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if (
            request.operation == "workflow_catalog.get_content"
            and request.query[0].value == BASE_SHA
        ):
            if baseline is None:
                return GitHubTransportFailure(kind="unavailable", message="baseline unavailable")
            return _response(_contents_response(DEPENDENCY_GRAPH_PATH, baseline))
        return _valid_handler()(request)

    transport = _Transport(handler)
    context = asyncio.run(_provider(transport).load(_request(), _policy()))

    assert not context.diff.full_ci_invalidating
    assert "graph_provenance_untrusted" in context.dependency_graph.invalidating_reasons
    assert context.planning_input.full_ci_invalidating
    assert [
        request.query[0].value
        for request in transport.requests
        if request.operation == "workflow_catalog.get_content"
    ] == [HEAD_SHA, BASE_SHA]


@pytest.mark.parametrize(
    "change",
    [
        DiffFileChangeInput(DEPENDENCY_GRAPH_PATH, "modified"),
        DiffFileChangeInput(DEPENDENCY_GRAPH_PATH, "added"),
        DiffFileChangeInput(DEPENDENCY_GRAPH_PATH, "removed"),
        DiffFileChangeInput("old-graph.json", "renamed", previous_path=DEPENDENCY_GRAPH_PATH),
        DiffFileChangeInput(DEPENDENCY_GRAPH_PATH, "renamed", previous_path="old-graph.json"),
    ],
    ids=["modified", "added", "removed", "renamed-from", "renamed-to"],
)
def test_graph_path_invalidation_is_not_owned_by_candidate_metadata(
    change: DiffFileChangeInput,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ci_coordinator.integrations.github import repository_context

    async def changed_diff(
        epoch: RepositoryEpoch, *_args: object, **_kwargs: object
    ) -> DiffContext:
        return build_diff_context(
            DiffBuildInput(
                base_sha=epoch.base_sha,
                head_sha=epoch.head_sha,
                files=(change,),
                source=DiffSource("github", True, 1, 1, 1_000),
            )
        )

    monkeypatch.setattr(repository_context, "load_diff", changed_diff)
    transport = _Transport(_valid_handler())
    context = asyncio.run(_provider(transport).load(_request(), _policy()))

    assert not context.diff.full_ci_invalidating
    assert context.planning_input.full_ci_invalidating
    assert "graph_provenance_untrusted" in context.dependency_graph.invalidating_reasons
    assert not any(
        request.operation == "workflow_catalog.get_content" for request in transport.requests
    )


def test_unchanged_graph_keeps_selective_planning_reachable() -> None:
    transport = _Transport(_valid_handler())
    context = asyncio.run(_provider(transport).load(_request(), _policy()))
    policy = make_policy(
        policy_hash=_policy().policy_hash,
        config_epoch_id=_policy().epoch_id,
        compiled_policy_hash=_policy().compiled_policy_hash,
    )
    candidate = plan(context.planning_input, policy)

    assert isinstance(candidate, DeterministicPlan)
    verified = verify(context.planning_input, policy, candidate)
    assert not verified.fallback.triggered
    assert [item.obligation_id for item in verified.omitted_obligations] == ["docs-lint"]
    assert [item.obligation_id for item in verified.selected_obligations] == [
        "backend-tests",
        "required-baseline",
    ]


def test_unavailable_graph_baseline_produces_verified_full_ci() -> None:
    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if (
            request.operation == "workflow_catalog.get_content"
            and request.query[0].value == BASE_SHA
        ):
            return GitHubTransportFailure(kind="unavailable", message="no baseline")
        return _valid_handler()(request)

    context = asyncio.run(_provider(_Transport(handler)).load(_request(), _policy()))
    policy = make_policy(
        policy_hash=_policy().policy_hash,
        config_epoch_id=_policy().epoch_id,
        compiled_policy_hash=_policy().compiled_policy_hash,
    )
    candidate = plan(context.planning_input, policy)

    assert isinstance(candidate, DeterministicPlan)
    verified = verify(context.planning_input, policy, candidate)
    assert verified.fallback.triggered
    assert not verified.omitted_obligations
    assert tuple((item.obligation_id, item.depth) for item in verified.selected_obligations) == (
        ("backend-tests", "full"),
        ("docs-lint", "standard"),
        ("required-baseline", "full"),
    )


def test_cancellation_during_baseline_read_is_not_an_admitted_fallback() -> None:
    cancellation = asyncio.CancelledError("baseline cancelled")

    def handler(request: GitHubRequest) -> GitHubTransportResult:
        if (
            request.operation == "workflow_catalog.get_content"
            and request.query[0].value == BASE_SHA
        ):
            raise cancellation
        return _valid_handler()(request)

    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(_provider(_Transport(handler)).load(_request(), _policy()))
    assert caught.value is cancellation
