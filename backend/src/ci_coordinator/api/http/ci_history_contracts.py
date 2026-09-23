from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.ci_economics.archive_retention import DetailPolicySource
from ci_coordinator.ci_economics.archive_retention_payload import (
    DETAIL_RETENTION_ADAPTER,
    DetailRetentionPayload,
)
from ci_coordinator.ci_economics.discovery import MAX_DISCOVERY_PAGES
from ci_coordinator.ci_economics.history_administration import HistoryStatus
from ci_coordinator.ci_economics.history_payload import HistoryDatasetPayload
from ci_coordinator.ci_economics.history_rechecks import MAX_HISTORY_RECHECK_RUNS
from ci_coordinator.ci_economics.history_scan import HistoryScanOutcome, HistoryScanState
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type HistoryCounter = Annotated[int, Field(ge=0, le=MAX_SAFE_JSON_INTEGER)]


class HistoryMutationResponse(ResponseModel):
    schema_version: Literal["ci-economics-history-mutation/v1"] = "ci-economics-history-mutation/v1"
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    outcome: Literal[
        "committed",
        "replayed",
        "revision_conflict",
        "operation_conflict",
        "capacity_reached",
        "dataset_fenced",
        "invalid_population",
    ]
    snapshot: HistoryDatasetPayload | None

    @model_validator(mode="after")
    def admit_outcome(self) -> Self:
        if (self.outcome in {"committed", "replayed"}) != (self.snapshot is not None):
            raise ValueError("history mutation outcome contradicts its snapshot")
        return self


class HistoryDefaultsResponse(ResponseModel):
    revision: ObservationPositiveId
    detail_retention: DetailRetentionPayload
    updated_at: datetime


class HistoryEffectivePolicyResponse(ResponseModel):
    source: DetailPolicySource
    revision: ObservationPositiveId
    policy: DetailRetentionPayload


class HistoryScanResponse(ResponseModel):
    revision: ObservationPositiveId
    created_from: datetime
    created_through: datetime
    window_from: datetime
    window_through: datetime
    page_number: int = Field(ge=1, le=MAX_DISCOVERY_PAGES)
    cycle_started_at: datetime
    next_attempt_at: datetime
    lease_expires_at: datetime | None
    pages_seen: HistoryCounter
    attempts_seen: HistoryCounter
    last_outcome: HistoryScanOutcome | None
    traversal_complete: bool


class HistoryDiscoveryResponse(ResponseModel):
    recovery_floor: datetime
    completed_through: datetime | None
    pending_runs: int = Field(ge=0, le=100)
    progress: HistoryScanResponse


class HistoryStatusResponse(ResponseModel):
    schema_version: Literal["ci-economics-history-status/v1"] = "ci-economics-history-status/v1"
    installation_id: ObservationPositiveId
    repository_id: ObservationPositiveId
    defaults: HistoryDefaultsResponse
    effective_detail_retention: HistoryEffectivePolicyResponse | None
    snapshot: HistoryDatasetPayload | None
    scan: HistoryScanResponse | None
    discovery: HistoryDiscoveryResponse | None
    pending_rechecks: int = Field(ge=0, le=MAX_HISTORY_RECHECK_RUNS)
    observed_at: datetime


def history_status_response(status: HistoryStatus) -> HistoryStatusResponse:
    dataset, scan = status.dataset, status.scan
    effective = None
    if dataset is not None:
        policy, reference = dataset.resolve_detail_policy(status.defaults)
        effective = HistoryEffectivePolicyResponse(
            source=reference.source,
            revision=reference.revision,
            policy=DETAIL_RETENTION_ADAPTER.validate_python(policy.canonical_mapping()),
        )
    discovery = status.discovery
    recent = None
    if discovery is not None and discovery.recent is not None:
        pending = discovery.checkpoint.pending
        recent = HistoryDiscoveryResponse(
            recovery_floor=discovery.recent.recovery_floor,
            completed_through=discovery.recent.completed_through,
            pending_runs=0
            if pending is None
            else len(pending.observed.page.sources) - pending.run_index,
            progress=_scan_response(discovery),
        )
    return HistoryStatusResponse(
        installation_id=status.scope.installation_id,
        repository_id=status.scope.repository_id,
        defaults=HistoryDefaultsResponse(
            revision=status.defaults.revision,
            detail_retention=DETAIL_RETENTION_ADAPTER.validate_python(
                status.defaults.detail_retention.canonical_mapping()
            ),
            updated_at=status.defaults.updated_at,
        ),
        effective_detail_retention=effective,
        snapshot=None
        if dataset is None
        else HistoryDatasetPayload.model_validate(dataset.canonical_mapping()),
        scan=None if scan is None else _scan_response(scan),
        discovery=recent,
        pending_rechecks=status.pending_rechecks,
        observed_at=status.observed_at,
    )


def _scan_response(scan: HistoryScanState) -> HistoryScanResponse:
    cursor = scan.checkpoint.cursor
    return HistoryScanResponse(
        revision=scan.revision,
        created_from=cursor.created_from,
        created_through=cursor.created_through,
        window_from=cursor.window.created_from,
        window_through=cursor.window.created_through,
        page_number=cursor.page_number,
        cycle_started_at=cursor.cycle_started_at,
        next_attempt_at=scan.next_attempt_at,
        lease_expires_at=None if scan.lease is None else scan.lease.expires_at,
        pages_seen=scan.pages_seen,
        attempts_seen=scan.attempts_seen,
        last_outcome=scan.last_outcome,
        traversal_complete=scan.checkpoint.complete,
    )
