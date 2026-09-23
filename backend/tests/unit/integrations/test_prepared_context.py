from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

import pytest
from preparation_support import PreparationClock

from ci_coordinator.integrations.github import prepared_context
from ci_coordinator.integrations.github.prepared_context import PreparedContextCache
from ci_coordinator.integrations.github.repository_context import GitHubRepositoryContextProvider
from ci_coordinator.repo_context.diff_model import RepositoryEpoch
from ci_coordinator.repo_context.planning_input import PlanningInput, PolicySnapshot

from .test_github_repository_context import _Factory, _policy, _request, _Transport, _valid_handler


def _input(index: int = 0) -> PlanningInput:
    provider = GitHubRepositoryContextProvider(_Factory(_Transport(_valid_handler())))
    request = replace(_request(), ref=f"refs/heads/{index}")
    return asyncio.run(provider.load(request, _policy())).planning_input


@pytest.mark.parametrize(
    "change",
    [
        lambda e: replace(e, installation_id=102),
        lambda e: replace(e, repository_id=203),
        lambda e: replace(e, owner="other"),
        lambda e: replace(e, name="other"),
        lambda e: replace(e, event_name="merge_group"),
        lambda e: replace(e, ref="refs/heads/other"),
        lambda e: replace(e, base_sha="c" * 40),
        lambda e: replace(e, head_sha="c" * 40),
    ],
    ids=["installation", "repository", "owner", "name", "event", "ref", "base", "head"],
)
def test_every_epoch_coordinate_is_part_of_the_cache_key(
    change: Callable[[RepositoryEpoch], RepositoryEpoch],
) -> None:
    context = _input()
    cache = PreparedContextCache(PreparationClock())
    assert cache.put(context, 10.0)
    assert cache.find(context.repo_epoch, context.policy) is not None
    assert cache.find(change(context.repo_epoch), context.policy) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda p: replace(p, epoch_id="3" * 64),
        lambda p: replace(p, compiled_policy_hash="3" * 64),
        lambda p: replace(p, policy_hash="3" * 64),
        lambda p: replace(p, dependency_graph_source="configured"),
        lambda p: replace(p, global_risk_paths=("src/**",)),
        lambda p: replace(p, risk_classes=("frontend",)),
    ],
    ids=["epoch", "compiled", "policy", "source", "risk_paths", "risk_classes"],
)
def test_every_policy_coordinate_is_part_of_the_cache_key(
    change: Callable[[PolicySnapshot], PolicySnapshot],
) -> None:
    context = _input()
    cache = PreparedContextCache(PreparationClock())
    assert cache.put(context, 10.0)
    assert cache.find(context.repo_epoch, change(context.policy)) is None


@pytest.mark.parametrize("now", [130.0, 131.0, 9.0, float("nan"), float("inf")])
def test_invalid_or_expired_clock_cannot_admit_or_publish(now: float) -> None:
    context = _input()
    clock = PreparationClock()
    cache = PreparedContextCache(clock)
    assert cache.put(context, 10.0)
    entry = cache.find(context.repo_epoch, context.policy)
    assert entry is not None
    clock.value = now
    assert not cache.current(entry)
    assert cache.find(context.repo_epoch, context.policy) is None
    assert not cache.put(context, 10.0)


def test_reads_and_repeated_puts_do_not_renew_the_entry() -> None:
    context = _input()
    clock = PreparationClock()
    cache = PreparedContextCache(clock)
    assert cache.put(context, 10.0)
    first = cache.find(context.repo_epoch, context.policy)
    clock.value = 129
    assert cache.find(context.repo_epoch, context.policy) is first
    assert cache.put(context, 129)
    clock.value = 130
    assert cache.find(context.repo_epoch, context.policy) is None


def test_count_budget_and_close_reject_late_publication() -> None:
    context = _input()
    cache = PreparedContextCache(PreparationClock())
    contexts = [_input(i) for i in range(5)]
    for item in contexts:
        assert cache.put(item, 10.0)
    assert cache.find(contexts[0].repo_epoch, context.policy) is None
    for item in contexts[1:]:
        assert cache.find(item.repo_epoch, context.policy) is not None
    cache.close()
    assert not cache.put(context, 10.0)
    assert cache.find(contexts[-1].repo_epoch, context.policy) is None


def test_aggregate_and_individual_weight_budgets_preserve_complete_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _input()
    assert len(context.diff.files) == len(context.dependency_graph.nodes) == 1
    monkeypatch.setattr(prepared_context, "_MAX_UNITS", 3)
    cache = PreparedContextCache(PreparationClock())
    assert cache.put(context, 10.0)
    other = _input(1)
    assert cache.put(other, 10.0)
    assert cache.find(context.repo_epoch, context.policy) is None
    assert cache.find(other.repo_epoch, context.policy) is not None
    monkeypatch.setattr(prepared_context, "_MAX_UNITS", 1)
    assert not cache.put(context, 10.0)
    assert cache.find(other.repo_epoch, context.policy) is not None


@pytest.mark.parametrize("acquired_at", [-1.0, 11.0, float("nan"), float("inf")])
def test_invalid_acquisition_is_not_retained(acquired_at: float) -> None:
    cache = PreparedContextCache(PreparationClock())
    context = _input()
    assert not cache.put(context, acquired_at)
    assert cache.find(context.repo_epoch, context.policy) is None


def test_incomplete_input_is_not_retained() -> None:
    context = replace(_input(), fallback_reasons=("incomplete",))
    cache = PreparedContextCache(PreparationClock())
    assert not cache.put(context, 10.0)
