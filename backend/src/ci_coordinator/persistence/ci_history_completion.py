import secrets
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.archive_detail import ArchivedAttemptDetail
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_gap import HistoryGap, HistoryGapReason
from ci_coordinator.ci_economics.history_payload import HistoryCursorPayload
from ci_coordinator.ci_economics.history_ports import HistoryTransition
from ci_coordinator.ci_economics.history_recent import HISTORY_DISCOVERY_INTERVAL_SECONDS
from ci_coordinator.ci_economics.history_rechecks import HistoryRecheckHint
from ci_coordinator.ci_economics.history_scan import (
    HistoryClaim,
    HistoryScanOutcome,
    HistoryScanState,
    current_history_claim,
    finish_history_claim,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_attempts
from ci_coordinator.persistence._schema_ci_history_control import ci_history_rechecks
from ci_coordinator.persistence.ci_history_codec import decode_archive_statistics
from ci_coordinator.persistence.ci_history_detail_store import store_history_detail
from ci_coordinator.persistence.ci_history_gap_store import store_history_gap
from ci_coordinator.persistence.ci_history_recheck_rows import admit_history_recheck
from ci_coordinator.persistence.ci_history_state_store import (
    HistoryWriteConflict,
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    load_history_scan,
    lock_history_scope,
    write_history_scan,
)
from ci_coordinator.persistence.ci_history_statistics_store import (
    history_attempt_predicate,
    load_history_jobs,
    store_history_statistics,
)


async def complete_history_page(
    connection: AsyncConnection, claim: HistoryClaim, observed: ProviderObservationPage
) -> HistoryTransition:
    checkpoint, gap_reason = claim.state.checkpoint.accept_page(observed)
    locked = await lock_current_history_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, now = locked
    if gap_reason is not None:
        dataset, gap_outcome = await store_history_gap(
            connection, dataset, _gap(claim, gap_reason), now=now
        )
        if gap_outcome == "capacity_reached":
            return await _finish(
                connection, dataset, prior, claim, prior.checkpoint, "capacity_reached"
            )
    return await _finish(connection, dataset, prior, claim, checkpoint, "page_recorded", page=1)


async def complete_history_statistics(
    connection: AsyncConnection,
    claim: HistoryClaim,
    statistics: ArchivedAttemptStatistics,
    *,
    detail: ArchivedAttemptDetail | None = None,
) -> HistoryTransition:
    if claim.state.lane != "backfill":
        raise ValueError("discovery hands off runs rather than directly writing statistics")
    statistics = ArchivedAttemptStatistics.model_validate(statistics)
    pending = claim.state.checkpoint.pending
    if pending is None:
        raise ValueError("history claim is not awaiting an attempt")
    identity = statistics.attempt.to_attempt()
    checkpoint = claim.state.checkpoint.complete_attempt(
        identity.scope, identity.workflow_run_id, identity.run_attempt
    )
    source = pending.observed.page.sources[pending.run_index]
    if (
        statistics.workflow_id != pending.workflow_id
        or statistics.run_created_at != source.run_created_at
    ):
        raise ValueError("history result substitutes discovered workflow or source creation time")
    locked = await lock_current_history_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, now = locked
    if not dataset.configuration.selects(statistics.workflow_id):
        return "claim_lost"
    original = dataset
    async with connection.begin_nested() as contribution:
        dataset, outcome = await store_history_statistics(connection, dataset, statistics, now=now)
        gap_reason: HistoryGapReason | None = (
            "incomparable"
            if outcome == "incomparable"
            else "job_population_incomplete"
            if statistics.population in {"partial", "unavailable"}
            else None
        )
        if outcome != "capacity_reached" and gap_reason is not None:
            dataset, gap_outcome = await store_history_gap(
                connection, dataset, _gap(claim, gap_reason), now=now
            )
            if gap_outcome == "capacity_reached":
                outcome = "capacity_reached"
        if (
            outcome in {"recorded", "refined", "replayed"}
            and statistics.population == "complete"
            and detail is not None
        ):
            dataset, detail_outcome = await store_history_detail(
                connection, dataset, statistics, detail, now=now
            )
            if detail_outcome == "capacity_reached":
                outcome = "capacity_reached"
        if outcome == "capacity_reached":
            await contribution.rollback()
            dataset = original
    if outcome == "capacity_reached":
        return await _finish(
            connection, dataset, prior, claim, prior.checkpoint, "capacity_reached"
        )
    scan_outcome: HistoryScanOutcome = "attempt_recorded" if outcome == "recorded" else outcome
    return await _finish(connection, dataset, prior, claim, checkpoint, scan_outcome, attempt=1)


async def complete_unavailable_history_attempt(
    connection: AsyncConnection, claim: HistoryClaim
) -> HistoryTransition:
    if claim.state.lane != "backfill":
        raise ValueError("discovery cannot complete an attempted statistics read")
    pending = claim.state.checkpoint.pending
    if pending is None:
        raise ValueError("history claim is not awaiting an attempt")
    current = pending.attempt_cursor
    checkpoint = claim.state.checkpoint.complete_attempt(
        current.scope, current.workflow_run_id, current.next_attempt
    )
    locked = await lock_current_history_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, now = locked
    if not dataset.configuration.selects(pending.workflow_id):
        return "claim_lost"
    dataset, outcome = await store_history_gap(
        connection, dataset, _gap(claim, "provider_not_found"), now=now
    )
    if outcome == "capacity_reached":
        return await _finish(
            connection, dataset, prior, claim, prior.checkpoint, "capacity_reached"
        )
    return await _finish(connection, dataset, prior, claim, checkpoint, "unavailable", attempt=1)


async def complete_unselected_history_run(
    connection: AsyncConnection, claim: HistoryClaim
) -> HistoryTransition:
    locked = await lock_current_history_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, _ = locked
    checkpoint = prior.checkpoint.skip_unselected_run(dataset.configuration)
    return await _finish(connection, dataset, prior, claim, checkpoint, "unselected")


async def handoff_discovered_history_run(
    connection: AsyncConnection, claim: HistoryClaim
) -> HistoryTransition:
    if claim.state.lane != "discovery":
        raise ValueError("recent handoff requires the discovery lane")
    locked = await lock_current_history_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, now = locked
    pending = prior.checkpoint.pending
    if pending is None:
        raise ValueError("recent handoff requires a persisted pending source")
    if not dataset.configuration.selects(pending.workflow_id):
        checkpoint = prior.checkpoint.skip_unselected_run(dataset.configuration)
        return await _finish(connection, dataset, prior, claim, checkpoint, "unselected")
    source = pending.observed.page.sources[pending.run_index]
    if (
        pending.attempt_cursor.next_attempt == pending.attempt_cursor.latest_attempt
        and await _can_replay_discovered_fact(connection, dataset, source, pending.workflow_id)
    ):
        checkpoint = prior.checkpoint.handoff_run(
            source.attempt.scope, source.attempt.workflow_run_id
        )
        return await _finish(connection, dataset, prior, claim, checkpoint, "replayed")
    hint = HistoryRecheckHint(
        pending.attempt_cursor, pending.workflow_id, source.run_created_at, "recent"
    )
    admission = await admit_history_recheck(connection, dataset, hint, now=now)
    if admission == "capacity_reached":
        return await _finish(
            connection, dataset, prior, claim, prior.checkpoint, "capacity_reached"
        )
    if admission not in {"admitted", "replayed"}:
        raise HistoryWriteConflict("recent handoff lost its dataset admission")
    checkpoint = prior.checkpoint.handoff_run(source.attempt.scope, source.attempt.workflow_run_id)
    return await _finish(connection, dataset, prior, claim, checkpoint, "recheck_queued")


async def _can_replay_discovered_fact(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    source: ProviderRunCollectionSource,
    workflow_id: int,
) -> bool:
    attempt = source.attempt
    if (
        await connection.scalar(
            select(ci_history_rechecks.c.workflow_run_id)
            .where(
                history_scope_predicate(ci_history_rechecks, dataset.scope),
                ci_history_rechecks.c.workflow_run_id == attempt.workflow_run_id,
            )
            .limit(1)
        )
        is not None
    ):
        return False
    row = (
        (
            await connection.execute(
                select(ci_history_attempts).where(
                    history_attempt_predicate(ci_history_attempts, dataset.generation, attempt)
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["has_conflict"] is not False:
        return False
    try:
        jobs = await load_history_jobs(connection, dataset.generation, attempt)
        statistics = decode_archive_statistics(row, jobs)
    except (ValueError, TypeError, OverflowError):
        return False
    return (
        statistics.population == "complete"
        and statistics.attempt.to_attempt() == attempt
        and statistics.workflow_id == workflow_id
        and statistics.run_created_at == source.run_created_at
    )


async def defer_history_claim(
    connection: AsyncConnection,
    claim: HistoryClaim,
    reason: Literal[
        "provider_unavailable", "provider_malformed", "access_unavailable", "timed_out"
    ],
) -> HistoryTransition:
    if reason not in {
        "provider_unavailable",
        "provider_malformed",
        "access_unavailable",
        "timed_out",
    }:
        raise ValueError("unsupported history deferral reason")
    locked = await lock_current_history_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, now = locked
    pending = prior.checkpoint.pending
    if pending is None or prior.lane == "discovery":
        return await _finish(connection, dataset, prior, claim, prior.checkpoint, reason)
    if not dataset.configuration.selects(pending.workflow_id):
        return "claim_lost"
    attempt = pending.attempt_cursor
    hint = HistoryRecheckHint(
        replace(attempt, latest_attempt=attempt.next_attempt),
        pending.workflow_id,
        pending.observed.page.sources[pending.run_index].run_created_at,
        "repair",
    )
    original = dataset
    async with connection.begin_nested() as handoff:
        dataset, gap_outcome = await store_history_gap(
            connection, dataset, _gap(claim, "provider_deferred"), now=now
        )
        capacity_reached = gap_outcome == "capacity_reached"
        if not capacity_reached:
            admission = await admit_history_recheck(
                connection, dataset, hint, now=now, revisit_completed=True
            )
            if admission not in {"admitted", "replayed", "capacity_reached"}:
                raise HistoryWriteConflict("historical repair lost its dataset admission")
            capacity_reached = admission == "capacity_reached"
        if capacity_reached:
            await handoff.rollback()
            dataset = original
    if capacity_reached:
        return await _finish(
            connection, dataset, prior, claim, prior.checkpoint, "capacity_reached"
        )
    checkpoint = prior.checkpoint.complete_attempt(
        attempt.scope, attempt.workflow_run_id, attempt.next_attempt
    )
    return await _finish(connection, dataset, prior, claim, checkpoint, "recheck_queued", attempt=1)


async def lock_current_history_claim(
    connection: AsyncConnection, claim: HistoryClaim
) -> tuple[HistoryDataset, HistoryScanState, datetime] | None:
    if type(claim) is not HistoryClaim:
        raise TypeError("history completion requires an exact claim")
    scope = claim.state.scope
    await lock_history_scope(connection, scope)
    dataset = await load_history_dataset(connection, scope, locked=True)
    prior = await load_history_scan(connection, scope, lane=claim.state.lane, locked=True)
    if dataset is None or prior is None:
        return None
    now = await history_database_time(connection)
    if not current_history_claim(dataset, prior, claim, now):
        return None
    return dataset, prior, now


def _gap(claim: HistoryClaim, reason: HistoryGapReason) -> HistoryGap:
    state = claim.state
    cursor = state.checkpoint.cursor
    pending = state.checkpoint.pending
    attempt = None if pending is None else pending.attempt_cursor
    return HistoryGap(
        schemaVersion="ci-economics-history-gap/v1",
        generation=state.generation,
        configurationRevision=state.configuration_revision,
        cursor=HistoryCursorPayload.model_validate(cursor.canonical_mapping()),
        reason=reason,
        workflowRunId=None if attempt is None else attempt.workflow_run_id,
        runAttempt=None if attempt is None else attempt.next_attempt,
    )


async def _finish(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    prior: HistoryScanState,
    claim: HistoryClaim,
    checkpoint: HistoryCheckpoint,
    outcome: HistoryScanOutcome,
    *,
    page: int = 0,
    attempt: int = 0,
) -> HistoryTransition:
    now = await history_database_time(connection)
    delayed = outcome in {
        "capacity_reached",
        "provider_unavailable",
        "provider_malformed",
        "access_unavailable",
        "timed_out",
    }
    successor = finish_history_claim(
        dataset,
        prior,
        claim,
        now=now,
        checkpoint=checkpoint,
        next_attempt_at=now + timedelta(seconds=60 + secrets.randbelow(61))
        if delayed
        else now + timedelta(seconds=HISTORY_DISCOVERY_INTERVAL_SECONDS)
        if prior.recent is not None and checkpoint.complete
        else now,
        outcome=outcome,
        pages_completed=page,
        attempts_completed=attempt,
    )
    if successor is None:
        raise HistoryWriteConflict("history completion lost its lease before terminal CAS")
    await write_history_scan(connection, prior, successor, live_lease=claim.state.lease)
    return "capacity_reached" if outcome == "capacity_reached" else "applied"
