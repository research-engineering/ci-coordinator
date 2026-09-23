from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import Field
from sqlalchemy.engine import RowMapping

from ci_coordinator.ci_economics.archive_statistics import ArchiveInstant
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_RECHECK_ACQUISITIONS,
    HistoryRecheckHint,
    HistoryRecheckSource,
    HistoryRecheckState,
)
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
    require_bytes,
)
from ci_coordinator.persistence.ci_history_lease_codec import HistoryLeasePayload

MAX_HISTORY_RECHECK_BYTES = 4096


class _RecheckPayload(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-history-recheck/v1"] = Field(alias="schemaVersion")
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    generation: ObservationPositiveId
    workflow_run_id: ObservationPositiveId = Field(alias="workflowRunId")
    workflow_id: ObservationPositiveId = Field(alias="workflowId")
    run_created_at: ArchiveInstant = Field(alias="runCreatedAt")
    first_attempt: ObservationPositiveId = Field(alias="firstAttempt")
    latest_attempt: ObservationPositiveId = Field(alias="latestAttempt")
    next_attempt: ObservationPositiveId = Field(alias="nextAttempt")
    source: HistoryRecheckSource
    revision: ObservationPositiveId
    next_attempt_at: ArchiveInstant = Field(alias="nextAttemptAt")
    acquisition_count: int = Field(
        alias="acquisitionCount", ge=0, le=MAX_HISTORY_RECHECK_ACQUISITIONS
    )
    lease: HistoryLeasePayload | None = Field(repr=False)

    def to_state(self) -> HistoryRecheckState:
        return HistoryRecheckState(
            HistoryRecheckHint(
                HistoryAttemptCursor(
                    RepositoryScope(self.installation_id, self.repository_id),
                    self.workflow_run_id,
                    self.latest_attempt,
                    self.first_attempt,
                ),
                self.workflow_id,
                self.run_created_at,
                self.source,
            ),
            self.generation,
            self.revision,
            self.next_attempt,
            self.next_attempt_at,
            self.acquisition_count,
            None if self.lease is None else self.lease.to_lease(),
        )


def encode_history_recheck(state: HistoryRecheckState) -> dict[str, object]:
    if type(state) is not HistoryRecheckState:
        raise TypeError("history recheck storage requires an exact state")
    hint, lease = state.hint, state.lease
    cursor = hint.cursor
    payload = _RecheckPayload(
        schemaVersion="ci-economics-history-recheck/v1",
        installationId=cursor.scope.installation_id,
        repositoryId=cursor.scope.repository_id,
        generation=state.generation,
        workflowRunId=cursor.workflow_run_id,
        workflowId=hint.workflow_id,
        runCreatedAt=hint.run_created_at,
        firstAttempt=cursor.next_attempt,
        latestAttempt=cursor.latest_attempt,
        nextAttempt=state.next_attempt,
        source=hint.source,
        revision=state.revision,
        nextAttemptAt=state.next_attempt_at,
        acquisitionCount=state.acquisition_count,
        lease=None if lease is None else HistoryLeasePayload.from_lease(lease),
    )
    return {
        "installation_id": cursor.scope.installation_id,
        "repository_id": cursor.scope.repository_id,
        "generation": state.generation,
        "workflow_run_id": cursor.workflow_run_id,
        "workflow_id": hint.workflow_id,
        "source": hint.source,
        "revision": state.revision,
        "state_canonical": encode_canonical_object(
            payload.model_dump(mode="json"),
            maximum_bytes=MAX_HISTORY_RECHECK_BYTES,
            context="history recheck",
        ),
        "next_attempt_at": state.next_attempt_at,
        "acquisition_count": state.acquisition_count,
        "lease_worker_id": None if lease is None else lease.worker_id,
        "lease_token": None if lease is None else lease.token,
        "lease_acquired_at": None if lease is None else lease.acquired_at,
        "lease_expires_at": None if lease is None else lease.expires_at,
    }


def decode_history_recheck(row: Mapping[str, object] | RowMapping) -> HistoryRecheckState:
    raw = require_bytes(row.get("state_canonical"), "history recheck")
    decode_canonical_object(raw, maximum_bytes=MAX_HISTORY_RECHECK_BYTES, context="history recheck")
    state = _RecheckPayload.model_validate_json(raw).to_state()
    for name, value in encode_history_recheck(state).items():
        actual = (
            require_bytes(row.get(name), "history recheck")
            if type(value) is bytes
            else row.get(name)
        )
        if isinstance(value, datetime) and isinstance(actual, datetime):
            if actual != value:
                raise ValueError("history recheck time projection differs")
        elif type(actual) is not type(value) or actual != value:
            raise ValueError("history recheck projection differs from canonical state")
    return state
