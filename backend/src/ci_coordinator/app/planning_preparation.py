from __future__ import annotations

import re
from typing import Literal

from ci_coordinator.app.dynamic_plan_service import ActiveConfigEpochResolver
from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.github_ingestion.seeds import (
    DynamicCiSeed,
    ExactShaRange,
    MergeGroupSeed,
    PullRequestSeed,
    PushSeed,
)
from ci_coordinator.repo_context.diff_model import RepositoryEpoch
from ci_coordinator.repo_context.planning_input import PolicySnapshot
from ci_coordinator.repo_context.planning_preparation import (
    ContextPreparationOutcome,
    RepositoryContextPreparer,
)

type PlanningPreparationOutcome = ContextPreparationOutcome | Literal["ineligible"]


def preparation_epoch(seed: DynamicCiSeed) -> RepositoryEpoch | None:
    if not isinstance(seed, PushSeed | PullRequestSeed | MergeGroupSeed):
        return None
    if type(seed.range_binding) is not ExactShaRange:
        return None
    repository = seed.repository
    if (
        any(
            type(value) is not int or not 0 < value < 2**53
            for value in (repository.installation_id, repository.repository_id)
        )
        or any(
            type(value) is not str or not 0 < len(value) <= limit
            for value, limit in ((repository.owner, 256), (repository.name, 256), (seed.ref, 4096))
        )
        or any(
            type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None
            for value in (seed.base_sha, seed.head_sha)
        )
    ):
        return None
    if isinstance(seed, PullRequestSeed) and (
        type(seed.pull_request_number) is not int
        or not 0 < seed.pull_request_number < 2**63
        or seed.ref != f"refs/pull/{seed.pull_request_number}/merge"
    ):
        return None
    if not isinstance(seed.ref, str):
        return None
    if not isinstance(seed, PullRequestSeed) and not seed.ref.startswith("refs/heads/"):
        return None
    return RepositoryEpoch(
        installation_id=repository.installation_id,
        repository_id=repository.repository_id,
        owner=repository.owner,
        name=repository.name,
        event_name=seed.event_name,
        ref=seed.ref,
        base_sha=seed.base_sha,
        head_sha=seed.head_sha,
    )


class PlanningPreparationService:
    def __init__(
        self, active_epochs: ActiveConfigEpochResolver, contexts: RepositoryContextPreparer
    ) -> None:
        self._active_epochs = active_epochs
        self._contexts = contexts

    async def __call__(self, epoch: RepositoryEpoch) -> PlanningPreparationOutcome:
        scope = RepositoryScope(epoch.installation_id, epoch.repository_id)
        active = await self._active_epochs.load_active(scope)
        if active is None or active.active.scope != scope:
            return "ineligible"
        projection = project_dynamic_ci_planning(active.draft)
        if projection is None:
            return "ineligible"
        return await self._contexts.prepare(epoch, PolicySnapshot.from_projection(projection))
