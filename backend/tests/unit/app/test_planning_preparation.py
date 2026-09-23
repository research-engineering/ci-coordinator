from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import pytest
from app.test_candidate_planning import _active_epoch
from preparation_support import preparation_seed, prepared_epoch

from ci_coordinator.app.planning_preparation import PlanningPreparationService, preparation_epoch
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.config_epochs import ActiveConfigEpochSnapshot
from ci_coordinator.github_ingestion.provenance import GitHubRepository
from ci_coordinator.github_ingestion.seeds import (
    DynamicCiSeed,
    FullCiInvalidatingRange,
    MergeGroupSeed,
    PullRequestSeed,
    PushSeed,
)
from ci_coordinator.repo_context.diff_model import RepositoryEpoch
from ci_coordinator.repo_context.planning_input import PolicySnapshot
from ci_coordinator.repo_context.planning_preparation import ContextPreparationOutcome


@pytest.mark.parametrize(
    "change",
    [
        lambda s: replace(s, ref=""),
        lambda s: replace(s, ref="x" * 4097),
        lambda s: replace(s, ref="refs/tags/v1"),
        lambda s: replace(s, base_sha="g" * 40),
        lambda s: replace(s, head_sha="b" * 39),
        lambda s: replace(s, range_binding=FullCiInvalidatingRange(("range_unknown",))),
    ],
)
def test_non_exact_or_oversized_seed_is_not_scheduled(
    change: Callable[[PushSeed], PushSeed],
) -> None:
    assert preparation_epoch(change(preparation_seed())) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda r: replace(r, owner="x" * 257),
        lambda r: replace(r, name=""),
        lambda r: replace(r, name="x" * 257),
        lambda r: replace(r, installation_id=True),
        lambda r: replace(r, installation_id=0),
        lambda r: replace(r, repository_id=2**53),
    ],
)
def test_compact_repository_identity_has_an_independent_bound(
    change: Callable[[GitHubRepository], GitHubRepository],
) -> None:
    seed = preparation_seed()
    assert preparation_epoch(replace(seed, repository=change(seed.repository))) is None


@pytest.mark.parametrize("event", ["push", "pull_request", "merge_group"])
def test_supported_seed_projects_only_compact_epoch_without_delivery_or_run(event: str) -> None:
    seed = preparation_seed()
    common = (
        seed.provenance,
        seed.repository,
        seed.action,
        seed.base_sha,
        seed.head_sha,
        seed.range_binding,
    )
    candidate: DynamicCiSeed = seed
    if event == "pull_request":
        candidate = PullRequestSeed(*common, "refs/pull/7/merge", 7)
    elif event == "merge_group":
        candidate = MergeGroupSeed(*common, "refs/heads/gh-readonly-queue/main/group", "group")
    epoch = preparation_epoch(candidate)
    assert epoch is not None
    assert epoch.event_name == event
    assert "delivery" not in epoch.to_identity_mapping()
    if isinstance(candidate, PullRequestSeed):
        for changed in (
            replace(candidate, pull_request_number=8),
            replace(candidate, pull_request_number=True),
            replace(candidate, pull_request_number=0),
        ):
            assert preparation_epoch(changed) is None
    if isinstance(candidate, MergeGroupSeed):
        assert preparation_epoch(replace(candidate, ref=None)) is None


def test_base_seed_without_supported_event_is_ineligible() -> None:
    seed = preparation_seed()
    assert (
        preparation_epoch(
            DynamicCiSeed(
                seed.provenance,
                seed.repository,
                None,
                seed.base_sha,
                seed.head_sha,
                seed.range_binding,
            )
        )
        is None
    )


@dataclass
class ActiveEpochs:
    snapshot: ActiveConfigEpochSnapshot | None
    scopes: list[RepositoryScope] = field(default_factory=list)

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self.scopes.append(scope)
        return self.snapshot


@dataclass
class Preparer:
    calls: list[tuple[RepositoryEpoch, PolicySnapshot]] = field(default_factory=list)

    async def prepare(
        self, epoch: RepositoryEpoch, policy: PolicySnapshot
    ) -> ContextPreparationOutcome:
        self.calls.append((epoch, policy))
        return "prepared"


@pytest.mark.parametrize("state", ["active", "missing", "observe_only", "wrong_scope"])
def test_current_policy_is_resolved_before_provider_work(state: str) -> None:
    active = None if state == "missing" else _active_epoch(dynamic=state != "observe_only")
    resolver = ActiveEpochs(active)
    contexts = Preparer()
    epoch = replace(prepared_epoch(), installation_id=100, repository_id=200)
    if state == "wrong_scope":
        epoch = replace(epoch, repository_id=201)
    result = asyncio.run(PlanningPreparationService(resolver, contexts)(epoch))
    assert resolver.scopes == [RepositoryScope(epoch.installation_id, epoch.repository_id)]
    if state == "active":
        assert active is not None
        projection = project_dynamic_ci_planning(active.draft)
        assert projection is not None
        assert result == "prepared"
        assert contexts.calls == [(epoch, PolicySnapshot.from_projection(projection))]
    else:
        assert result == "ineligible"
        assert contexts.calls == []
