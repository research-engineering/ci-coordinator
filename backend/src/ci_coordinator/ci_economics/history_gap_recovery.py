from typing import Annotated, Final, Literal, Self

from pydantic import Field, field_validator, model_validator

from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_gap import HistoryRecheckGap
from ci_coordinator.ci_economics.history_rechecks import HistoryRecheckHint
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel, JsonTuple
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object

HISTORY_GAPS_REQUEUED_EVENT_TYPE: Final = "ci-economics-history-gaps-requeued/v1"
MAX_GAP_REPAIR_ATTEMPTS: Final = 50
type GapId = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
type HistoryGapRepairOutcome = Literal[
    "committed",
    "replayed",
    "operation_conflict",
    "revision_conflict",
    "generation_conflict",
    "dataset_fenced",
    "gap_not_found",
    "unsupported_gap",
    "inconsistent_source",
    "selection_too_wide",
    "workflow_unselected",
    "capacity_reached",
]


class HistoryGapRepairRequest(EconomicsPayloadModel):
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    generation: ObservationPositiveId
    expected_revision: ObservationPositiveId = Field(alias="expectedRevision")
    gap_ids: tuple[GapId, ...] = Field(alias="gapIds", min_length=1, max_length=50)
    operation_id: str = Field(
        alias="operationId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )

    @field_validator("gap_ids", mode="before")
    @classmethod
    def freeze_gap_ids(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @field_validator("gap_ids")
    @classmethod
    def admit_gap_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("gap selection must contain unique ordered identities")
        return value

    @property
    def scope(self) -> RepositoryScope:
        return RepositoryScope(self.installation_id, self.repository_id)


class RepairHistoryGaps(HistoryGapRepairRequest):
    actor: str = Field(min_length=1, max_length=512)

    @classmethod
    def from_request(cls, request: HistoryGapRepairRequest, *, actor: str) -> Self:
        admitted = HistoryGapRepairRequest.model_validate(request)
        return cls.model_validate({**admitted.model_dump(), "actor": actor})

    @field_validator("actor")
    @classmethod
    def admit_actor(cls, value: str) -> str:
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("history repair actor must be bounded Unicode scalar text")
        return value

    @property
    def request(self) -> HistoryGapRepairRequest:
        return HistoryGapRepairRequest.model_validate(self.model_dump(exclude={"actor"}))

    @property
    def command_digest(self) -> str:
        return hash_object(self.model_dump(mode="json"))

    @property
    def audit_key(self) -> str:
        return (
            f"ci-history-gap-repair:{self.installation_id}:{self.repository_id}:{self.operation_id}"
        )


class HistoryGapRepairInterval(EconomicsPayloadModel):
    workflow_run_id: ObservationPositiveId = Field(alias="workflowRunId")
    from_attempt: ObservationPositiveId = Field(alias="fromAttempt")
    through_attempt: ObservationPositiveId = Field(alias="throughAttempt")

    @model_validator(mode="after")
    def admit_interval(self) -> Self:
        if not 1 <= self.through_attempt - self.from_attempt + 1 <= MAX_GAP_REPAIR_ATTEMPTS:
            raise ValueError("repair interval exceeds its attempt budget")
        return self


class HistoryGapRepairReceipt(EconomicsPayloadModel):
    request: HistoryGapRepairRequest
    intervals: JsonTuple[HistoryGapRepairInterval] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def admit_intervals(self) -> Self:
        runs = tuple(item.workflow_run_id for item in self.intervals)
        if (
            runs != tuple(sorted(set(runs)))
            or len(runs) > len(self.request.gap_ids)
            or sum(item.through_attempt - item.from_attempt + 1 for item in self.intervals)
            > MAX_GAP_REPAIR_ATTEMPTS
        ):
            raise ValueError("repair receipt exceeds its unique run/attempt selection")
        return self


class HistoryGapRepairResult(EconomicsPayloadModel):
    outcome: HistoryGapRepairOutcome
    operation_id: str = Field(alias="operationId", min_length=1, max_length=128)
    receipt: HistoryGapRepairReceipt | None = None

    @model_validator(mode="after")
    def admit_receipt(self) -> Self:
        if (self.outcome in {"committed", "replayed"}) != (self.receipt is not None):
            raise ValueError("repair outcome must match receipt presence")
        if self.receipt is not None and self.receipt.request.operation_id != self.operation_id:
            raise ValueError("repair receipt substitutes operation identity")
        return self


def gap_repair_hints(
    command: RepairHistoryGaps, gaps: tuple[HistoryRecheckGap, ...]
) -> tuple[HistoryRecheckHint, ...] | Literal["inconsistent_source", "selection_too_wide"]:
    if tuple(gap.gap_id for gap in gaps) != command.gap_ids or any(
        gap.scope != command.scope or gap.generation != command.generation for gap in gaps
    ):
        raise ValueError("repair sources do not match the exact command selection")
    grouped: dict[int, HistoryRecheckHint] = {}
    for gap in gaps:
        prior = grouped.get(gap.workflow_run_id)
        if prior is not None and (prior.workflow_id, prior.run_created_at) != (
            gap.workflow_id,
            gap.run_created_at,
        ):
            return "inconsistent_source"
        lower = (
            gap.run_attempt if prior is None else min(prior.cursor.next_attempt, gap.run_attempt)
        )
        upper = (
            gap.run_attempt if prior is None else max(prior.cursor.latest_attempt, gap.run_attempt)
        )
        grouped[gap.workflow_run_id] = HistoryRecheckHint(
            HistoryAttemptCursor(command.scope, gap.workflow_run_id, upper, lower),
            gap.workflow_id,
            gap.run_created_at,
            "repair",
        )
    hints = tuple(grouped[key] for key in sorted(grouped))
    if (
        sum(h.cursor.latest_attempt - h.cursor.next_attempt + 1 for h in hints)
        > MAX_GAP_REPAIR_ATTEMPTS
    ):
        return "selection_too_wide"
    return hints
