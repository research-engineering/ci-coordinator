from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Final, Literal

from pydantic import Field
from sqlalchemy.engine import RowMapping

from ci_coordinator.ci_economics.archive_statistics import ArchiveInstant
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_rechecks import HistoryRecheckHint
from ci_coordinator.ci_economics.model import WorkflowConclusion
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.persistence.canonical_row import decode_canonical_object, require_bytes
from ci_coordinator.persistence.ci_economics_codec import decode_workflow_job_observation

HISTORY_DELIVERY_DECODER: Final = "ci-history-delivery/v1"
MAX_HISTORY_SOURCE_BYTES: Final = 16_384
type _HeadSha = Annotated[str, Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")]
type _Digest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
type UnsupportedHistorySource = Literal["job_only", "workflow_unknown"]


class _SourceColumns(EconomicsPayloadModel):
    delivery_id: str = Field(min_length=1, max_length=128)
    observation_kind: Literal["workflow_run", "workflow_job"]
    installation_id: ObservationPositiveId
    repository_id: ObservationPositiveId
    workflow_run_id: ObservationPositiveId
    run_attempt: ObservationPositiveId
    head_sha: _HeadSha
    provider_job_id: ObservationPositiveId | None
    semantic_hash: _Digest
    recorded_at: ArchiveInstant
    retain_until: ArchiveInstant
    observation_canonical_json: bytes = Field(min_length=1, max_length=MAX_HISTORY_SOURCE_BYTES)


class _RunObservation(EconomicsPayloadModel):
    schema_version: Literal["ci-workflow-run-observation/v1"] = Field(alias="schemaVersion")
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    workflow_run_id: ObservationPositiveId = Field(alias="workflowRunId")
    run_attempt: ObservationPositiveId = Field(alias="runAttempt")
    workflow_id: ObservationPositiveId | None = Field(alias="workflowId")
    workflow_file: str = Field(alias="workflowFile")
    workflow_name: str = Field(alias="workflowName")
    head_branch: str | None = Field(alias="headBranch")
    head_sha: _HeadSha = Field(alias="headSha")
    workflow_event: str = Field(alias="workflowEvent")
    status: Literal["completed"]
    conclusion: WorkflowConclusion | None
    created_at: ArchiveInstant = Field(alias="createdAt")
    run_started_at: ArchiveInstant = Field(alias="runStartedAt")
    updated_at: ArchiveInstant = Field(alias="updatedAt")


@dataclass(frozen=True, slots=True)
class HistoryDeliverySource:
    delivery_id: str
    scope: RepositoryScope
    workflow_run_id: int
    source_fingerprint: str
    recorded_at: datetime
    retain_until: datetime
    hint: HistoryRecheckHint | None
    unsupported: UnsupportedHistorySource | None


def decode_history_delivery(row: Mapping[str, object] | RowMapping) -> HistoryDeliverySource:
    raw = require_bytes(row.get("observation_canonical_json"), "history delivery source")
    mapping = decode_canonical_object(
        raw, maximum_bytes=MAX_HISTORY_SOURCE_BYTES, context="history delivery source"
    )
    columns = _SourceColumns.model_validate({**row, "observation_canonical_json": raw})
    if columns.retain_until != columns.recorded_at + timedelta(days=90):
        raise ValueError("history source retention differs from the observation contract")
    scope = RepositoryScope(columns.installation_id, columns.repository_id)
    hint: HistoryRecheckHint | None = None
    unsupported: UnsupportedHistorySource | None
    if columns.observation_kind == "workflow_job":
        decode_workflow_job_observation(dict(row))
        unsupported = "job_only"
    else:
        run = _RunObservation.model_validate_json(raw)
        if (
            columns.provider_job_id is not None
            or (
                run.installation_id,
                run.repository_id,
                run.workflow_run_id,
                run.run_attempt,
                run.head_sha,
            )
            != (
                columns.installation_id,
                columns.repository_id,
                columns.workflow_run_id,
                columns.run_attempt,
                columns.head_sha,
            )
            or columns.semantic_hash != hash_object(mapping)
        ):
            raise ValueError("history run source columns differ from canonical evidence")
        unsupported = "workflow_unknown" if run.workflow_id is None else None
        if run.workflow_id is not None:
            hint = HistoryRecheckHint(
                HistoryAttemptCursor(scope, run.workflow_run_id, run.run_attempt, 1),
                run.workflow_id,
                run.created_at,
                "recent",
            )
    fingerprint = hash_object(
        {
            "decoder": HISTORY_DELIVERY_DECODER,
            "columns": columns.model_dump(mode="json", exclude={"observation_canonical_json"}),
            "bodySha256": sha256(raw).hexdigest(),
        }
    )
    return HistoryDeliverySource(
        columns.delivery_id,
        scope,
        columns.workflow_run_id,
        fingerprint,
        columns.recorded_at,
        columns.retain_until,
        hint,
        unsupported,
    )


def history_delivery_fields(source: HistoryDeliverySource) -> dict[str, object]:
    if source.hint is None or source.unsupported is not None:
        raise ValueError("unsupported history source has no delivery inbox projection")
    return {
        "delivery_id": source.delivery_id,
        "source_fingerprint": source.source_fingerprint,
        "installation_id": source.scope.installation_id,
        "repository_id": source.scope.repository_id,
        "workflow_run_id": source.workflow_run_id,
        "run_attempt": source.hint.cursor.latest_attempt,
        "workflow_id": source.hint.workflow_id,
        "run_created_at": source.hint.run_created_at,
        "source_recorded_at": source.recorded_at,
        "source_retain_until": source.retain_until,
    }
