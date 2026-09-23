from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from preparation_support import PreparationClock

from ci_coordinator.integrations.github.contracts import (
    GitHubRequest,
    GitHubTransportFailure,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.repository_context import GitHubRepositoryContextProvider
from ci_coordinator.repo_context.diff_model import RepositoryEpoch

from .test_github_repository_context import (
    _Factory,
    _policy,
    _pull_request_metadata,
    _repository_identity,
    _request,
    _response,
    _Transport,
    _valid_handler,
)


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_warm_request_reuses_equal_facts_but_still_checks_live_identity(event: str) -> None:
    async def scenario() -> None:
        transport = _Transport(_valid_handler())
        provider = GitHubRepositoryContextProvider(_Factory(transport))
        request = _request(event)
        cold = await provider.load(request, _policy())
        assert not cold.planning_input.full_ci_invalidating
        assert await provider.prepare(cold.repo_epoch, _policy()) == "prepared"
        transport.requests.clear()
        warm = await provider.load(request, _policy())
        assert warm == cold
        assert [r.operation for r in transport.requests] == (
            ["repositories.get_by_id", "diff.get_pull_request"]
            if event == "pull_request"
            else ["repositories.get_by_id"]
        )
        provider.close_prepared_contexts()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "drift", ["installation", "owner", "name", "repository", "pr_base", "pr_head", "pr_number"]
)
def test_warm_context_never_overrides_current_provider_identity(drift: str) -> None:
    async def scenario() -> None:
        transport = _Transport(_valid_handler())
        provider = GitHubRepositoryContextProvider(_Factory(transport))
        request = _request("pull_request")
        cold = await provider.load(request, _policy())
        assert await provider.prepare(cold.repo_epoch, _policy()) == "prepared"

        def changed(call: GitHubRequest) -> GitHubTransportResult:
            if drift == "installation":
                return GitHubTransportFailure(kind="unavailable", message="no access")
            if call.operation == "repositories.get_by_id":
                return _response(
                    _repository_identity(
                        repository_id=203 if drift == "repository" else 202,
                        owner="other" if drift == "owner" else "acme",
                        name="other" if drift == "name" else "repository",
                    )
                )
            if call.operation == "diff.get_pull_request":
                return _response(
                    _pull_request_metadata(
                        number=8 if drift == "pr_number" else 7,
                        base_sha="c" * 40 if drift == "pr_base" else request.base_sha,
                        head_sha="c" * 40 if drift == "pr_head" else request.head_sha,
                    )
                )
            return _valid_handler()(call)

        transport.handler = changed
        result = await provider.load(request, _policy())
        assert result.planning_input.full_ci_invalidating

    asyncio.run(scenario())


@pytest.mark.parametrize("late_stage", ["preparation", "repository", "pr"])
def test_age_is_owned_by_acquisition_and_checked_after_external_await(late_stage: str) -> None:
    async def scenario() -> None:
        clock = PreparationClock()
        transport = _Transport(_valid_handler())
        provider = GitHubRepositoryContextProvider(_Factory(transport), clock=clock)
        request = _request("pull_request")
        epoch = (await provider.load(request, _policy())).repo_epoch
        armed = False

        def delayed(call: GitHubRequest) -> GitHubTransportResult:
            if (
                armed
                and call.operation
                == {
                    "preparation": "workflow_catalog.get_content",
                    "repository": "repositories.get_by_id",
                    "pr": "diff.get_pull_request",
                }[late_stage]
            ):
                clock.value = 130.0
            return _valid_handler()(call)

        transport.handler = delayed
        armed = late_stage == "preparation"
        assert await provider.prepare(epoch, _policy()) == ("not_cached" if armed else "prepared")
        armed = True
        transport.requests.clear()
        result = await provider.load(request, _policy())
        assert not result.planning_input.full_ci_invalidating
        assert any(r.operation == "workflow_catalog.get_content" for r in transport.requests)

    asyncio.run(scenario())


def test_foreground_does_not_wait_for_preparation_and_old_policy_cannot_populate_new_key() -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        request = _request()
        background: asyncio.Task[str] | None = None

        class InterleavedTransport(_Transport):
            async def send(self, request: GitHubRequest) -> GitHubTransportResult:
                if (
                    asyncio.current_task() is background
                    and request.operation == "workflow_catalog.get_content"
                ):
                    started.set()
                    await release.wait()
                return await super().send(request)

        transport = InterleavedTransport(_valid_handler())
        provider = GitHubRepositoryContextProvider(_Factory(transport))
        epoch = (await provider.load(request, _policy())).repo_epoch
        background = asyncio.create_task(provider.prepare(epoch, _policy()))
        await asyncio.wait_for(started.wait(), 1)
        successor = replace(_policy(), epoch_id="2" * 64)
        foreground = await asyncio.wait_for(provider.load(request, successor), 1)
        assert foreground.planning_input.policy == successor
        release.set()
        assert await background == "prepared"
        transport.requests.clear()
        await provider.load(request, successor)
        assert any(r.operation == "workflow_catalog.get_content" for r in transport.requests)

    asyncio.run(scenario())


def test_failed_and_repeated_preparation_do_not_renew_a_successful_entry() -> None:
    async def scenario() -> None:
        clock = PreparationClock()
        transport = _Transport(_valid_handler())
        provider = GitHubRepositoryContextProvider(_Factory(transport), clock=clock)
        epoch = (await provider.load(_request(), _policy())).repo_epoch
        assert await provider.prepare(epoch, _policy()) == "prepared"
        clock.value = 129
        transport.requests.clear()
        assert await provider.prepare(epoch, _policy()) == "prepared"
        assert transport.requests == []
        clock.value = 130
        transport.handler = lambda _: GitHubTransportFailure(kind="unavailable", message="offline")
        assert await provider.prepare(epoch, _policy()) == "invalid"
        transport.handler = _valid_handler()
        transport.requests.clear()
        await provider.load(_request(), _policy())
        assert any(r.operation == "workflow_catalog.get_content" for r in transport.requests)

    asyncio.run(scenario())


def test_preparation_needs_no_workflow_run_and_cannot_publish_after_close() -> None:
    async def scenario() -> None:
        provider = GitHubRepositoryContextProvider(_Factory(_Transport(_valid_handler())))
        epoch = RepositoryEpoch(
            101, 202, "acme", "repository", "push", "refs/heads/main", "a" * 40, "b" * 40
        )
        assert await provider.prepare(epoch, _policy()) == "prepared"
        provider.close_prepared_contexts()
        assert await provider.prepare(epoch, _policy()) == "not_cached"

    asyncio.run(scenario())
