"""Application sequencing for bounded CI economics evidence collection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ci_coordinator.ci_economics import (
    MAX_ECONOMICS_PAGE_SIZE,
    AttemptCollectionStore,
    AttemptEconomics,
    AttemptEvidenceProvider,
    AttemptIdentity,
    AttemptSummaryPage,
    CiEconomicsEvidenceConflict,
    CiEconomicsProfile,
    CiEconomicsQuery,
    CiEconomicsRetentionStore,
    CiEconomicsStoreUnavailable,
    ProviderAttemptDeferred,
    WorkflowJobFact,
)
from ci_coordinator.ci_economics.catalog import (
    ProviderSourcePage,
    decode_source_cursor,
    require_catalog_page,
)
from ci_coordinator.ci_economics.read_models import RecordedAttemptEconomics
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.observability import RuntimeMetrics

type CollectionItemOutcome = Literal[
    "aborted",
    "captured",
    "claim_lost",
    "deferred",
    "none_due",
    "terminal_conflict",
]

COLLECTION_ITEM_OUTCOMES: Final[tuple[CollectionItemOutcome, ...]] = (
    "aborted",
    "captured",
    "claim_lost",
    "deferred",
    "none_due",
    "terminal_conflict",
)
_COLLECTION_ITEM_OUTCOME_SET: Final = frozenset(COLLECTION_ITEM_OUTCOMES)


class CiEconomicsAuthorizer(Protocol):
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool: ...


@dataclass(frozen=True, slots=True)
class AttemptSummariesAvailable:
    page: AttemptSummaryPage

    def __post_init__(self) -> None:
        if type(self.page) is not AttemptSummaryPage:
            raise TypeError("CI economics summaries require an exact page")


@dataclass(frozen=True, slots=True)
class AttemptJobsAvailable:
    economics: AttemptEconomics
    jobs: tuple[WorkflowJobFact, ...]
    next_job_id: int | None

    def __post_init__(self) -> None:
        if type(self.economics) is not AttemptEconomics:
            raise TypeError("CI economics jobs require exact attempt economics")
        if type(self.jobs) is not tuple or any(
            type(job) is not WorkflowJobFact for job in self.jobs
        ):
            raise TypeError("CI economics job page requires exact job facts")
        if len(self.jobs) > MAX_ECONOMICS_PAGE_SIZE:
            raise ValueError("CI economics job page exceeds its cardinality bound")
        if any(job.attempt != self.economics.snapshot.attempt for job in self.jobs):
            raise ValueError("CI economics job page crosses attempt identity")
        if self.next_job_id is not None and (
            not self.jobs or self.next_job_id != self.jobs[-1].provider_job_id
        ):
            raise ValueError("CI economics job cursor does not identify its final item")


@dataclass(frozen=True, slots=True)
class CiEconomicsReadForbidden:
    pass


@dataclass(frozen=True, slots=True)
class CiEconomicsReadNotFound:
    pass


@dataclass(frozen=True, slots=True)
class CiEconomicsReadUnavailable:
    pass


type AttemptSummariesResult = (
    AttemptSummariesAvailable | CiEconomicsReadForbidden | CiEconomicsReadUnavailable
)
type ProviderSourcePageResult = (
    ProviderSourcePage | CiEconomicsReadForbidden | CiEconomicsReadUnavailable
)
type AttemptJobsResult = (
    AttemptJobsAvailable
    | CiEconomicsReadForbidden
    | CiEconomicsReadNotFound
    | CiEconomicsReadUnavailable
)
type AttemptMeasurementsResult = (
    RecordedAttemptEconomics
    | CiEconomicsReadForbidden
    | CiEconomicsReadNotFound
    | CiEconomicsReadUnavailable
)


class CiEconomicsReadUseCase(Protocol):
    async def list_provider_sources(
        self, *, actor: str, scope: RepositoryScope, after_cursor: str | None, limit: int
    ) -> ProviderSourcePageResult: ...

    async def load_measurements(
        self, *, actor: str, attempt: AttemptIdentity
    ) -> AttemptMeasurementsResult: ...

    async def list_attempts(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_cursor: str | None,
        limit: int,
    ) -> AttemptSummariesResult: ...

    async def load_attempt_jobs(
        self,
        *,
        actor: str,
        attempt: AttemptIdentity,
        after_job_id: int | None,
        limit: int,
    ) -> AttemptJobsResult: ...


class CiEconomicsReadService:
    """Authorize and sequence finite repository-scoped economics reads."""

    def __init__(
        self,
        *,
        authorizer: CiEconomicsAuthorizer,
        query: CiEconomicsQuery,
    ) -> None:
        self._authorizer = authorizer
        self._query = query

    async def list_provider_sources(
        self, *, actor: str, scope: RepositoryScope, after_cursor: str | None, limit: int
    ) -> ProviderSourcePageResult:
        require_catalog_page(scope, limit)
        cursor = None if after_cursor is None else decode_source_cursor(after_cursor)
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsReadForbidden()
        try:
            page = await self._query.list_provider_sources(
                scope, after_cursor=after_cursor, limit=limit
            )
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if (
            type(page) is not ProviderSourcePage
            or page.scope != scope
            or len(page.items) > limit
            or (cursor is not None and any(item.key >= cursor for item in page.items))
        ):
            return CiEconomicsReadUnavailable()
        return page

    async def load_measurements(
        self, *, actor: str, attempt: AttemptIdentity
    ) -> AttemptMeasurementsResult:
        if type(attempt) is not AttemptIdentity:
            raise TypeError("CI measurement read requires an exact attempt identity")
        if not await self._authorizer.allows_scope(actor=actor, scope=attempt.scope):
            return CiEconomicsReadForbidden()
        try:
            result = await self._query.load_measurements(attempt)
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if result is None:
            return CiEconomicsReadNotFound()
        if type(result) is not RecordedAttemptEconomics or result.attempt != attempt:
            return CiEconomicsReadUnavailable()
        return result

    async def list_attempts(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_cursor: str | None,
        limit: int,
    ) -> AttemptSummariesResult:
        _require_scope(scope)
        _require_page_limit(limit)
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsReadForbidden()
        try:
            page = await self._query.list_attempts(
                scope,
                after_cursor=after_cursor,
                limit=limit,
            )
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if any(item.attempt.scope != scope for item in page.items):
            return CiEconomicsReadUnavailable()
        return AttemptSummariesAvailable(page)

    async def load_attempt_jobs(
        self,
        *,
        actor: str,
        attempt: AttemptIdentity,
        after_job_id: int | None,
        limit: int,
    ) -> AttemptJobsResult:
        if type(attempt) is not AttemptIdentity:
            raise TypeError("CI economics read requires an exact attempt identity")
        _require_page_limit(limit)
        if after_job_id is not None and (type(after_job_id) is not int or after_job_id < 1):
            raise ValueError("CI economics job cursor must be positive or absent")
        if not await self._authorizer.allows_scope(actor=actor, scope=attempt.scope):
            return CiEconomicsReadForbidden()
        try:
            economics = await self._query.load_attempt(attempt)
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if economics is None:
            return CiEconomicsReadNotFound()
        if economics.snapshot.attempt != attempt:
            return CiEconomicsReadUnavailable()
        candidates = tuple(
            job
            for job in economics.snapshot.jobs
            if after_job_id is None or job.provider_job_id > after_job_id
        )
        page = candidates[: limit + 1]
        jobs = page[:limit]
        return AttemptJobsAvailable(
            economics=economics,
            jobs=jobs,
            next_job_id=jobs[-1].provider_job_id if len(page) > limit else None,
        )


@dataclass(frozen=True, slots=True)
class CiEconomicsCollectionRound:
    registered: int
    outcomes: tuple[CollectionItemOutcome, ...]

    def __post_init__(self) -> None:
        if type(self.registered) is not int or self.registered < 0:
            raise ValueError("registered collection count must be non-negative")
        if type(self.outcomes) is not tuple:
            raise TypeError("collection outcomes must be an exact tuple")
        if any(outcome not in _COLLECTION_ITEM_OUTCOME_SET for outcome in self.outcomes):
            raise ValueError("collection round contains an invalid outcome")


class CiEconomicsCollectionService:
    """Coordinate independent claims without owning provider or persistence policy."""

    def __init__(
        self,
        store: AttemptCollectionStore,
        provider: AttemptEvidenceProvider,
        profile: CiEconomicsProfile,
        *,
        runtime_metrics: RuntimeMetrics,
        worker_id: str,
    ) -> None:
        if type(profile) is not CiEconomicsProfile:
            raise TypeError("CI economics collection requires an exact profile")
        if type(runtime_metrics) is not RuntimeMetrics:
            raise TypeError("CI economics collection requires exact runtime metrics")
        if (
            type(worker_id) is not str
            or len(worker_id) != 64
            or any(character not in "0123456789abcdef" for character in worker_id)
        ):
            raise ValueError("CI economics worker id must be a lowercase SHA-256 digest")
        self._store = store
        self._provider = provider
        self._profile = profile
        self._runtime_metrics = runtime_metrics
        self._worker_id = worker_id

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        await self.run(abort_signal)

    async def run(self, abort_signal: asyncio.Event) -> CiEconomicsCollectionRound:
        registered = await self._store.register_eligible(
            limit=self._profile.maximum_claims_per_round
        )
        semaphore = asyncio.Semaphore(self._profile.maximum_concurrent_claims)
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(self._collect_one(semaphore, abort_signal))
                for _ in range(self._profile.maximum_claims_per_round)
            ]
        result = CiEconomicsCollectionRound(
            registered=registered,
            outcomes=tuple(task.result() for task in tasks),
        )
        self._runtime_metrics.ci_economics_collection_round(
            registered=result.registered,
            outcomes=result.outcomes,
        )
        return result

    async def _collect_one(
        self,
        semaphore: asyncio.Semaphore,
        abort_signal: asyncio.Event,
    ) -> CollectionItemOutcome:
        async with semaphore:
            if abort_signal.is_set():
                return "aborted"
            claim = await self._store.claim_next(worker_id=self._worker_id)
            if claim is None:
                return "none_due"
            try:
                observed = await self._provider.load_stable(claim.source)
            except asyncio.CancelledError:
                raise
            except Exception:
                transition = await self._store.defer_claim(claim, "unexpected_error")
                return "deferred" if transition == "applied" else "claim_lost"
            if isinstance(observed, ProviderAttemptDeferred):
                transition = await self._store.defer_claim(claim, observed.reason)
                return "deferred" if transition == "applied" else "claim_lost"
            try:
                recorded = await self._store.record_snapshot(claim, observed)
            except CiEconomicsEvidenceConflict:
                transition = await self._store.reject_claim(claim)
                return "terminal_conflict" if transition == "applied" else "claim_lost"
            return recorded


class CiEconomicsRetentionService:
    """Expose independent bounded retention operations to runtime lifecycle wiring."""

    def __init__(
        self,
        store: CiEconomicsRetentionStore,
        profile: CiEconomicsProfile,
    ) -> None:
        if type(profile) is not CiEconomicsProfile:
            raise TypeError("CI economics retention requires an exact profile")
        self._store = store
        self._batch_size = profile.cleanup_batch_size

    async def expire_evidence(self, abort_signal: asyncio.Event, /) -> None:
        if not abort_signal.is_set():
            await self._store.expire_evidence(limit=self._batch_size)

    async def purge_tombstones(self, abort_signal: asyncio.Event, /) -> None:
        if not abort_signal.is_set():
            await self._store.purge_tombstones(limit=self._batch_size)

    async def delete_expired_observations(self, abort_signal: asyncio.Event, /) -> None:
        if not abort_signal.is_set():
            await self._store.delete_expired_observations(limit=self._batch_size)


def _require_scope(scope: object) -> None:
    if type(scope) is not RepositoryScope:
        raise TypeError("CI economics read requires an exact repository scope")


def _require_page_limit(limit: object) -> None:
    if type(limit) is not int or not 1 <= limit <= MAX_ECONOMICS_PAGE_SIZE:
        raise ValueError("CI economics page size is outside its admitted bound")
