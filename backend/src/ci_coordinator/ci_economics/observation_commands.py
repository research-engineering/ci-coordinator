from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from ci_coordinator.ci_economics.observation import ObservationConfiguration, ObservationSnapshot
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

OBSERVATION_CONFIGURED_EVENT_TYPE: Final = "ci-economics-observation-configured/v1"
_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


@dataclass(frozen=True, slots=True)
class ConfigureObservation:
    scope: RepositoryScope
    expected_revision: int
    configuration: ObservationConfiguration
    operation_id: str
    actor: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("observation command requires exact repository scope")
        if (
            type(self.expected_revision) is not int
            or not 0 <= self.expected_revision < MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("expected observation revision must permit one safe increment")
        if type(self.configuration) is not ObservationConfiguration:
            raise TypeError("observation command requires exact configuration")
        if type(self.operation_id) is not str or _OPERATION_ID.fullmatch(self.operation_id) is None:
            raise ValueError("observation operation ID must be a bounded ASCII identifier")
        if (
            type(self.actor) is not str
            or not 1 <= len(self.actor) <= 512
            or "\x00" in self.actor
            or any(0xD800 <= ord(value) <= 0xDFFF for value in self.actor)
        ):
            raise ValueError("observation actor must be bounded Unicode scalar text")

    def next_snapshot(self, configured_at: datetime) -> ObservationSnapshot:
        return ObservationSnapshot(
            self.scope, self.expected_revision + 1, self.configuration, configured_at
        )

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-economics-observation-command/v1",
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "expectedRevision": self.expected_revision,
            "configuration": self.configuration.canonical_mapping(),
            "operationId": self.operation_id,
            "actor": self.actor,
        }

    @property
    def command_digest(self) -> str:
        return hash_object(self.canonical_mapping())

    @property
    def audit_key(self) -> str:
        return (
            f"ci-economics-observation:{self.scope.installation_id}:"
            f"{self.scope.repository_id}:{self.operation_id}"
        )


@dataclass(frozen=True, slots=True)
class ObservationCommitted:
    snapshot: ObservationSnapshot
    replayed: bool

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ObservationSnapshot or type(self.replayed) is not bool:
            raise TypeError("observation receipt requires exact snapshot and replay state")


@dataclass(frozen=True, slots=True)
class ObservationConflict:
    reason: Literal["revision_conflict", "operation_conflict", "capacity_reached"]

    def __post_init__(self) -> None:
        if self.reason not in {"revision_conflict", "operation_conflict", "capacity_reached"}:
            raise ValueError("unknown observation command conflict")


type ObservationWriteResult = ObservationCommitted | ObservationConflict
