import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from tests.integration.persistence._observation_support import (
    SCOPE,
    observation_command,
    observation_store,
)

from ci_coordinator.ci_economics.discovery import ProviderObservationPage, ProviderRunDiscoveryPage
from ci_coordinator.ci_economics.observation_commands import ObservationCommitted
from ci_coordinator.ci_economics.observation_scan import ObservationClaim
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.connection import create_postgres_engine

pytestmark = pytest.mark.persistence


def _substitute(claim: ObservationClaim, field: str) -> ObservationClaim:
    cursor = claim.cursor
    match field:
        case "installation":
            return replace(claim, scope=RepositoryScope(102, SCOPE.repository_id))
        case "repository":
            return replace(claim, scope=RepositoryScope(SCOPE.installation_id, 203))
        case "configuration":
            return replace(claim, config_revision=claim.config_revision + 1)
        case "revision":
            return replace(claim, expected_revision=claim.expected_revision + 1)
        case "lane":
            return replace(claim, lane="recent")
        case "worker":
            return replace(claim, lease=replace(claim.lease, worker_id="b" * 64))
        case "token":
            return replace(claim, lease=replace(claim.lease, token="0" * 64))
        case "lease_epoch":
            return replace(
                claim,
                lease=replace(
                    claim.lease,
                    acquired_at=claim.lease.acquired_at + timedelta(microseconds=1),
                    expires_at=claim.lease.expires_at + timedelta(microseconds=1),
                ),
            )
        case "interval":
            return replace(
                claim,
                cursor=replace(
                    cursor,
                    interval=replace(
                        cursor.interval,
                        created_from=cursor.interval.created_from - timedelta(seconds=1),
                    ),
                ),
            )
        case "window":
            return replace(
                claim,
                cursor=replace(
                    cursor,
                    window=replace(
                        cursor.window,
                        created_through=cursor.window.created_through - timedelta(seconds=1),
                    ),
                ),
            )
        case "page":
            return replace(claim, cursor=replace(cursor, page_number=2))
        case "cycle":
            return replace(
                claim,
                cursor=replace(
                    cursor, cycle_started_at=cursor.cycle_started_at + timedelta(microseconds=1)
                ),
            )
        case _:
            raise AssertionError(field)


@pytest.mark.parametrize(
    "field",
    [
        "installation",
        "repository",
        "configuration",
        "revision",
        "lane",
        "worker",
        "token",
        "lease_epoch",
        "interval",
        "window",
        "page",
        "cycle",
    ],
)
def test_holder_operations_reject_each_substituted_claim_operand_without_changing_state(
    runtime_postgres_database_url: str, field: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = observation_store(engine)
        try:
            assert isinstance(
                await adapter.configure_observation(observation_command()), ObservationCommitted
            )
            recent = await adapter.claim_observation(worker_id="a" * 64)
            backfill = await adapter.claim_observation(worker_id="a" * 64)
            assert recent is not None and recent.claim.lane == "recent"
            assert backfill is not None and backfill.claim.lane == "backfill"
            claim = backfill.claim
            substituted = _substitute(claim, field)
            assert substituted != claim
            before = await adapter.observation_status(SCOPE)
            assert (
                await adapter.defer_observation(substituted, "provider_unavailable") == "claim_lost"
            )
            page = ProviderObservationPage(
                ProviderRunDiscoveryPage(
                    substituted.scope,
                    substituted.cursor.window,
                    substituted.cursor.page_number,
                    0 if substituted.cursor.page_number == 1 else 101,
                    (),
                    "exhausted" if substituted.cursor.page_number == 1 else "truncated",
                ),
                (),
            )
            assert await adapter.record_observation_page(substituted, page) == "claim_lost"
            after = await adapter.observation_status(SCOPE)
            assert after.snapshot == before.snapshot and after.scans == before.scans
            assert after.occupied_source_slots == before.occupied_source_slots == 0
            assert await adapter.defer_observation(claim, "provider_unavailable") == "applied"
            current = await adapter.observation_status(SCOPE)
            scan = next(item for item in current.scans if item.state.lane == "backfill")
            assert scan.state.lease is None and scan.state.cursor == claim.cursor
            assert scan.state.revision == claim.expected_revision + 1
            assert scan.last_outcome == "provider_unavailable"
            assert await adapter.defer_observation(claim, "provider_unavailable") == "claim_lost"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
