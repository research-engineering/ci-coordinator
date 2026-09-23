from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.api.http.ci_economics_source_contracts import EconomicsSourceScopeBody
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.ci_economics.discovery import MAX_DISCOVERY_PAGES, RunDiscoveryWindow
from ci_coordinator.ci_economics.observation_commands import ConfigureObservation
from ci_coordinator.ci_economics.observation_gaps import (
    MAX_OBSERVATION_GAP_PAGE_SIZE,
    OBSERVATION_GAP_CURSOR_PATTERN,
)
from ci_coordinator.ci_economics.observation_payload import (
    ObservationConfigurationPayload,
    ObservationDigest,
    ObservationGapPayload,
    ObservationPositiveId,
    ObservationSnapshotPayload,
)
from ci_coordinator.ci_economics.observation_ports import ObservationGapPage, ObservationStatus
from ci_coordinator.ci_economics.observation_progress import (
    ObservationOutcome,
    ObservationScanProgress,
)
from ci_coordinator.ci_economics.observation_scan import ObservationLane
from ci_coordinator.ci_economics.observation_workflows import (
    MAX_OBSERVATION_WORKFLOW_PAGES,
    OBSERVATION_WORKFLOW_PAGE_SIZE,
    ObservationWorkflowPage,
)
from ci_coordinator.ci_economics.sources import MAX_PROVIDER_SOURCES_PER_REPOSITORY
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type ObservationOperationId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
]
type ObservationCounter = Annotated[int, Field(ge=0, le=MAX_SAFE_JSON_INTEGER)]
type ObservationCursor = Annotated[
    str, Field(max_length=115, pattern=OBSERVATION_GAP_CURSOR_PATTERN)
]


class ConfigureObservationBody(EconomicsSourceScopeBody):
    expected_revision: int = Field(
        validation_alias="expectedRevision", ge=0, lt=MAX_SAFE_JSON_INTEGER
    )
    configuration: ObservationConfigurationPayload
    operation_id: ObservationOperationId = Field(validation_alias="operationId")

    def to_command(self, actor: str) -> ConfigureObservation:
        return ConfigureObservation(
            self.scope,
            self.expected_revision,
            self.configuration.to_configuration(),
            self.operation_id,
            actor,
        )


class ObservationMutationResponse(ResponseModel):
    schema_version: Literal["ci-economics-observation-mutation/v1"] = (
        "ci-economics-observation-mutation/v1"
    )
    operation_id: ObservationOperationId
    outcome: Literal[
        "committed", "replayed", "revision_conflict", "operation_conflict", "capacity_reached"
    ]
    snapshot: ObservationSnapshotPayload | None

    @model_validator(mode="after")
    def admit_outcome(self) -> Self:
        if (self.outcome in {"committed", "replayed"}) != (self.snapshot is not None):
            raise ValueError("observation receipt outcome contradicts its snapshot")
        return self


class ObservationWindowResponse(ResponseModel):
    created_from: datetime
    created_through: datetime


class ObservationScanResponse(ResponseModel):
    lane: ObservationLane
    interval: ObservationWindowResponse | None
    window: ObservationWindowResponse | None
    page_number: Annotated[int, Field(ge=1, le=MAX_DISCOVERY_PAGES)] | None
    cycle_started_at: datetime | None
    next_attempt_at: datetime
    lease_expires_at: datetime | None
    last_completed_through: datetime | None
    last_page_at: datetime | None
    pages_seen: ObservationCounter
    sources_registered: ObservationCounter
    last_outcome: ObservationOutcome | None


class ObservationStatusResponse(ResponseModel):
    schema_version: Literal["ci-economics-observation-status/v1"] = (
        "ci-economics-observation-status/v1"
    )
    installation_id: ObservationPositiveId
    repository_id: ObservationPositiveId
    snapshot: ObservationSnapshotPayload | None
    scans: tuple[ObservationScanResponse, ...] = Field(max_length=2)
    occupied_source_slots: int = Field(ge=0, le=MAX_PROVIDER_SOURCES_PER_REPOSITORY)
    maximum_source_slots: Literal[10000] = MAX_PROVIDER_SOURCES_PER_REPOSITORY
    detail_truncated_until: datetime | None
    observed_at: datetime


class ObservationGapResponse(ResponseModel):
    gap_id: ObservationDigest
    gap: ObservationGapPayload
    expires_at: datetime


class ObservationGapsResponse(ResponseModel):
    schema_version: Literal["ci-economics-observation-gaps/v1"] = "ci-economics-observation-gaps/v1"
    installation_id: ObservationPositiveId
    repository_id: ObservationPositiveId
    items: tuple[ObservationGapResponse, ...] = Field(max_length=MAX_OBSERVATION_GAP_PAGE_SIZE)
    next_cursor: ObservationCursor | None
    observed_at: datetime


class ObservationWorkflowResponse(ResponseModel):
    workflow_id: ObservationPositiveId
    name: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=1024)
    state: str = Field(min_length=1, max_length=64)


class ObservationWorkflowsResponse(ResponseModel):
    schema_version: Literal["ci-economics-observation-workflows/v1"] = (
        "ci-economics-observation-workflows/v1"
    )
    installation_id: ObservationPositiveId
    repository_id: ObservationPositiveId
    page_number: int = Field(ge=1, le=MAX_OBSERVATION_WORKFLOW_PAGES)
    provider_total: ObservationCounter
    items: tuple[ObservationWorkflowResponse, ...] = Field(
        max_length=OBSERVATION_WORKFLOW_PAGE_SIZE
    )
    termination: Literal["next_page", "exhausted", "truncated"]


def observation_workflows_response(page: ObservationWorkflowPage) -> ObservationWorkflowsResponse:
    return ObservationWorkflowsResponse(
        installation_id=page.scope.installation_id,
        repository_id=page.scope.repository_id,
        page_number=page.page_number,
        provider_total=page.provider_total,
        items=tuple(
            ObservationWorkflowResponse(
                workflow_id=item.workflow_id, name=item.name, path=item.path, state=item.state
            )
            for item in page.workflows
        ),
        termination=page.termination,
    )


def observation_status_response(status: ObservationStatus) -> ObservationStatusResponse:
    return ObservationStatusResponse(
        installation_id=status.scope.installation_id,
        repository_id=status.scope.repository_id,
        snapshot=None
        if status.snapshot is None
        else ObservationSnapshotPayload.model_validate(status.snapshot.canonical_mapping()),
        scans=tuple(_scan_response(scan) for scan in status.scans),
        occupied_source_slots=status.occupied_source_slots,
        detail_truncated_until=status.detail_truncated_until,
        observed_at=status.observed_at,
    )


def observation_gaps_response(page: ObservationGapPage) -> ObservationGapsResponse:
    return ObservationGapsResponse(
        installation_id=page.scope.installation_id,
        repository_id=page.scope.repository_id,
        items=tuple(
            ObservationGapResponse(
                gap_id=gap.gap_id,
                gap=ObservationGapPayload.model_validate(gap.canonical_mapping()),
                expires_at=gap.expires_at,
            )
            for gap in page.gaps
        ),
        next_cursor=page.next_cursor,
        observed_at=page.observed_at,
    )


def _window_response(window: RunDiscoveryWindow) -> ObservationWindowResponse:
    return ObservationWindowResponse(
        created_from=window.created_from, created_through=window.created_through
    )


def _scan_response(progress: ObservationScanProgress) -> ObservationScanResponse:
    state = progress.state
    cursor = state.cursor
    return ObservationScanResponse(
        lane=state.lane,
        interval=None if cursor is None else _window_response(cursor.interval),
        window=None if cursor is None else _window_response(cursor.window),
        page_number=None if cursor is None else cursor.page_number,
        cycle_started_at=None if cursor is None else cursor.cycle_started_at,
        next_attempt_at=state.next_attempt_at,
        lease_expires_at=None if state.lease is None else state.lease.expires_at,
        last_completed_through=progress.last_completed_through,
        last_page_at=progress.last_page_at,
        pages_seen=progress.pages_seen,
        sources_registered=progress.sources_registered,
        last_outcome=progress.last_outcome,
    )
