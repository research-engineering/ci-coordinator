from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from ci_coordinator.ci_economics._observation_values import digest, positive_id, utc_time
from ci_coordinator.ci_economics.observation_scan import ObservationLane
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object

MAX_OBSERVATION_GAPS: Final = 256
MAX_OBSERVATION_GAP_PAGE_SIZE: Final = 50
OBSERVATION_GAP_RETENTION: Final = timedelta(days=90)
OBSERVATION_GAP_CURSOR_PATTERN: Final = (
    r"^([1-9][0-9]{0,15})\.([1-9][0-9]{0,15})\.([1-9][0-9]{0,15})\.([0-9a-f]{64})$"
)
type ObservationGapReason = Literal[
    "provider_truncated", "outside_source_window", "source_conflict", "outage_window_lost"
]


class InvalidObservationGapCursor(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ObservationGapCursor:
    scope: RepositoryScope
    config_revision: int
    gap_id: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("gap cursor requires exact scope")
        positive_id(self.config_revision, "gap cursor revision")
        digest(self.gap_id)

    @property
    def value(self) -> str:
        return (
            f"{self.scope.installation_id}.{self.scope.repository_id}."
            f"{self.config_revision}.{self.gap_id}"
        )

    @classmethod
    def parse(cls, value: str) -> ObservationGapCursor:
        match = re.fullmatch(OBSERVATION_GAP_CURSOR_PATTERN, value) if type(value) is str else None
        if match is None:
            raise InvalidObservationGapCursor("gap cursor is malformed")
        installation, repository, revision, gap_id = match.groups()
        try:
            return cls(RepositoryScope(int(installation), int(repository)), int(revision), gap_id)
        except (TypeError, ValueError) as error:
            raise InvalidObservationGapCursor("gap cursor is malformed") from error


@dataclass(frozen=True, slots=True)
class ObservationGap:
    scope: RepositoryScope
    config_revision: int
    selector_digest: str
    lane: ObservationLane
    cycle_started_at: datetime
    created_from: datetime
    created_through: datetime
    reason: ObservationGapReason

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("observation gap requires exact scope")
        positive_id(self.config_revision, "gap configuration revision")
        digest(self.selector_digest)
        if self.lane not in {"recent", "backfill"}:
            raise ValueError("unknown observation gap lane")
        object.__setattr__(self, "cycle_started_at", utc_time(self.cycle_started_at))
        object.__setattr__(self, "created_from", utc_time(self.created_from))
        object.__setattr__(self, "created_through", utc_time(self.created_through))
        if not self.created_from <= self.created_through <= self.cycle_started_at:
            raise ValueError("gap interval must be ordered and not later than its cycle")
        if self.reason not in {
            "provider_truncated",
            "outside_source_window",
            "source_conflict",
            "outage_window_lost",
        }:
            raise ValueError("unknown observation gap reason")
        try:
            _ = self.expires_at
        except OverflowError as error:
            raise ValueError("gap cycle cannot represent its retention expiry") from error

    @property
    def expires_at(self) -> datetime:
        return self.cycle_started_at + OBSERVATION_GAP_RETENTION

    @property
    def gap_id(self) -> str:
        return hash_object(self.canonical_mapping())

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-economics-observation-gap/v1",
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "configRevision": self.config_revision,
            "selectorDigest": self.selector_digest,
            "lane": self.lane,
            "cycleStartedAt": self.cycle_started_at.isoformat(),
            "createdFrom": self.created_from.isoformat(),
            "createdThrough": self.created_through.isoformat(),
            "reason": self.reason,
        }


def gap_detail_truncation_deadline(
    prior: datetime | None, evicted: ObservationGap, now: datetime
) -> datetime | None:
    if type(evicted) is not ObservationGap:
        raise TypeError("gap eviction requires an exact observation gap")
    now = utc_time(now)
    prior = None if prior is None else utc_time(prior)
    retained = tuple(
        value for value in (prior, evicted.expires_at) if value is not None and value > now
    )
    return max(retained, default=None)
