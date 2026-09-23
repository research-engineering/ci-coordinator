import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Final, Literal

from ci_coordinator.ci_economics.archive_detail import (
    ArchivedAttemptDetail,
    validate_history_detail,
)
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_ports import (
    HistoryAttemptNotFound,
    HistoryAttemptObservation,
    HistoryAttemptProvider,
    HistoryAttemptResult,
    HistoryCollectionStore,
    HistoryDeferral,
    HistoryTransition,
)
from ci_coordinator.ci_economics.history_rechecks import HistoryRecheckClaim
from ci_coordinator.ci_economics.history_scan import HistoryClaim, HistoryScanLane
from ci_coordinator.ci_economics.observation_ports import ObservationProvider
from ci_coordinator.ci_economics.observation_scan import validate_observation_worker_id
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable, ProviderAttemptDeferred
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.observability import RuntimeDiagnosticObserver, RuntimeMetrics
from ci_coordinator.operator_controls.auth import (
    RepositoryAccessReader,
    RepositoryAccessUnavailable,
)

HISTORY_PROVIDER_DEADLINE_SECONDS: Final = 20
type HistoryLane = Literal["backfill", "recent", "repair"]
HISTORY_LANES: Final[tuple[HistoryLane, ...]] = ("backfill", "recent", "repair")
type HistoryWorkLane = HistoryLane | Literal["discovery"]
HISTORY_WORK_LANES: Final[tuple[HistoryWorkLane, ...]] = (*HISTORY_LANES, "discovery")
type HistoryItemOutcome = (
    HistoryTransition
    | Literal["aborted", "none_due", "recovered", "store_unavailable", "unexpected_error"]
)
type _ReadFailure = Literal["aborted", "access_unavailable", "timed_out", "provider_unavailable"]


class CiHistoryCollectionService:
    def __init__(
        self,
        *,
        store: HistoryCollectionStore,
        discovery: ObservationProvider,
        attempts: HistoryAttemptProvider,
        repository_access: RepositoryAccessReader,
        worker_id: str,
        metrics: RuntimeMetrics,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        validate_observation_worker_id(worker_id)
        self._store = store
        self._discovery = discovery
        self._attempts = attempts
        self._access = repository_access
        self._worker_id = worker_id
        self._metrics = metrics
        self._diagnostics = diagnostics

    async def run(self, abort_signal: asyncio.Event) -> tuple[HistoryItemOutcome, ...]:
        async with asyncio.TaskGroup() as group:
            tasks = tuple(
                group.create_task(self.collect_next(lane, abort_signal)) for lane in HISTORY_LANES
            )
        return tuple(task.result() for task in tasks)

    async def collect_next(self, lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        if type(lane) is not str or lane not in HISTORY_WORK_LANES:
            raise ValueError("history collection requires an admitted lane")
        try:
            result = (
                await self._scan(lane, abort)
                if lane == "backfill" or lane == "discovery"
                else await self._recheck(lane, abort)
            )
        except asyncio.CancelledError:
            self._metrics.ci_history_item(lane, "aborted")
            raise
        except CiEconomicsStoreUnavailable:
            result = "store_unavailable"
        except Exception as error:
            result = "unexpected_error"
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("ci_history_collection", error)
        self._metrics.ci_history_item(lane, result)
        return result

    async def _scan(self, lane: HistoryScanLane, abort: asyncio.Event) -> HistoryItemOutcome:
        if abort.is_set():
            return "aborted"
        with self._metrics.ci_history_stage(lane, "claim"):
            work = await self._store.claim_history(worker_id=self._worker_id, lane=lane)
        if abort.is_set():
            return "aborted"
        if work is None:
            return "none_due"
        dataset, claim = work
        if (
            type(dataset) is not HistoryDataset
            or type(claim) is not HistoryClaim
            or claim.state.lease is None
            or claim.state.lease.worker_id != self._worker_id
            or claim.state.lane != lane
            or (dataset.scope, dataset.generation, dataset.configuration_revision)
            != (claim.state.scope, claim.state.generation, claim.state.configuration_revision)
            or dataset.state != "active"
        ):
            return "store_unavailable"
        pending = claim.state.checkpoint.pending
        result: ProviderObservationPage | HistoryAttemptResult | _ReadFailure
        if pending is None:
            cursor = claim.state.checkpoint.cursor
            result = await self._read(
                lane,
                cursor.scope,
                lambda: self._discovery.discover_observation_page(
                    cursor.scope, cursor.window, page_number=cursor.page_number
                ),
                abort,
            )
            if type(result) is ProviderObservationPage and (
                result.page.scope,
                result.page.window,
                result.page.page_number,
            ) != (cursor.scope, cursor.window, cursor.page_number):
                result = ProviderAttemptDeferred("provider_binding_mismatch")
        else:
            if not dataset.configuration.selects(pending.workflow_id):
                with self._metrics.ci_history_stage(lane, "completion"):
                    return await self._store.skip_unselected_history_run(claim)
            if lane == "discovery":
                with self._metrics.ci_history_stage(lane, "completion"):
                    return await self._store.handoff_discovered_history_run(claim)
            source = pending.observed.page.sources[pending.run_index]
            result = await self._read_attempt(
                lane, pending.attempt_cursor, pending.workflow_id, source.run_created_at, abort
            )
        if abort.is_set() or result == "aborted":
            return "aborted"
        valid_page = pending is None and type(result) is ProviderObservationPage
        observation = _unpack_attempt_observation(result)
        valid_attempt = pending is not None and (
            isinstance(result, (ArchivedAttemptStatistics, HistoryAttemptNotFound))
            or observation is not None
        )
        if (
            not valid_page
            and not valid_attempt
            and not isinstance(result, (ProviderAttemptDeferred, str))
        ):
            result = ProviderAttemptDeferred("provider_malformed")
        self._observe_provider(lane, result)
        with self._metrics.ci_history_stage(lane, "completion"):
            if isinstance(result, str | ProviderAttemptDeferred):
                reason = result.reason if isinstance(result, ProviderAttemptDeferred) else result
                return await self._store.defer_history(claim, _deferral(reason))
            if isinstance(result, HistoryAttemptNotFound):
                return await self._store.record_unavailable_history_attempt(claim)
            if observation is not None:
                statistics, detail = observation
                admitted_detail = _admit_detail_sidecar(statistics, detail)
                if admitted_detail is None:
                    return await self._store.record_history_statistics(claim, statistics)
                return await self._store.record_history_statistics(
                    claim, statistics, detail=admitted_detail
                )
            if isinstance(result, ArchivedAttemptStatistics):
                return await self._store.record_history_statistics(claim, result)
            if type(result) is not ProviderObservationPage:
                return await self._store.defer_history(claim, "provider_malformed")
            return await self._store.record_history_page(claim, result)

    async def _recheck(
        self, lane: Literal["recent", "repair"], abort: asyncio.Event
    ) -> HistoryItemOutcome:
        if abort.is_set():
            return "aborted"
        with self._metrics.ci_history_stage(lane, "claim"):
            claim = await self._store.claim_recheck(worker_id=self._worker_id, source=lane)
        if abort.is_set():
            return "aborted"
        if claim is None:
            return "none_due"
        if isinstance(claim, str):
            return claim
        if (
            type(claim) is not HistoryRecheckClaim
            or claim.state.lease is None
            or claim.state.lease.worker_id != self._worker_id
            or claim.state.hint.source != lane
        ):
            return "store_unavailable"
        hint = claim.state.hint
        result = await self._read_attempt(
            lane, claim.state.cursor, hint.workflow_id, hint.run_created_at, abort
        )
        if abort.is_set() or result == "aborted":
            return "aborted"
        self._observe_provider(lane, result)
        with self._metrics.ci_history_stage(lane, "completion"):
            observation = _unpack_attempt_observation(result)
            if observation is not None:
                statistics, detail = observation
                admitted_detail = _admit_detail_sidecar(statistics, detail)
                if admitted_detail is None:
                    return await self._store.record_recheck_statistics(claim, statistics)
                return await self._store.record_recheck_statistics(
                    claim, statistics, detail=admitted_detail
                )
            if isinstance(result, ArchivedAttemptStatistics):
                return await self._store.record_recheck_statistics(claim, result)
            if (
                isinstance(result, ProviderAttemptDeferred)
                and result.reason == "provider_not_terminal"
            ):
                return await self._store.wait_for_history_attempt(claim)
            return await self._store.record_recheck_failure(
                claim, missing=isinstance(result, HistoryAttemptNotFound)
            )

    async def _read_attempt(
        self,
        lane: HistoryWorkLane,
        cursor: HistoryAttemptCursor,
        workflow_id: int,
        created_at: datetime,
        abort: asyncio.Event,
    ) -> HistoryAttemptResult | _ReadFailure:
        result = await self._read(
            lane,
            cursor.scope,
            lambda: self._attempts.load_history_attempt(cursor, run_created_at=created_at),
            abort,
        )
        if type(result) is HistoryAttemptNotFound:
            if result.requested != cursor:
                return ProviderAttemptDeferred("provider_binding_mismatch")
        else:
            observation = _unpack_attempt_observation(result)
            statistics = result if observation is None else observation[0]
            if type(statistics) is not ArchivedAttemptStatistics:
                if not isinstance(result, str | ProviderAttemptDeferred):
                    return ProviderAttemptDeferred("provider_malformed")
                return result
            attempt = statistics.attempt.to_attempt()
            if (
                attempt.scope,
                attempt.workflow_run_id,
                attempt.run_attempt,
                statistics.workflow_id,
                statistics.run_created_at,
            ) != (
                cursor.scope,
                cursor.workflow_run_id,
                cursor.next_attempt,
                workflow_id,
                created_at,
            ):
                return ProviderAttemptDeferred("provider_binding_mismatch")
        return result

    async def _read[T](
        self,
        lane: HistoryWorkLane,
        scope: RepositoryScope,
        operation: Callable[[], Awaitable[T]],
        abort: asyncio.Event,
    ) -> T | _ReadFailure:
        deadline = asyncio.timeout(HISTORY_PROVIDER_DEADLINE_SECONDS)
        try:
            async with deadline:
                with self._metrics.ci_history_stage(lane, "access"):
                    allowed = await self._access.allows_repository(scope)
                if abort.is_set():
                    return "aborted"
                if allowed is not True:
                    return "access_unavailable"
                with self._metrics.ci_history_stage(lane, "provider"):
                    return await operation()
        except RepositoryAccessUnavailable:
            return "access_unavailable"
        except TimeoutError:
            return "timed_out" if deadline.expired() else "provider_unavailable"

    def _observe_provider(self, lane: HistoryWorkLane, result: object) -> None:
        outcome: str
        if isinstance(result, ProviderAttemptDeferred):
            outcome = result.reason
        elif (observation := _unpack_attempt_observation(result)) is not None:
            outcome = observation[0].population
        elif isinstance(result, ArchivedAttemptStatistics):
            outcome = result.population
        elif isinstance(result, HistoryAttemptNotFound):
            outcome = "unavailable_attempt"
        elif isinstance(result, ProviderObservationPage):
            outcome = "page"
        else:
            outcome = str(result)
        self._metrics.ci_history_provider_result(lane, outcome)


def _unpack_attempt_observation(
    result: object,
) -> tuple[ArchivedAttemptStatistics, ArchivedAttemptDetail | None] | None:
    if type(result) is not HistoryAttemptObservation:
        return None
    return result.statistics, result.detail


def _admit_detail_sidecar(
    statistics: ArchivedAttemptStatistics, detail: ArchivedAttemptDetail | None
) -> ArchivedAttemptDetail | None:
    if detail is None:
        return None
    try:
        return validate_history_detail(statistics, detail)
    except (TypeError, ValueError, OverflowError):
        return None


def _deferral(reason: str) -> HistoryDeferral:
    if reason in {"provider_malformed", "provider_binding_mismatch", "provider_incomplete"}:
        return "provider_malformed"
    if reason == "access_unavailable":
        return "access_unavailable"
    if reason == "timed_out":
        return "timed_out"
    return "provider_unavailable"
