from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ci_coordinator.ci_economics._observation_values import positive_id, utc_time
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object

MAX_OBSERVATION_REPOSITORIES: Final = 256
MAX_OBSERVATION_WORKFLOW_IDS: Final = 32
MAX_OBSERVATION_BACKFILL_DAYS: Final = 6
DEFAULT_OBSERVATION_BACKFILL_DAYS: Final = 1


@dataclass(frozen=True, slots=True)
class ObservationConfiguration:
    enabled: bool
    workflow_ids: tuple[int, ...] | None
    backfill_days: int

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("observation enabled state must be an exact boolean")
        if (
            type(self.backfill_days) is not int
            or not 0 <= self.backfill_days <= MAX_OBSERVATION_BACKFILL_DAYS
        ):
            raise ValueError("observation backfill must be zero to six days")
        if self.workflow_ids is not None:
            if type(self.workflow_ids) is not tuple:
                raise TypeError("selected workflow IDs must be an exact tuple")
            if not 1 <= len(self.workflow_ids) <= MAX_OBSERVATION_WORKFLOW_IDS:
                raise ValueError("observation selects one to32 workflow IDs, or all")
            for workflow_id in self.workflow_ids:
                positive_id(workflow_id, "workflow ID")
            if len(set(self.workflow_ids)) != len(self.workflow_ids):
                raise ValueError("selected workflow IDs must be unique")
            object.__setattr__(self, "workflow_ids", tuple(sorted(self.workflow_ids)))

    def selects(self, workflow_id: int) -> bool:
        positive_id(workflow_id, "workflow ID")
        return self.workflow_ids is None or workflow_id in self.workflow_ids

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "selector": {
                "kind": "all" if self.workflow_ids is None else "selected",
                "workflowIds": None if self.workflow_ids is None else list(self.workflow_ids),
            },
            "backfillDays": self.backfill_days,
        }

    @property
    def selector_digest(self) -> str:
        return hash_object(self.canonical_mapping()["selector"])


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    scope: RepositoryScope
    revision: int
    configuration: ObservationConfiguration
    configured_at: datetime

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("observation requires exact repository scope")
        positive_id(self.revision, "observation revision")
        if type(self.configuration) is not ObservationConfiguration:
            raise TypeError("observation requires exact configuration")
        object.__setattr__(self, "configured_at", utc_time(self.configured_at))

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-economics-observation/v1",
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "revision": self.revision,
            "configuration": self.configuration.canonical_mapping(),
            "configuredAt": self.configured_at.isoformat(),
        }

    @property
    def snapshot_digest(self) -> str:
        return hash_object(self.canonical_mapping())
