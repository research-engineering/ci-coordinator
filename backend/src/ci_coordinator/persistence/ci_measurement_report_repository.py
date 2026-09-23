from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import and_, func, literal, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.catalog import (
    MeasurementReportPage,
    MeasurementReportPointer,
    RecordedProviderSource,
    require_catalog_digest,
    require_catalog_page,
)
from ci_coordinator.ci_economics.ports import (
    CiEconomicsStoreUnavailable,
    MeasurementReportWriteResult,
)
from ci_coordinator.ci_economics.reports import (
    MAX_REPORTS_PER_ATTEMPT,
    JobMeasurementReport,
    MeasurementReportOrigin,
    StoredMeasurementReport,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import StrictJsonError
from ci_coordinator.persistence.canonical_row import require_digest
from ci_coordinator.persistence.ci_economics_budget_lock import lock_budget_scope
from ci_coordinator.persistence.ci_economics_budget_repository import (
    _PostgresBudgetPolicyRepository,
)
from ci_coordinator.persistence.ci_economics_budget_signals import _PostgresBudgetSignalRepository
from ci_coordinator.persistence.ci_economics_collection_codec import (
    decode_collection_source,
    decode_collection_state,
)
from ci_coordinator.persistence.ci_measurement_report_codec import (
    decode_measurement_report,
    encode_measurement_report,
)
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.schema import (
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
)


class _PostgresCiMeasurementReportRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
        *,
        budget_policies: _PostgresBudgetPolicyRepository,
        budget_signals: _PostgresBudgetSignalRepository,
    ) -> None:
        self._connection = connection
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required
        self._budget_policies = budget_policies
        self._budget_signals = budget_signals

    async def list_measurement_reports(
        self, scope: RepositoryScope, source_id: str, *, after_cursor: str | None, limit: int
    ) -> MeasurementReportPage | None:
        self._ensure_active()
        require_catalog_page(scope, limit)
        require_catalog_digest(source_id)
        if after_cursor is not None:
            require_catalog_digest(after_cursor)
        source, report = ci_workflow_attempt_collections.c, ci_job_measurement_reports.c
        report_match = and_(
            report.subject_id == source.subject_id,
            report.source_kind == "provider_run",
            report.retain_until == source.evidence_retain_until,
            report.retain_until > func.statement_timestamp(),
        )
        if after_cursor is not None:
            report_match = and_(report_match, report.report_id > after_cursor)
        statement = (
            select(
                ci_workflow_attempt_collections,
                report.report_id,
                report.report_digest,
                report.received_at,
                report.retain_until,
            )
            .outerjoin(ci_job_measurement_reports, report_match)
            .where(
                source.subject_id == source_id,
                source.source_kind == "provider_run",
                source.installation_id == scope.installation_id,
                source.repository_id == scope.repository_id,
                source.status != "expired",
                source.evidence_retain_until > func.statement_timestamp(),
            )
            .order_by(report.report_id)
            .limit(limit + 1)
        )
        try:
            rows = (await self._connection.execute(statement)).mappings().all()
            if not rows:
                return None
            first = dict(rows[0])
            provenance = decode_collection_source(first)
            if type(provenance) is not ProviderRunCollectionSource:
                raise ValueError("report catalog returned another source kind")
            recorded = RecordedProviderSource(provenance, decode_collection_state(first))
            items: list[MeasurementReportPointer] = []
            for row in rows[:limit]:
                if row["report_id"] is None:
                    if len(rows) != 1 or any(
                        row[name] is not None
                        for name in ("report_digest", "received_at", "retain_until")
                    ):
                        raise ValueError("empty report catalog has contradictory metadata")
                    continue
                received_at, retain_until = row["received_at"], row["retain_until"]
                if type(received_at) is not datetime or type(retain_until) is not datetime:
                    raise ValueError("report catalog has invalid receipt or retention time")
                if retain_until != recorded.state.evidence_retain_until:
                    raise ValueError("report catalog crosses its source retention")
                items.append(
                    MeasurementReportPointer(
                        require_digest(row["report_id"], "report catalog identity"),
                        require_digest(row["report_digest"], "report catalog digest"),
                        received_at,
                        retain_until,
                    )
                )
            next_cursor = items[-1].report_id if len(rows) > limit else None
            return MeasurementReportPage(provenance, tuple(items), next_cursor)
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable(
                "measurement report catalog is unavailable"
            ) from error

    async def record_measurement_report(
        self,
        source: ProviderRunCollectionSource,
        report: JobMeasurementReport,
        origin: MeasurementReportOrigin,
    ) -> MeasurementReportWriteResult:
        self._ensure_active()
        if (
            type(source) is not ProviderRunCollectionSource
            or type(report) is not JobMeasurementReport
        ):
            raise TypeError("report write requires an exact source and payload")
        if type(origin) is not MeasurementReportOrigin:
            raise TypeError("report write requires an exact receiver observation")
        if source.attempt != report.attempt:
            raise ValueError("report write crosses its source attempt")
        try:
            source_row = (
                (
                    await self._connection.execute(
                        select(ci_workflow_attempt_collections)
                        .where(ci_workflow_attempt_collections.c.subject_id == source.source_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if source_row is None or source_row["source_kind"] != "provider_run":
                return "source_unavailable"
            if decode_collection_source(dict(source_row)) != source:
                return "source_unavailable"
            now = await self._database_now()
            expires = source_row["evidence_retain_until"]
            if type(expires) is not datetime:
                raise CiEconomicsStoreUnavailable("report source has no retention instant")
            if now >= expires or source_row["status"] == "expired":
                return "outside_retention"
            existing = (
                (
                    await self._connection.execute(
                        select(ci_job_measurement_reports).where(
                            ci_job_measurement_reports.c.report_id == report.report_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                recorded = decode_measurement_report(dict(existing), source)
                if recorded.retain_until != expires:
                    raise CiEconomicsStoreUnavailable("report retention differs from its source")
                return "replayed" if recorded.report == report else "report_conflict"
            existing_slots = (
                select(ci_job_measurement_reports.c.report_id)
                .where(ci_job_measurement_reports.c.subject_id == source.source_id)
                .limit(MAX_REPORTS_PER_ATTEMPT)
                .subquery()
            )
            count = await self._connection.scalar(select(func.count()).select_from(existing_slots))
            if type(count) is not int:
                raise CiEconomicsStoreUnavailable("report quota could not be observed")
            if count >= MAX_REPORTS_PER_ATTEMPT:
                return "capacity_reached"
            await lock_budget_scope(self._connection, source.attempt.scope, shared=True)
            policies = await self._budget_policies.list_policies(source.attempt.scope)
            now = await self._database_now()
            if now >= expires:
                return "outside_retention"
            record = StoredMeasurementReport(report, source, origin, now, expires)
            values = encode_measurement_report(record)
            admitted_values = select(
                *(
                    literal(value, type_=ci_job_measurement_reports.c[name].type)
                    for name, value in values.items()
                )
            ).where(func.statement_timestamp() < expires)
            inserted = await self._connection.scalar(
                postgres_insert(ci_job_measurement_reports)
                .from_select(list(values), admitted_values)
                .returning(ci_job_measurement_reports.c.report_id)
            )
            if inserted != report.report_id:
                return "outside_retention"
            await self._budget_signals.record_signals(policies, record)
            return "recorded"
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CiEconomicsStoreUnavailable:
            self._mark_rollback_required()
            raise
        except (PersistenceError, SQLAlchemyError, StrictJsonError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise CiEconomicsStoreUnavailable(
                "CI measurement report write is unavailable"
            ) from error

    async def load_measurement_report(
        self, scope: RepositoryScope, report_id: str
    ) -> StoredMeasurementReport | None:
        self._ensure_active()
        if type(scope) is not RepositoryScope:
            raise TypeError("report read requires an exact repository scope")
        require_digest(report_id, "measurement report identity")
        try:
            row = (
                (
                    await self._connection.execute(
                        select(ci_job_measurement_reports)
                        .join(
                            ci_workflow_attempt_collections,
                            ci_job_measurement_reports.c.subject_id
                            == ci_workflow_attempt_collections.c.subject_id,
                        )
                        .where(
                            ci_job_measurement_reports.c.report_id == report_id,
                            ci_job_measurement_reports.c.retain_until > func.statement_timestamp(),
                            ci_workflow_attempt_collections.c.installation_id
                            == scope.installation_id,
                            ci_workflow_attempt_collections.c.repository_id == scope.repository_id,
                            ci_workflow_attempt_collections.c.source_kind == "provider_run",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            source_row = (
                (
                    await self._connection.execute(
                        select(ci_workflow_attempt_collections).where(
                            ci_workflow_attempt_collections.c.subject_id == row["subject_id"]
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if source_row is None or source_row["evidence_retain_until"] != row["retain_until"]:
                raise CiEconomicsStoreUnavailable("report source disappeared or changed retention")
            source = decode_collection_source(dict(source_row))
            if type(source) is not ProviderRunCollectionSource or source.attempt.scope != scope:
                raise CiEconomicsStoreUnavailable("report source crosses its repository scope")
            return decode_measurement_report(dict(row), source)
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, StrictJsonError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable(
                "CI measurement report read is unavailable"
            ) from error

    async def _database_now(self) -> datetime:
        now = await self._connection.scalar(select(func.statement_timestamp()))
        if type(now) is not datetime:
            raise CiEconomicsStoreUnavailable("database time is unavailable for reports")
        return now
