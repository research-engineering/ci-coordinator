from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.collection import ProviderCollectionFailureReason
from ci_coordinator.ci_economics.observation_scan import ObservationScanState
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type ObservationFailure = (
    ProviderCollectionFailureReason | Literal["access_unavailable", "timed_out"]
)
type ObservationOutcome = ObservationFailure | Literal["page_recorded", "capacity_reached"]


@dataclass(frozen=True, slots=True)
class ObservationScanProgress:
    state: ObservationScanState
    last_completed_through: datetime | None = None
    last_page_at: datetime | None = None
    pages_seen: int = 0
    sources_registered: int = 0
    last_outcome: ObservationOutcome | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not ObservationScanState:
            raise TypeError("observation progress requires an exact scan state")
        for name in ("last_completed_through", "last_page_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, utc_time(value))
        for value in (self.pages_seen, self.sources_registered):
            if type(value) is not int or not 0 <= value <= MAX_SAFE_JSON_INTEGER:
                raise ValueError("observation counters must be non-negative safe integers")
        if (
            (self.pages_seen == 0) != (self.last_page_at is None)
            or self.sources_registered > 100 * self.pages_seen
            or (self.pages_seen > 0 and self.last_outcome is None)
        ):
            raise ValueError("observation counters contradict recorded page progress")
        if self.last_completed_through is not None and (
            self.last_completed_through.microsecond
            or self.last_page_at is None
            or self.last_completed_through > self.last_page_at
        ):
            raise ValueError("completed endpoint requires a later recorded page")
        if self.last_outcome not in {
            None,
            "page_recorded",
            "capacity_reached",
            "provider_unavailable",
            "provider_binding_mismatch",
            "provider_malformed",
            "provider_incomplete",
            "provider_not_terminal",
            "provider_unstable",
            "access_unavailable",
            "timed_out",
        }:
            raise ValueError("unknown observation outcome")
