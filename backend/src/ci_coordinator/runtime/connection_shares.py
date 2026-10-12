"""Source-counted PostgreSQL connection shares of one connected backend process.

Each counted task holds at most one pooled connection at a time, so a population's
share is its maximum number of concurrently running tasks. The inventory is a
declared, unmeasured design input: composition does not enforce it, and the
example pool of 16 does not satisfy it (webhook ingress design, CI-069 measures).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ci_coordinator.app.ci_history_collection import HISTORY_WORK_LANES
from ci_coordinator.app.ci_history_delivery import HISTORY_DELIVERY_WORKERS
from ci_coordinator.app.ci_observation_scanning import OBSERVATION_CONCURRENT_READS
from ci_coordinator.ci_economics import CiEconomicsProfile
from ci_coordinator.runtime.webhook_admission import WEBHOOK_VERIFIER_PERMITS

FOREGROUND_CONNECTION_RESERVE: Final = 4


@dataclass(frozen=True, slots=True)
class ConnectionShare:
    population: str
    maximum_connections: int

    def __post_init__(self) -> None:
        if type(self.population) is not str or not self.population:
            raise ValueError("connection share population must be named")
        if type(self.maximum_connections) is not int or self.maximum_connections < 1:
            raise ValueError("connection share must be a positive connection count")


@dataclass(frozen=True, slots=True)
class ConnectionShareInventory:
    webhook_permits: int
    background: tuple[ConnectionShare, ...]
    foreground_reserve: int

    @property
    def background_connections(self) -> int:
        return sum(share.maximum_connections for share in self.background)

    @property
    def required_pool_size(self) -> int:
        return self.webhook_permits + self.background_connections + self.foreground_reserve

    def admits(self, database_pool_size: int) -> bool:
        return self.required_pool_size <= database_pool_size


def runtime_connection_shares(
    ci_economics_profile: CiEconomicsProfile,
    *,
    webhook_permits: int = WEBHOOK_VERIFIER_PERMITS,
) -> ConnectionShareInventory:
    """Count every lifecycle-owned population; the maintenance round runs them concurrently."""
    return ConnectionShareInventory(
        webhook_permits=webhook_permits,
        background=(
            ConnectionShare("reconciliation_round", 1),
            ConnectionShare(
                "ci_economics_collection", ci_economics_profile.maximum_concurrent_claims
            ),
            ConnectionShare("ci_economics_expiry", 1),
            ConnectionShare("ci_economics_tombstone_purge", 1),
            ConnectionShare("ci_economics_observation_cleanup", 1),
            ConnectionShare("ci_observation_discovery", OBSERVATION_CONCURRENT_READS),
            ConnectionShare("ci_observation_gap_cleanup", 1),
            ConnectionShare("ci_history_delivery", HISTORY_DELIVERY_WORKERS),
            ConnectionShare("ci_history_detail_cleanup", 1),
            ConnectionShare("activity_cleanup", 1),
            ConnectionShare("ci_history_collection_lanes", len(HISTORY_WORK_LANES)),
            ConnectionShare("planning_preparation", 1),
        ),
        foreground_reserve=FOREGROUND_CONNECTION_RESERVE,
    )
