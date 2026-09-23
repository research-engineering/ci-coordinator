import asyncio
from typing import Final, Literal

from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.observation_ports import (
    ClaimedObservation,
    ObservationClaimStore,
    ObservationProvider,
)
from ci_coordinator.ci_economics.observation_progress import ObservationFailure
from ci_coordinator.ci_economics.observation_scan import validate_observation_worker_id
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable, ProviderAttemptDeferred
from ci_coordinator.observability import RuntimeDiagnosticObserver, RuntimeMetrics
from ci_coordinator.operator_controls.auth import (
    RepositoryAccessReader,
    RepositoryAccessUnavailable,
)

OBSERVATION_PROVIDER_DEADLINE_SECONDS: Final = 20
OBSERVATION_CONCURRENT_READS: Final = 2
OBSERVATION_CLAIMS_PER_WORKER: Final = 2
type ObservationItemOutcome = (
    ObservationFailure
    | Literal[
        "aborted",
        "none_due",
        "page_recorded",
        "capacity_reached",
        "claim_lost",
        "store_unavailable",
        "unexpected_error",
    ]
)


class CiObservationScanningService:
    def __init__(
        self,
        *,
        store: ObservationClaimStore,
        provider: ObservationProvider,
        repository_access: RepositoryAccessReader,
        worker_id: str,
        metrics: RuntimeMetrics,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        validate_observation_worker_id(worker_id)
        self._store = store
        self._provider = provider
        self._repository_access = repository_access
        self._worker_id = worker_id
        self._metrics = metrics
        self._diagnostics = diagnostics

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        await self.run(abort_signal)

    async def run(self, abort_signal: asyncio.Event) -> tuple[ObservationItemOutcome, ...]:
        async with asyncio.TaskGroup() as group:
            tasks = tuple(
                group.create_task(self._worker(abort_signal))
                for _ in range(OBSERVATION_CONCURRENT_READS)
            )
        return tuple(outcome for task in tasks for outcome in task.result())

    async def purge_gaps(self, abort_signal: asyncio.Event, /) -> None:
        if not abort_signal.is_set():
            await self._store.purge_observation_gaps(scope_limit=4)

    async def _worker(self, abort_signal: asyncio.Event) -> tuple[ObservationItemOutcome, ...]:
        outcomes: list[ObservationItemOutcome] = []
        for _ in range(OBSERVATION_CLAIMS_PER_WORKER):
            try:
                outcome = await self._scan_one(abort_signal)
            except asyncio.CancelledError:
                self._metrics.ci_observation_item("aborted")
                raise
            except CiEconomicsStoreUnavailable:
                outcome = "store_unavailable"
            except Exception as error:
                outcome = "unexpected_error"
                if self._diagnostics is not None:
                    self._diagnostics.unexpected_failure("ci_observation_discovery", error)
            self._metrics.ci_observation_item(outcome)
            outcomes.append(outcome)
            if outcome in {"aborted", "none_due", "store_unavailable"}:
                break
        return tuple(outcomes)

    async def _scan_one(self, abort_signal: asyncio.Event) -> ObservationItemOutcome:
        if abort_signal.is_set():
            return "aborted"
        work = await self._store.claim_observation(worker_id=self._worker_id)
        if work is None:
            return "none_due"
        if type(work) is not ClaimedObservation or work.claim.lease.worker_id != self._worker_id:
            return "store_unavailable"
        if abort_signal.is_set():
            return "aborted"
        result = await self._discover(work, abort_signal)
        if abort_signal.is_set():
            return "aborted"
        if isinstance(result, (ProviderAttemptDeferred, str)):
            reason = result.reason if isinstance(result, ProviderAttemptDeferred) else result
            transition = await self._store.defer_observation(work.claim, reason)
            if transition == "claim_lost":
                return "claim_lost"
            return reason if transition == "applied" else "store_unavailable"
        transition = await self._store.record_observation_page(work.claim, result)
        if transition == "applied":
            return "page_recorded"
        if transition in {"claim_lost", "capacity_reached"}:
            return transition
        return "store_unavailable"

    async def _discover(
        self, work: ClaimedObservation, abort_signal: asyncio.Event
    ) -> ProviderObservationPage | ProviderAttemptDeferred | ObservationFailure:
        deadline = asyncio.timeout(OBSERVATION_PROVIDER_DEADLINE_SECONDS)
        try:
            async with deadline:
                allowed = await self._repository_access.allows_repository(work.claim.scope)
                if allowed is not True or abort_signal.is_set():
                    return "access_unavailable"
                result = await self._provider.discover_observation_page(
                    work.claim.scope,
                    work.claim.cursor.window,
                    page_number=work.claim.cursor.page_number,
                )
        except RepositoryAccessUnavailable:
            return "access_unavailable"
        except TimeoutError:
            return "timed_out" if deadline.expired() else "provider_unavailable"
        if type(result) is ProviderAttemptDeferred:
            return result
        if (
            type(result) is not ProviderObservationPage
            or result.page.scope != work.claim.scope
            or result.page.window != work.claim.cursor.window
            or result.page.page_number != work.claim.cursor.page_number
        ):
            return "provider_binding_mismatch"
        return result
