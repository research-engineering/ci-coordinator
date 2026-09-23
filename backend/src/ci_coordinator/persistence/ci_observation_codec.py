from collections.abc import Mapping
from datetime import datetime

from pydantic import Field
from sqlalchemy.engine import RowMapping

from ci_coordinator.ci_economics.discovery import RunDiscoveryWindow
from ci_coordinator.ci_economics.observation import ObservationSnapshot
from ci_coordinator.ci_economics.observation_gaps import ObservationGap
from ci_coordinator.ci_economics.observation_payload import (
    ObservationDigest,
    ObservationGapPayload,
    ObservationPositiveId,
    ObservationSnapshotPayload,
    ObservationTimestamp,
)
from ci_coordinator.ci_economics.observation_progress import (
    ObservationOutcome,
    ObservationScanProgress,
)
from ci_coordinator.ci_economics.observation_scan import (
    ObservationLane,
    ObservationLease,
    ObservationScanState,
)
from ci_coordinator.ci_economics.observation_windows import ObservationCursor
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
)

MAX_OBSERVATION_PAYLOAD_BYTES = 2_048


class _CursorPayload(EconomicsPayloadModel):
    interval_from: ObservationTimestamp = Field(alias="intervalFrom")
    interval_through: ObservationTimestamp = Field(alias="intervalThrough")
    window_from: ObservationTimestamp = Field(alias="windowFrom")
    window_through: ObservationTimestamp = Field(alias="windowThrough")
    page_number: int = Field(alias="pageNumber", ge=1, le=10)
    cycle_started_at: ObservationTimestamp = Field(alias="cycleStartedAt")

    def to_cursor(self) -> ObservationCursor:
        return ObservationCursor(
            RunDiscoveryWindow(
                datetime.fromisoformat(self.interval_from),
                datetime.fromisoformat(self.interval_through),
            ),
            RunDiscoveryWindow(
                datetime.fromisoformat(self.window_from),
                datetime.fromisoformat(self.window_through),
            ),
            self.page_number,
            datetime.fromisoformat(self.cycle_started_at),
        )


class _LeasePayload(EconomicsPayloadModel):
    worker_id: ObservationDigest = Field(alias="workerId")
    token: ObservationDigest = Field(repr=False)
    acquired_at: ObservationTimestamp = Field(alias="acquiredAt")
    expires_at: ObservationTimestamp = Field(alias="expiresAt")

    def to_lease(self) -> ObservationLease:
        return ObservationLease(
            self.worker_id,
            self.token,
            datetime.fromisoformat(self.acquired_at),
            datetime.fromisoformat(self.expires_at),
        )


class _ScanPayload(EconomicsPayloadModel):
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    config_revision: ObservationPositiveId = Field(alias="configRevision")
    lane: ObservationLane
    revision: ObservationPositiveId
    cursor: _CursorPayload | None
    next_attempt_at: ObservationTimestamp = Field(alias="nextAttemptAt")
    lease: _LeasePayload | None = Field(repr=False)

    def to_state(self) -> ObservationScanState:
        return ObservationScanState(
            RepositoryScope(self.installation_id, self.repository_id),
            self.config_revision,
            self.lane,
            self.revision,
            None if self.cursor is None else self.cursor.to_cursor(),
            datetime.fromisoformat(self.next_attempt_at),
            None if self.lease is None else self.lease.to_lease(),
        )


def encode_observation_payload(value: Mapping[str, object]) -> bytes:
    return encode_canonical_object(
        dict(value), maximum_bytes=MAX_OBSERVATION_PAYLOAD_BYTES, context="observation payload"
    )


def decode_observation_payload(value: object) -> dict[str, object]:
    return dict(
        decode_canonical_object(
            value, maximum_bytes=MAX_OBSERVATION_PAYLOAD_BYTES, context="observation payload"
        )
    )


def decode_subscription(row: Mapping[str, object] | RowMapping) -> ObservationSnapshot:
    snapshot = ObservationSnapshotPayload.model_validate(
        decode_observation_payload(row["snapshot_canonical"])
    ).to_snapshot()
    if (
        snapshot.scope.installation_id != row["installation_id"]
        or snapshot.scope.repository_id != row["repository_id"]
        or snapshot.revision != row["revision"]
        or snapshot.configuration.enabled is not row["enabled"]
        or snapshot.snapshot_digest != row["snapshot_digest"]
    ):
        raise ValueError("observation configuration contradicts storage projections")
    return snapshot


def encode_scan(progress: ObservationScanProgress) -> dict[str, object]:
    state = progress.state
    lease = state.lease
    canonical = {
        "installationId": state.scope.installation_id,
        "repositoryId": state.scope.repository_id,
        "configRevision": state.config_revision,
        "lane": state.lane,
        "revision": state.revision,
        "cursor": None if state.cursor is None else state.cursor.canonical_mapping(),
        "nextAttemptAt": state.next_attempt_at.isoformat(),
        "lease": None
        if lease is None
        else {
            "workerId": lease.worker_id,
            "token": lease.token,
            "acquiredAt": lease.acquired_at.isoformat(),
            "expiresAt": lease.expires_at.isoformat(),
        },
    }
    return {
        "installation_id": state.scope.installation_id,
        "repository_id": state.scope.repository_id,
        "lane": state.lane,
        "config_revision": state.config_revision,
        "revision": state.revision,
        "state_canonical": encode_observation_payload(canonical),
        "next_attempt_at": state.next_attempt_at,
        "lease_expires_at": None if lease is None else lease.expires_at,
        "last_completed_through": progress.last_completed_through,
        "last_page_at": progress.last_page_at,
        "pages_seen": progress.pages_seen,
        "sources_registered": progress.sources_registered,
        "last_outcome": progress.last_outcome,
    }


class _ProgressColumns(EconomicsPayloadModel):
    last_completed_through: datetime | None = Field(alias="last_completed_through")
    last_page_at: datetime | None = Field(alias="last_page_at")
    pages_seen: int = Field(alias="pages_seen")
    sources_registered: int = Field(alias="sources_registered")
    last_outcome: ObservationOutcome | None = Field(alias="last_outcome")


def decode_scan(row: Mapping[str, object] | RowMapping) -> ObservationScanProgress:
    state = _ScanPayload.model_validate(
        decode_observation_payload(row["state_canonical"])
    ).to_state()
    values = _ProgressColumns.model_validate(
        {name: row[name] for name in _ProgressColumns.model_fields}
    )
    progress = ObservationScanProgress(
        state,
        values.last_completed_through,
        values.last_page_at,
        values.pages_seen,
        values.sources_registered,
        values.last_outcome,
    )
    encoded = encode_scan(progress)
    if any(encoded[name] != row[name] for name in encoded):
        raise ValueError("observation scan contradicts canonical storage")
    return progress


def decode_gap(row: Mapping[str, object] | RowMapping) -> ObservationGap:
    gap = ObservationGapPayload.model_validate(
        decode_observation_payload(row["gap_canonical"])
    ).to_gap()
    if (
        gap.gap_id != row["gap_id"]
        or gap.scope.installation_id != row["installation_id"]
        or gap.scope.repository_id != row["repository_id"]
        or gap.expires_at != row["expires_at"]
    ):
        raise ValueError("observation gap contradicts canonical storage")
    return gap
