import secrets
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.observation import ObservationSnapshot
from ci_coordinator.ci_economics.observation_gaps import ObservationGap, ObservationGapReason
from ci_coordinator.ci_economics.observation_ports import ObservationTransitionResult
from ci_coordinator.ci_economics.observation_progress import (
    ObservationFailure,
    ObservationScanProgress,
)
from ci_coordinator.ci_economics.observation_scan import (
    ObservationClaim,
    current_observation_claim,
    finish_observation_claim,
)
from ci_coordinator.ci_economics.observation_schedule import observation_cycle_due_at
from ci_coordinator.ci_economics.observation_windows import advance_discovery_cursor
from ci_coordinator.ci_economics.sources import ProviderSourceRegistrationResult
from ci_coordinator.persistence.ci_economics_collection_repository import (
    _PostgresCiEconomicsCollectionRepository,
)
from ci_coordinator.persistence.ci_observation_codec import decode_subscription
from ci_coordinator.persistence.ci_observation_gaps import record_observation_gap
from ci_coordinator.persistence.ci_observation_lock import (
    load_locked_subscription,
    lock_observation_scope,
    observation_database_time,
)
from ci_coordinator.persistence.ci_observation_scan_state import (
    ObservationLeaseExpired,
    load_observation_scans,
    schedule_observation_scope,
    write_observation_scan,
)


async def record_observation_page(
    connection: AsyncConnection,
    collections: _PostgresCiEconomicsCollectionRepository,
    claim: ObservationClaim,
    observed: ProviderObservationPage,
) -> ObservationTransitionResult:
    if type(claim) is not ObservationClaim or type(observed) is not ProviderObservationPage:
        raise TypeError("observation completion requires exact claim and page")
    if observed.page.scope != claim.scope:
        raise ValueError("observation page crosses repository scope")
    page_progress = advance_discovery_cursor(claim.cursor, observed.page)
    locked = await _locked_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    snapshot, scans, prior, now = locked
    selected = tuple(
        source
        for source, workflow_id in zip(observed.page.sources, observed.workflow_ids, strict=True)
        if snapshot.configuration.selects(workflow_id)
    )
    outcomes: tuple[ProviderSourceRegistrationResult, ...] = ()
    if page_progress.register_sources:
        outcomes = await collections.register_provider_sources(claim.scope, selected)
    capacity_reached = "capacity_reached" in outcomes
    cursor = claim.cursor if capacity_reached else page_progress.cursor
    if page_progress.gap_reason is not None:
        await record_observation_gap(
            connection, _page_gap(snapshot, claim, page_progress.gap_reason), now
        )
    for reason in ("outside_source_window", "source_conflict"):
        if reason in outcomes:
            await record_observation_gap(connection, _page_gap(snapshot, claim, reason), now)
    now = await observation_database_time(connection)
    next_attempt_at = (
        _retry_at(now)
        if capacity_reached
        else observation_cycle_due_at(claim.lane, now)
        if cursor is None
        else now
    )
    state = finish_observation_claim(
        snapshot, prior.state, claim, now=now, cursor=cursor, next_attempt_at=next_attempt_at
    )
    if state is None:
        raise ObservationLeaseExpired("observation page lost its lease before commit")
    successor = replace(
        prior,
        state=state,
        last_completed_through=claim.cursor.interval.created_through
        if cursor is None
        else prior.last_completed_through,
        last_page_at=now,
        pages_seen=prior.pages_seen + 1,
        sources_registered=prior.sources_registered + outcomes.count("registered"),
        last_outcome="capacity_reached" if capacity_reached else "page_recorded",
    )
    await write_observation_scan(connection, prior.state, successor, live_lease=claim.lease)
    await schedule_observation_scope(
        connection, snapshot, scans, successor, "backfill" if claim.lane == "recent" else "recent"
    )
    return "capacity_reached" if capacity_reached else "applied"


async def defer_observation(
    connection: AsyncConnection,
    claim: ObservationClaim,
    reason: ObservationFailure,
) -> ObservationTransitionResult:
    if type(claim) is not ObservationClaim:
        raise TypeError("observation deferral requires exact claim")
    if reason not in {
        "provider_unavailable",
        "provider_binding_mismatch",
        "provider_malformed",
        "provider_incomplete",
        "provider_not_terminal",
        "provider_unstable",
        "access_unavailable",
        "timed_out",
    }:
        raise ValueError("unknown observation failure")
    locked = await _locked_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    snapshot, scans, prior, now = locked
    state = finish_observation_claim(
        snapshot, prior.state, claim, now=now, cursor=claim.cursor, next_attempt_at=_retry_at(now)
    )
    if state is None:
        return "claim_lost"
    successor = replace(prior, state=state, last_outcome=reason)
    await write_observation_scan(connection, prior.state, successor, live_lease=claim.lease)
    await schedule_observation_scope(
        connection, snapshot, scans, successor, "backfill" if claim.lane == "recent" else "recent"
    )
    return "applied"


async def _locked_claim(
    connection: AsyncConnection,
    claim: ObservationClaim,
) -> (
    tuple[
        ObservationSnapshot,
        tuple[ObservationScanProgress, ObservationScanProgress],
        ObservationScanProgress,
        datetime,
    ]
    | None
):
    await lock_observation_scope(connection, claim.scope)
    row = await load_locked_subscription(connection, claim.scope)
    if row is None:
        return None
    snapshot = decode_subscription(row)
    scans = await load_observation_scans(connection, snapshot)
    prior = next(scan for scan in scans if scan.state.lane == claim.lane)
    now = await observation_database_time(connection)
    if not current_observation_claim(snapshot, prior.state, claim, now):
        return None
    return snapshot, scans, prior, now


def _page_gap(
    snapshot: ObservationSnapshot,
    claim: ObservationClaim,
    reason: ObservationGapReason | Literal["outside_source_window", "source_conflict"],
) -> ObservationGap:
    return ObservationGap(
        claim.scope,
        claim.config_revision,
        snapshot.configuration.selector_digest,
        claim.lane,
        claim.cursor.cycle_started_at,
        claim.cursor.window.created_from,
        claim.cursor.window.created_through,
        reason,
    )


def _retry_at(now: datetime) -> datetime:
    return now + timedelta(seconds=60 + secrets.randbelow(61))
