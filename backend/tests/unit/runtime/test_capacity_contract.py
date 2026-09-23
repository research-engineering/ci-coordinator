from ci_coordinator.api.http.app import (
    READINESS_REQUEST_CONCURRENCY_LIMIT,
    WORKFLOW_DISCOVERY_REQUEST_CONCURRENCY_LIMIT,
)
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_MAXIMUM_CONCURRENT_EXCHANGES,
)
from ci_coordinator.integrations.github.workflow_discovery import (
    WORKFLOW_DISCOVERY_MAXIMUM_CONCURRENT_BLOBS,
    WORKFLOW_DISCOVERY_MAXIMUM_CONCURRENT_SNAPSHOTS,
)
from ci_coordinator.persistence.readiness import MAXIMUM_DATABASE_READINESS_WAITERS
from ci_coordinator.runtime.readiness import MAXIMUM_CONCURRENT_READINESS_WAITERS


def test_public_readiness_admission_cannot_saturate_inner_single_flight_waiters() -> None:
    assert (
        READINESS_REQUEST_CONCURRENCY_LIMIT
        <= MAXIMUM_CONCURRENT_READINESS_WAITERS
        <= MAXIMUM_DATABASE_READINESS_WAITERS
    )


def test_workflow_discovery_preserves_global_github_exchange_capacity() -> None:
    maximum_discovery_exchanges = (
        WORKFLOW_DISCOVERY_MAXIMUM_CONCURRENT_SNAPSHOTS
        * WORKFLOW_DISCOVERY_MAXIMUM_CONCURRENT_BLOBS
    )

    assert WORKFLOW_DISCOVERY_REQUEST_CONCURRENCY_LIMIT == 1
    assert maximum_discovery_exchanges < GITHUB_MAXIMUM_CONCURRENT_EXCHANGES
