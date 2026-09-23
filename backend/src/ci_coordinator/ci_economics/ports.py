"""Process-boundary ports for CI economics evidence and bounded reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ci_coordinator.ci_economics.catalog import MeasurementReportPage, ProviderSourcePage
from ci_coordinator.ci_economics.collection import (
    CollectionClaim,
    ProviderCollectionFailureReason,
    RetryableCollectionFailureReason,
)
from ci_coordinator.ci_economics.discovery import ProviderRunDiscoveryPage, RunDiscoveryWindow
from ci_coordinator.ci_economics.model import (
    AttemptEconomics,
    AttemptIdentity,
    AttemptSummaryPage,
    ProviderAttemptSnapshot,
)
from ci_coordinator.ci_economics.read_models import RecordedAttemptEconomics
from ci_coordinator.ci_economics.report_ingestion import ProviderReportJobBinding
from ci_coordinator.ci_economics.reports import (
    JobMeasurementReport,
    MeasurementReportOrigin,
    StoredMeasurementReport,
)
from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
)
from ci_coordinator.config_control import RepositoryScope

type ClaimTransitionResult = Literal["applied", "claim_lost"]
type SnapshotRecordResult = Literal["captured", "claim_lost"]
type MeasurementReportWriteResult = Literal[
    "recorded",
    "replayed",
    "report_conflict",
    "source_unavailable",
    "outside_retention",
    "capacity_reached",
]


class CiEconomicsStoreUnavailable(RuntimeError):
    """Durable CI economics evidence is temporarily unavailable."""


class CiEconomicsEvidenceConflict(RuntimeError):
    """One immutable evidence identity has incompatible observed facts."""


@dataclass(frozen=True, slots=True)
class ProviderAttemptDeferred:
    reason: ProviderCollectionFailureReason

    def __post_init__(self) -> None:
        if self.reason not in {
            "provider_unavailable",
            "provider_binding_mismatch",
            "provider_malformed",
            "provider_incomplete",
            "provider_not_terminal",
            "provider_unstable",
        }:
            raise ValueError("provider attempt deferral requires a reason")


class AttemptEvidenceProvider(Protocol):
    async def load_stable(
        self,
        source: CollectionSource,
    ) -> ProviderAttemptSnapshot | ProviderAttemptDeferred: ...


class ProviderSourceRegistration(Protocol):
    async def register_provider_source(
        self, source: ProviderRunCollectionSource
    ) -> ProviderSourceRegistrationResult: ...


class ProviderSourceResolver(Protocol):
    async def discover_page(
        self,
        scope: RepositoryScope,
        window: RunDiscoveryWindow,
        *,
        page_number: int,
    ) -> ProviderRunDiscoveryPage | ProviderAttemptDeferred: ...

    async def resolve_attempt(
        self,
        scope: RepositoryScope,
        workflow_run_id: int,
        run_attempt: int,
    ) -> ProviderRunCollectionSource | ProviderAttemptDeferred: ...


class AttemptCollectionStore(Protocol):
    async def register_eligible(self, *, limit: int) -> int: ...

    async def claim_next(self, *, worker_id: str) -> CollectionClaim | None: ...

    async def defer_claim(
        self,
        claim: CollectionClaim,
        reason: RetryableCollectionFailureReason,
    ) -> ClaimTransitionResult: ...

    async def record_snapshot(
        self,
        claim: CollectionClaim,
        snapshot: ProviderAttemptSnapshot,
    ) -> SnapshotRecordResult: ...

    async def reject_claim(self, claim: CollectionClaim) -> ClaimTransitionResult: ...


class CiEconomicsQuery(Protocol):
    async def list_provider_sources(
        self, scope: RepositoryScope, *, after_cursor: str | None, limit: int
    ) -> ProviderSourcePage: ...

    async def list_attempts(
        self,
        scope: RepositoryScope,
        *,
        after_cursor: str | None,
        limit: int,
    ) -> AttemptSummaryPage: ...

    async def load_attempt(
        self,
        attempt: AttemptIdentity,
    ) -> AttemptEconomics | None: ...

    async def load_measurements(
        self, attempt: AttemptIdentity
    ) -> RecordedAttemptEconomics | None: ...


class CiEconomicsRetentionStore(Protocol):
    async def expire_evidence(self, *, limit: int) -> int: ...

    async def purge_tombstones(self, *, limit: int) -> int: ...

    async def delete_expired_observations(self, *, limit: int) -> int: ...


class MeasurementReportLookup(Protocol):
    async def load_measurement_report(
        self, scope: RepositoryScope, report_id: str
    ) -> StoredMeasurementReport | None: ...


class MeasurementReportQuery(MeasurementReportLookup, Protocol):
    async def list_measurement_reports(
        self, scope: RepositoryScope, source_id: str, *, after_cursor: str | None, limit: int
    ) -> MeasurementReportPage | None: ...


class MeasurementReportJobProvider(Protocol):
    async def bind_job(
        self, report: JobMeasurementReport, *, repository: str, page_number: int
    ) -> ProviderReportJobBinding | ProviderAttemptDeferred: ...


class MeasurementReportStore(MeasurementReportLookup, Protocol):
    async def record_measurement_report(
        self,
        source: ProviderRunCollectionSource,
        report: JobMeasurementReport,
        origin: MeasurementReportOrigin,
    ) -> MeasurementReportWriteResult: ...
