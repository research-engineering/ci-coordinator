from __future__ import annotations

from dataclasses import dataclass, replace

from ci_coordinator.ci_economics._observation_values import positive_id
from ci_coordinator.config_control import RepositoryScope


@dataclass(frozen=True, slots=True)
class HistoryAttemptCursor:
    scope: RepositoryScope
    workflow_run_id: int
    latest_attempt: int
    next_attempt: int = 1

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("history attempt cursor requires an exact repository scope")
        for name in ("workflow_run_id", "latest_attempt", "next_attempt"):
            positive_id(getattr(self, name), name)
        if self.next_attempt > self.latest_attempt:
            raise ValueError("next attempt exceeds the frozen attempt population")

    def advance(
        self, scope: RepositoryScope, workflow_run_id: int, run_attempt: int
    ) -> HistoryAttemptCursor | None:
        if type(scope) is not RepositoryScope:
            raise TypeError("history attempt completion requires an exact repository scope")
        positive_id(workflow_run_id, "workflow run ID")
        positive_id(run_attempt, "run attempt")
        if (scope, workflow_run_id, run_attempt) != (
            self.scope,
            self.workflow_run_id,
            self.next_attempt,
        ):
            raise ValueError("history completion does not match the exact attempt cursor")
        return (
            None
            if self.next_attempt == self.latest_attempt
            else replace(self, next_attempt=self.next_attempt + 1)
        )

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "workflowRunId": self.workflow_run_id,
            "latestAttempt": self.latest_attempt,
            "nextAttempt": self.next_attempt,
        }
