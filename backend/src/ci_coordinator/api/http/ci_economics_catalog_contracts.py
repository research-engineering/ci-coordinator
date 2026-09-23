from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from ci_coordinator.api.http.ci_economics_source_contracts import (
    EconomicsProviderSourceResponse,
    provider_source_response,
)
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.ci_economics.catalog import (
    MeasurementReportPage,
    ProviderSourcePage,
    decode_source_cursor,
)
from ci_coordinator.ci_economics.collection import (
    MAX_COLLECTION_ATTEMPTS,
    CollectionFailureReason,
    CollectionStatus,
    CollectionTerminalReason,
)
from ci_coordinator.ci_economics.model import MAX_ECONOMICS_PAGE_SIZE
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


def _canonical_source_cursor(value: str) -> str:
    decode_source_cursor(value)
    return value


type SourceCursorQuery = Annotated[str, AfterValidator(_canonical_source_cursor)]


class EconomicsSourceCollectionResponse(ResponseModel):
    source: EconomicsProviderSourceResponse
    status: CollectionStatus
    attempt_count: int = Field(ge=0, le=MAX_COLLECTION_ATTEMPTS)
    max_attempts: int = Field(ge=1, le=MAX_COLLECTION_ATTEMPTS)
    next_attempt_at: datetime | None
    last_failure_reason: CollectionFailureReason | None
    terminal_reason: CollectionTerminalReason | None
    completed_at: datetime | None
    retain_until: datetime


class EconomicsSourcePageResponse(ResponseModel):
    schema_version: Literal["ci-economics-source-page/v2"]
    ok: Literal[True]
    installation_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    repository_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    items: tuple[EconomicsSourceCollectionResponse, ...] = Field(max_length=MAX_ECONOMICS_PAGE_SIZE)
    next_cursor: str | None = Field(min_length=3, max_length=33)


class MeasurementReportPointerResponse(ResponseModel):
    report_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    report_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    received_at: datetime
    retain_until: datetime


class MeasurementReportPageResponse(ResponseModel):
    schema_version: Literal["ci-measurement-report-page/v2"]
    ok: Literal[True]
    source: EconomicsProviderSourceResponse
    items: tuple[MeasurementReportPointerResponse, ...] = Field(max_length=MAX_ECONOMICS_PAGE_SIZE)
    next_cursor: str | None = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


def source_page_response(page: ProviderSourcePage) -> EconomicsSourcePageResponse:
    return EconomicsSourcePageResponse(
        schema_version="ci-economics-source-page/v2",
        ok=True,
        installation_id=page.scope.installation_id,
        repository_id=page.scope.repository_id,
        items=tuple(
            EconomicsSourceCollectionResponse(
                source=provider_source_response(item.source),
                status=item.state.status,
                attempt_count=item.state.attempt_count,
                max_attempts=item.state.max_attempts,
                next_attempt_at=item.state.next_attempt_at,
                last_failure_reason=item.state.last_failure_reason,
                terminal_reason=item.state.terminal_reason,
                completed_at=item.state.completed_at,
                retain_until=item.state.evidence_retain_until,
            )
            for item in page.items
        ),
        next_cursor=page.next_cursor,
    )


def report_page_response(page: MeasurementReportPage) -> MeasurementReportPageResponse:
    return MeasurementReportPageResponse(
        schema_version="ci-measurement-report-page/v2",
        ok=True,
        source=provider_source_response(page.source),
        items=tuple(
            MeasurementReportPointerResponse(
                report_id=item.report_id,
                report_digest=item.report_digest,
                received_at=item.received_at,
                retain_until=item.retain_until,
            )
            for item in page.items
        ),
        next_cursor=page.next_cursor,
    )


def catalog_query_is_admitted(pairs: list[tuple[str, str]]) -> bool:
    names = [name for name, _ in pairs]
    return (
        len(names) == len(set(names))
        and set(names) <= {"afterCursor", "limit"}
        and all(
            1 <= len(value) <= 3 and value.isascii() and value.isdecimal() and value[0] != "0"
            for name, value in pairs
            if name == "limit"
        )
    )
