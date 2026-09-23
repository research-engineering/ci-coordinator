"""Transaction-scoped adapter for the long-lived CI economics capability."""

from __future__ import annotations

from collections.abc import Callable

from ci_coordinator.ci_economics import (
    AttemptEconomics,
    AttemptIdentity,
    AttemptSummaryPage,
    CiEconomicsStoreUnavailable,
    ClaimTransitionResult,
    CollectionClaim,
    ProviderAttemptSnapshot,
    RetryableCollectionFailureReason,
    SnapshotRecordResult,
)
from ci_coordinator.ci_economics.catalog import MeasurementReportPage, ProviderSourcePage
from ci_coordinator.ci_economics.ports import MeasurementReportWriteResult
from ci_coordinator.ci_economics.read_models import RecordedAttemptEconomics
from ci_coordinator.ci_economics.reports import (
    JobMeasurementReport,
    MeasurementReportOrigin,
    StoredMeasurementReport,
)
from ci_coordinator.ci_economics.sources import (
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_economics_unit_of_work import (
    PostgresCiEconomicsUnitOfWork,
)
from ci_coordinator.persistence.errors import PersistenceError

type CiEconomicsUnitOfWorkFactory = Callable[[], PostgresCiEconomicsUnitOfWork]


class TransactionalCiEconomicsStore:
    def __init__(self, unit_of_work: CiEconomicsUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def list_provider_sources(
        self, scope: RepositoryScope, *, after_cursor: str | None, limit: int
    ) -> ProviderSourcePage:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.ci_economics.list_provider_sources(
                    scope, after_cursor=after_cursor, limit=limit
                )
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("provider source catalog is unavailable") from error

    async def list_measurement_reports(
        self, scope: RepositoryScope, source_id: str, *, after_cursor: str | None, limit: int
    ) -> MeasurementReportPage | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.ci_measurement_reports.list_measurement_reports(
                    scope, source_id, after_cursor=after_cursor, limit=limit
                )
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable(
                "measurement report catalog is unavailable"
            ) from error

    async def record_measurement_report(
        self,
        source: ProviderRunCollectionSource,
        report: JobMeasurementReport,
        origin: MeasurementReportOrigin,
    ) -> MeasurementReportWriteResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.ci_measurement_reports.record_measurement_report(
                    source, report, origin
                )
                if result == "recorded":
                    await transaction.commit()
                return result
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable(
                "CI measurement report write is unavailable"
            ) from error

    async def load_measurement_report(
        self, scope: RepositoryScope, report_id: str
    ) -> StoredMeasurementReport | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.ci_measurement_reports.load_measurement_report(
                    scope, report_id
                )
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable(
                "CI measurement report read is unavailable"
            ) from error

    async def register_eligible(self, *, limit: int) -> int:
        try:
            async with self._unit_of_work() as transaction:
                registered = await transaction.ci_economics_collection.register_eligible(
                    limit=limit
                )
                await transaction.commit()
                return registered
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable(
                "CI collection registration is unavailable"
            ) from error

    async def claim_next(self, *, worker_id: str) -> CollectionClaim | None:
        try:
            async with self._unit_of_work() as transaction:
                claim = await transaction.ci_economics_collection.claim_next(worker_id=worker_id)
                await transaction.commit()
                return claim
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI collection claim is unavailable") from error

    async def register_provider_source(
        self, source: ProviderRunCollectionSource
    ) -> ProviderSourceRegistrationResult:
        try:
            async with self._unit_of_work() as transaction:
                outcome = await transaction.ci_economics_collection.register_provider_source(source)
                if outcome == "registered":
                    await transaction.commit()
                return outcome
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable(
                "provider source registration is unavailable"
            ) from error

    async def defer_claim(
        self,
        claim: CollectionClaim,
        reason: RetryableCollectionFailureReason,
    ) -> ClaimTransitionResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.ci_economics_collection.defer_claim(claim, reason)
                if result == "applied":
                    await transaction.commit()
                return result
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI collection deferral is unavailable") from error

    async def record_snapshot(
        self,
        claim: CollectionClaim,
        snapshot: ProviderAttemptSnapshot,
    ) -> SnapshotRecordResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.ci_economics_collection.record_snapshot(
                    claim,
                    snapshot,
                )
                if result != "claim_lost":
                    await transaction.commit()
                return result
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI attempt snapshot is unavailable") from error

    async def reject_claim(self, claim: CollectionClaim) -> ClaimTransitionResult:
        try:
            async with self._unit_of_work() as transaction:
                result = await transaction.ci_economics_collection.reject_claim(claim)
                if result == "applied":
                    await transaction.commit()
                return result
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI collection rejection is unavailable") from error

    async def list_attempts(
        self,
        scope: RepositoryScope,
        *,
        after_cursor: str | None,
        limit: int,
    ) -> AttemptSummaryPage:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.ci_economics.list_attempts(
                    scope,
                    after_cursor=after_cursor,
                    limit=limit,
                )
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI economics page is unavailable") from error

    async def load_attempt(
        self,
        attempt: AttemptIdentity,
    ) -> AttemptEconomics | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.ci_economics.load_attempt(attempt)
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI attempt economics are unavailable") from error

    async def load_measurements(self, attempt: AttemptIdentity) -> RecordedAttemptEconomics | None:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.ci_economics.load_measurements(attempt)
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("retained CI measurements are unavailable") from error

    async def expire_evidence(self, *, limit: int) -> int:
        try:
            async with self._unit_of_work() as transaction:
                deleted = await transaction.ci_economics_collection.expire_evidence(limit=limit)
                await transaction.commit()
                return deleted
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI evidence expiry is unavailable") from error

    async def purge_tombstones(self, *, limit: int) -> int:
        try:
            async with self._unit_of_work() as transaction:
                deleted = await transaction.ci_economics_collection.purge_tombstones(limit=limit)
                await transaction.commit()
                return deleted
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI tombstone purge is unavailable") from error

    async def delete_expired_observations(self, *, limit: int) -> int:
        try:
            async with self._unit_of_work() as transaction:
                deleted = await transaction.ci_economics.delete_expired_observations(limit=limit)
                await transaction.commit()
                return deleted
        except PersistenceError as error:
            raise CiEconomicsStoreUnavailable("CI observation cleanup is unavailable") from error
