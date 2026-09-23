from collections.abc import Mapping
from datetime import datetime

from pydantic import Field
from sqlalchemy.engine import RowMapping

from ci_coordinator.ci_economics.archive_statistics import ArchiveInstant
from ci_coordinator.ci_economics.history_checkpoint_payload import HistoryCheckpointPayload
from ci_coordinator.ci_economics.history_configuration import (
    HistoryConfiguration,
    HistoryDataset,
    HistoryDatasetState,
    HistoryUsage,
)
from ci_coordinator.ci_economics.history_scan import (
    HistoryScanOutcome,
    HistoryScanState,
    RecentHistoryProgress,
)
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
    require_bytes,
)
from ci_coordinator.persistence.ci_history_lease_codec import HistoryLeasePayload

MAX_HISTORY_CONFIGURATION_BYTES = 4096
MAX_HISTORY_SCAN_BYTES = 65536


class _DatasetColumns(EconomicsPayloadModel):
    installation_id: ObservationPositiveId
    repository_id: ObservationPositiveId
    generation: ObservationPositiveId
    configuration_revision: ObservationPositiveId
    data_revision: ObservationPositiveId
    configured_at: ArchiveInstant
    state: HistoryDatasetState
    attempt_count: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    job_count: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    gap_count: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    canonical_bytes: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)


def encode_history_dataset(dataset: HistoryDataset) -> dict[str, object]:
    if type(dataset) is not HistoryDataset:
        raise TypeError("history persistence requires an exact dataset")
    return {
        "installation_id": dataset.scope.installation_id,
        "repository_id": dataset.scope.repository_id,
        "generation": dataset.generation,
        "configuration_revision": dataset.configuration_revision,
        "data_revision": dataset.data_revision,
        "configured_at": dataset.configured_at,
        "state": dataset.state,
        "configuration_canonical": encode_canonical_object(
            dataset.configuration.model_dump(mode="json"),
            maximum_bytes=MAX_HISTORY_CONFIGURATION_BYTES,
            context="history configuration",
        ),
        "attempt_count": dataset.usage.attempts,
        "job_count": dataset.usage.jobs,
        "gap_count": dataset.usage.gaps,
        "canonical_bytes": dataset.usage.canonical_bytes,
    }


def decode_history_dataset(row: Mapping[str, object] | RowMapping) -> HistoryDataset:
    columns = _DatasetColumns.model_validate(
        {name: row.get(name) for name in _DatasetColumns.model_fields}
    )
    raw = require_bytes(row.get("configuration_canonical"), "history configuration")
    decode_canonical_object(
        raw, maximum_bytes=MAX_HISTORY_CONFIGURATION_BYTES, context="history configuration"
    )
    return HistoryDataset(
        RepositoryScope(columns.installation_id, columns.repository_id),
        columns.generation,
        columns.configuration_revision,
        columns.data_revision,
        columns.configured_at,
        columns.state,
        HistoryConfiguration.model_validate_json(raw),
        HistoryUsage(
            attempts=columns.attempt_count,
            jobs=columns.job_count,
            gaps=columns.gap_count,
            canonicalBytes=columns.canonical_bytes,
        ),
    )


class _RecentProgressPayload(EconomicsPayloadModel):
    recovery_floor: ArchiveInstant = Field(alias="recoveryFloor")
    completed_through: ArchiveInstant | None = Field(alias="completedThrough")


class _ScanPayload(EconomicsPayloadModel):
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    generation: ObservationPositiveId
    configuration_revision: ObservationPositiveId = Field(alias="configurationRevision")
    revision: ObservationPositiveId
    checkpoint: HistoryCheckpointPayload
    next_attempt_at: ArchiveInstant = Field(alias="nextAttemptAt")
    lease: HistoryLeasePayload | None = Field(repr=False)
    pages_seen: int = Field(alias="pagesSeen", ge=0, le=MAX_SAFE_JSON_INTEGER)
    attempts_seen: int = Field(alias="attemptsSeen", ge=0, le=MAX_SAFE_JSON_INTEGER)
    last_outcome: HistoryScanOutcome | None = Field(alias="lastOutcome")
    recent: _RecentProgressPayload | None

    def to_state(self) -> HistoryScanState:
        lease = self.lease
        return HistoryScanState(
            RepositoryScope(self.installation_id, self.repository_id),
            self.generation,
            self.configuration_revision,
            self.revision,
            self.checkpoint.to_checkpoint(),
            self.next_attempt_at,
            None if lease is None else lease.to_lease(),
            self.pages_seen,
            self.attempts_seen,
            self.last_outcome,
            None
            if self.recent is None
            else RecentHistoryProgress(self.recent.recovery_floor, self.recent.completed_through),
        )


def encode_history_scan(state: HistoryScanState) -> dict[str, object]:
    if type(state) is not HistoryScanState:
        raise TypeError("history persistence requires an exact scan state")
    lease = state.lease
    canonical = {
        "installationId": state.scope.installation_id,
        "repositoryId": state.scope.repository_id,
        "generation": state.generation,
        "configurationRevision": state.configuration_revision,
        "revision": state.revision,
        "checkpoint": HistoryCheckpointPayload.from_checkpoint(state.checkpoint).model_dump(
            mode="json"
        ),
        "nextAttemptAt": state.next_attempt_at.isoformat(),
        "lease": None
        if lease is None
        else HistoryLeasePayload.from_lease(lease).model_dump(mode="json"),
        "pagesSeen": state.pages_seen,
        "attemptsSeen": state.attempts_seen,
        "lastOutcome": state.last_outcome,
        "recent": None
        if state.recent is None
        else {
            "recoveryFloor": state.recent.recovery_floor.isoformat(),
            "completedThrough": None
            if state.recent.completed_through is None
            else state.recent.completed_through.isoformat(),
        },
    }
    return {
        "installation_id": state.scope.installation_id,
        "repository_id": state.scope.repository_id,
        "lane": state.lane,
        "generation": state.generation,
        "configuration_revision": state.configuration_revision,
        "revision": state.revision,
        "state_canonical": encode_canonical_object(
            canonical, maximum_bytes=MAX_HISTORY_SCAN_BYTES, context="history scan"
        ),
        "traversal_complete": state.checkpoint.complete,
        "next_attempt_at": state.next_attempt_at,
        "lease_worker_id": None if lease is None else lease.worker_id,
        "lease_token": None if lease is None else lease.token,
        "lease_acquired_at": None if lease is None else lease.acquired_at,
        "lease_expires_at": None if lease is None else lease.expires_at,
    }


def decode_history_scan(row: Mapping[str, object] | RowMapping) -> HistoryScanState:
    raw = require_bytes(row.get("state_canonical"), "history scan")
    decode_canonical_object(raw, maximum_bytes=MAX_HISTORY_SCAN_BYTES, context="history scan")
    state = _ScanPayload.model_validate_json(raw).to_state()
    for name, value in encode_history_scan(state).items():
        actual = (
            require_bytes(row.get(name), "history scan") if type(value) is bytes else row.get(name)
        )
        if isinstance(value, datetime) and isinstance(actual, datetime):
            if actual != value:
                raise ValueError("history scan timestamp projection differs")
        elif type(actual) is not type(value) or actual != value:
            raise ValueError("history scan projection differs from its canonical state")
    return state
