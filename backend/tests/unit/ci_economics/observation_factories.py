from datetime import UTC, datetime, timedelta

from ci_coordinator.ci_economics.discovery import RunDiscoveryWindow
from ci_coordinator.ci_economics.observation import ObservationConfiguration, ObservationSnapshot
from ci_coordinator.ci_economics.observation_scan import (
    ObservationClaim,
    ObservationLease,
    ObservationScanState,
)
from ci_coordinator.ci_economics.observation_windows import ObservationCursor
from ci_coordinator.config_control import RepositoryScope

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
SCOPE = RepositoryScope(101, 202)


def observation() -> ObservationSnapshot:
    return ObservationSnapshot(SCOPE, 3, ObservationConfiguration(True, (10, 20), 1), NOW)


def cursor() -> ObservationCursor:
    return ObservationCursor.start(RunDiscoveryWindow(NOW - timedelta(hours=8), NOW), NOW)


def claimed_scan() -> tuple[ObservationSnapshot, ObservationScanState, ObservationClaim]:
    position = cursor()
    lease = ObservationLease("a" * 64, "b" * 64, NOW, NOW + timedelta(seconds=60))
    state = ObservationScanState(SCOPE, 3, "backfill", 7, position, NOW, lease)
    claim = ObservationClaim(SCOPE, 3, "backfill", 7, position, lease)
    return observation(), state, claim
