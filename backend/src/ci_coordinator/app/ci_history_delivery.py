import asyncio
from typing import Final, Literal

from ci_coordinator.ci_economics.history_configuration import MAX_HISTORY_DATASETS
from ci_coordinator.ci_economics.history_ports import HistoryDeliveryStatus, HistoryDeliveryStore
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.observability import RuntimeDiagnosticObserver, RuntimeMetrics

HISTORY_DELIVERY_SCOPES_PER_ROUND: Final = 16
HISTORY_DELIVERY_WORKERS: Final = 4
HISTORY_DELIVERY_SCOPE_SECONDS: Final = 10
HISTORY_DELIVERY_ROUND_SECONDS: Final = 50
type HistoryDeliveryOutcome = (
    HistoryDeliveryStatus | Literal["aborted", "store_unavailable", "unexpected_error", "timed_out"]
)


class CiHistoryDeliveryService:
    def __init__(
        self,
        store: HistoryDeliveryStore,
        metrics: RuntimeMetrics,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._diagnostics = diagnostics
        self._cursor: tuple[int, int] | None = None

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        await self.run(abort_signal)

    async def run(self, abort: asyncio.Event) -> tuple[HistoryDeliveryOutcome, ...]:
        if abort.is_set():
            return (self._record("aborted"),)
        results: list[HistoryDeliveryOutcome] = []
        deadline = asyncio.timeout(HISTORY_DELIVERY_ROUND_SECONDS)
        try:
            async with deadline:
                scopes = await self._store.list_delivery_scopes()
                selected = self._select(scopes)
                pending = iter(selected)

                async def worker() -> None:
                    while not abort.is_set():
                        scope = next(pending, None)
                        if scope is None:
                            return
                        self._cursor = (scope.installation_id, scope.repository_id)
                        results.append(await self._transfer(scope))

                async with asyncio.TaskGroup() as group:
                    for _ in range(min(HISTORY_DELIVERY_WORKERS, len(selected))):
                        group.create_task(worker())
            if abort.is_set():
                results.append(self._record("aborted"))
            return tuple(results)
        except asyncio.CancelledError:
            raise
        except CiEconomicsStoreUnavailable:
            results.append(self._record("store_unavailable"))
        except Exception as error:
            outcome: HistoryDeliveryOutcome = (
                "timed_out"
                if isinstance(error, TimeoutError) and deadline.expired()
                else "unexpected_error"
            )
            if outcome == "unexpected_error" and self._diagnostics is not None:
                self._diagnostics.unexpected_failure("ci_history_delivery", error)
            results.append(self._record(outcome))
        return tuple(results)

    def _select(self, scopes: tuple[RepositoryScope, ...]) -> tuple[RepositoryScope, ...]:
        if (
            type(scopes) is not tuple
            or len(scopes) > MAX_HISTORY_DATASETS
            or any(type(scope) is not RepositoryScope for scope in scopes)
        ):
            raise CiEconomicsStoreUnavailable("history scope population is not admitted")
        keys = [(scope.installation_id, scope.repository_id) for scope in scopes]
        if keys != sorted(set(keys)):
            raise CiEconomicsStoreUnavailable("history scope identities must be sorted and unique")
        start = next(
            (index for index, key in enumerate(keys) if self._cursor is None or key > self._cursor),
            0,
        )
        return (scopes[start:] + scopes[:start])[:HISTORY_DELIVERY_SCOPES_PER_ROUND]

    async def _transfer(self, scope: RepositoryScope) -> HistoryDeliveryOutcome:
        deadline = asyncio.timeout(HISTORY_DELIVERY_SCOPE_SECONDS)
        try:
            async with deadline:
                result = await self._store.transfer_deliveries(scope)
            self._metrics.ci_history_delivery(
                result.status,
                candidate_count=result.candidate_count,
                transferred_count=result.transferred_count,
                pending_age_seconds=result.pending_age_seconds,
                expired_pending_sample=result.expired_pending_sample,
            )
            return result.status
        except asyncio.CancelledError:
            self._record("aborted")
            raise
        except CiEconomicsStoreUnavailable:
            return self._record("store_unavailable")
        except Exception as error:
            if isinstance(error, TimeoutError) and deadline.expired():
                return self._record("timed_out")
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("ci_history_delivery", error)
            return self._record("unexpected_error")

    def _record(self, outcome: HistoryDeliveryOutcome) -> HistoryDeliveryOutcome:
        self._metrics.ci_history_delivery(outcome)
        return outcome
