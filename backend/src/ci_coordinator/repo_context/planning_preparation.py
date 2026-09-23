from __future__ import annotations

from typing import Literal, Protocol

from ci_coordinator.repo_context.diff_model import RepositoryEpoch
from ci_coordinator.repo_context.planning_input import PolicySnapshot

type ContextPreparationOutcome = Literal["prepared", "invalid", "not_cached"]


class RepositoryContextPreparer(Protocol):
    async def prepare(
        self, epoch: RepositoryEpoch, policy: PolicySnapshot
    ) -> ContextPreparationOutcome: ...
