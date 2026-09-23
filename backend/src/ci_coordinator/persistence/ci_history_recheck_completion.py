import secrets
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.archive_detail import ArchivedAttemptDetail
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_gap import HistoryRecheckGap
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_RECHECK_JITTER_SECONDS,
    HistoryRecheckClaim,
    HistoryRecheckState,
    current_history_recheck,
    finish_history_recheck,
    recover_exhausted_history_recheck,
)
from ci_coordinator.persistence.ci_history_detail_store import store_history_detail
from ci_coordinator.persistence.ci_history_gap_store import store_history_gap
from ci_coordinator.persistence.ci_history_recheck_rows import (
    load_history_recheck,
    write_history_recheck,
)
from ci_coordinator.persistence.ci_history_state_store import (
    HistoryWriteConflict,
    history_database_time,
    load_history_dataset,
    lock_history_scope,
)
from ci_coordinator.persistence.ci_history_statistics_store import store_history_statistics

type RecheckCompletion = Literal["applied", "claim_lost", "capacity_reached"]
type RecheckGapReason = Literal[
    "provider_not_found", "retry_exhausted", "job_population_incomplete", "incomparable"
]


async def complete_recheck_statistics(
    connection: AsyncConnection,
    claim: HistoryRecheckClaim,
    statistics: ArchivedAttemptStatistics,
    *,
    detail: ArchivedAttemptDetail | None = None,
) -> RecheckCompletion:
    statistics = ArchivedAttemptStatistics.model_validate(statistics)
    hint, identity = claim.state.hint, statistics.attempt.to_attempt()
    if (
        identity.scope,
        identity.workflow_run_id,
        identity.run_attempt,
        statistics.workflow_id,
        statistics.run_created_at,
    ) != (
        hint.cursor.scope,
        hint.cursor.workflow_run_id,
        claim.state.next_attempt,
        hint.workflow_id,
        hint.run_created_at,
    ):
        raise ValueError("history recheck result substitutes its exact source or attempt")
    locked = await _lock_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, now = locked
    original = dataset
    async with connection.begin_nested() as contribution:
        dataset, outcome = await store_history_statistics(connection, dataset, statistics, now=now)
        gap_reason: RecheckGapReason | None = (
            "incomparable"
            if outcome == "incomparable"
            else "job_population_incomplete"
            if statistics.population in {"partial", "unavailable"}
            else None
        )
        if outcome != "capacity_reached" and gap_reason is not None:
            dataset, gap_outcome = await store_history_gap(
                connection, dataset, recheck_gap(dataset, prior, gap_reason), now=now
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
    return await _finish(
        connection,
        dataset,
        prior,
        claim,
        "capacity_reached" if outcome == "capacity_reached" else "recorded",
    )


async def complete_recheck_failure(
    connection: AsyncConnection, claim: HistoryRecheckClaim, *, missing: bool
) -> RecheckCompletion:
    if type(missing) is not bool:
        raise TypeError("history failure classification must be an exact boolean")
    locked = await _lock_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, now = locked
    transition = finish_history_recheck(
        dataset, prior, claim, now=now, outcome="unavailable" if missing else "deferred"
    )
    if transition is None:
        return "claim_lost"
    if missing or transition.outcome == "retry_exhausted":
        dataset, outcome = await store_history_gap(
            connection,
            dataset,
            recheck_gap(dataset, prior, "provider_not_found" if missing else "retry_exhausted"),
            now=now,
        )
        if outcome == "capacity_reached":
            return await _finish(connection, dataset, prior, claim, "capacity_reached")
    return await _finish(
        connection, dataset, prior, claim, "unavailable" if missing else "deferred"
    )


async def recover_recheck_exhaustion(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    prior: HistoryRecheckState,
    *,
    now: datetime,
) -> RecheckCompletion:
    transition = recover_exhausted_history_recheck(dataset, prior, now)
    if transition is None:
        return "claim_lost"
    _, gap_outcome = await store_history_gap(
        connection, dataset, recheck_gap(dataset, prior, "retry_exhausted"), now=now
    )
    if gap_outcome == "capacity_reached":
        return "capacity_reached"
    await write_history_recheck(connection, prior, transition.successor, authority="recovery")
    return "applied"


async def wait_for_history_attempt(
    connection: AsyncConnection, claim: HistoryRecheckClaim
) -> RecheckCompletion:
    locked = await _lock_claim(connection, claim)
    if locked is None:
        return "claim_lost"
    dataset, prior, _ = locked
    return await _finish(connection, dataset, prior, claim, "waiting")


def recheck_gap(
    dataset: HistoryDataset, state: HistoryRecheckState, reason: RecheckGapReason
) -> HistoryRecheckGap:
    hint = state.hint
    return HistoryRecheckGap(
        schemaVersion="ci-economics-history-recheck-gap/v1",
        installationId=hint.cursor.scope.installation_id,
        repositoryId=hint.cursor.scope.repository_id,
        generation=state.generation,
        configurationRevision=dataset.configuration_revision,
        workflowRunId=hint.cursor.workflow_run_id,
        workflowId=hint.workflow_id,
        runAttempt=state.next_attempt,
        runCreatedAt=hint.run_created_at,
        reason=reason,
    )


async def _lock_claim(
    connection: AsyncConnection, claim: HistoryRecheckClaim
) -> tuple[HistoryDataset, HistoryRecheckState, datetime] | None:
    if type(claim) is not HistoryRecheckClaim:
        raise TypeError("history recheck completion requires an exact claim")
    scope = claim.state.hint.cursor.scope
    await lock_history_scope(connection, scope)
    dataset = await load_history_dataset(connection, scope, locked=True)
    prior = await load_history_recheck(
        connection, scope, claim.state.generation, claim.state.hint.cursor.workflow_run_id
    )
    if dataset is None or prior is None:
        return None
    now = await history_database_time(connection)
    return (dataset, prior, now) if current_history_recheck(dataset, prior, claim, now) else None


async def _finish(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    prior: HistoryRecheckState,
    claim: HistoryRecheckClaim,
    outcome: Literal["recorded", "unavailable", "deferred", "capacity_reached", "waiting"],
) -> RecheckCompletion:
    transition = finish_history_recheck(
        dataset,
        prior,
        claim,
        now=await history_database_time(connection),
        outcome=outcome,
        jitter_seconds=secrets.randbelow(MAX_HISTORY_RECHECK_JITTER_SECONDS + 1),
    )
    if transition is None:
        raise HistoryWriteConflict("history recheck lost its lease before terminal CAS")
    await write_history_recheck(connection, prior, transition.successor, authority="holder")
    return "capacity_reached" if outcome == "capacity_reached" else "applied"
