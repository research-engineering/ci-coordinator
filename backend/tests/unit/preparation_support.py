from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ci_coordinator.app.planning_preparation import preparation_epoch
from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance
from ci_coordinator.github_ingestion.seeds import ExactShaRange, PushSeed
from ci_coordinator.repo_context.diff_model import RepositoryEpoch


@dataclass
class PreparationClock:
    value: float = 10.0

    def now(self) -> float:
        return self.value


def preparation_seed(index: int = 0) -> PushSeed:
    return PushSeed(
        WebhookProvenance(
            f"delivery-{index}", "push", "d" * 64, datetime(2026, 9, 6, tzinfo=UTC), "fixture"
        ),
        GitHubRepository(101, 202, "acme", "repository"),
        None,
        "a" * 40,
        "b" * 40,
        ExactShaRange(),
        f"refs/heads/{index}",
    )


def prepared_epoch(index: int = 0) -> RepositoryEpoch:
    epoch = preparation_epoch(preparation_seed(index))
    assert epoch is not None
    return epoch
