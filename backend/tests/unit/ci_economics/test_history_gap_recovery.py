import json

import pytest

from ci_coordinator.ci_economics.history_gap import HistoryRecheckGap
from ci_coordinator.ci_economics.history_gap_recovery import (
    HistoryGapRepairInterval,
    HistoryGapRepairReceipt,
    HistoryGapRepairRequest,
    HistoryGapRepairResult,
    RepairHistoryGaps,
    gap_repair_hints,
)
from ci_economics.gap_recovery_factories import gap_command, recheck_gap


@pytest.mark.parametrize(
    "field,value",
    [
        ("installationId", True),
        ("repositoryId", 1.5),
        ("generation", 0),
        ("expectedRevision", 0),
        ("gapIds", []),
        ("gapIds", ["a" * 64, "a" * 64]),
        ("gapIds", ["b" * 64, "a" * 64]),
        ("gapIds", ["a" * 64 + "\n"]),
        ("operationId", "invalid space"),
        ("operationId", "operation\n"),
        ("actor", "caller-injected"),
    ],
)
def test_wire_admission_rejects_isolated_invalid_operand(field: str, value: object) -> None:
    raw = {**gap_command().request.model_dump(mode="json"), field: value}
    with pytest.raises(ValueError):
        HistoryGapRepairRequest.model_validate_json(json.dumps(raw))


def test_actor_enrichment_and_request_round_trip_preserve_exact_identity() -> None:
    original = gap_command()
    request = HistoryGapRepairRequest.model_validate_json(original.request.model_dump_json())
    enriched = RepairHistoryGaps.from_request(request, actor=original.actor)
    assert enriched == original and enriched.request == request
    assert enriched.command_digest == original.command_digest
    assert "actor" not in enriched.request.model_dump()
    assert HistoryGapRepairRequest.model_validate(request.model_dump(mode="json")) == request
    assert (
        RepairHistoryGaps.from_request(request, actor="other").command_digest
        != original.command_digest
    )


@pytest.mark.parametrize(
    "attempts,expected", [((1,), (1, 1)), ((2, 4), (2, 4)), ((1, 50), (1, 50))]
)
def test_interval_derivation_is_bounded_and_preserves_requested_extremes(
    attempts: tuple[int, ...],
    expected: tuple[int, int],
) -> None:
    gaps = tuple(sorted((recheck_gap(attempt=a) for a in attempts), key=lambda g: g.gap_id))
    hints = gap_repair_hints(gap_command(*gaps), gaps)
    assert not isinstance(hints, str) and len(hints) == 1
    assert (hints[0].cursor.next_attempt, hints[0].cursor.latest_attempt) == expected
    assert (
        hints[0].workflow_id == gaps[0].workflow_id
        and hints[0].run_created_at == gaps[0].run_created_at
    )


@pytest.mark.parametrize(
    "pairs",
    [((303, 1), (303, 51)), ((303, 1), (303, 50), (304, 1)), ((303, 1), (303, 9007199254740991))],
)
def test_numeric_span_budget_does_not_allocate_attempt_populations(
    pairs: tuple[tuple[int, int], ...],
) -> None:
    gaps = tuple(sorted((recheck_gap(*pair) for pair in pairs), key=lambda g: g.gap_id))
    assert gap_repair_hints(gap_command(*gaps), gaps) == "selection_too_wide"


@pytest.mark.parametrize(
    "field,value", [("workflowId", 405), ("runCreatedAt", "2021-01-01T00:00:00Z")]
)
def test_same_run_cannot_merge_inconsistent_source_metadata(field: str, value: object) -> None:
    other = HistoryRecheckGap.model_validate_json(
        json.dumps(
            {
                **recheck_gap(attempt=2).model_dump(mode="json"),
                field: value,
            }
        )
    )
    gaps = tuple(sorted((recheck_gap(), other), key=lambda g: g.gap_id))
    assert gap_repair_hints(gap_command(*gaps), gaps) == "inconsistent_source"


@pytest.mark.parametrize("changed", ["scope", "generation", "gap_identity"])
def test_source_selection_cannot_cross_command_identity(changed: str) -> None:
    command = gap_command()
    update = (
        {"installationId": 999}
        if changed == "scope"
        else {"generation": 2}
        if changed == "generation"
        else {"gapIds": ("a" * 64,)}
    )
    altered = RepairHistoryGaps.model_validate({**command.model_dump(), **update})
    with pytest.raises(ValueError):
        gap_repair_hints(altered, (recheck_gap(),))


@pytest.mark.parametrize(
    "outcome,has_receipt", [("committed", False), ("replayed", False), ("capacity_reached", True)]
)
def test_result_presence_is_not_independent_of_outcome(outcome: str, has_receipt: bool) -> None:
    command = gap_command()
    receipt = HistoryGapRepairReceipt(
        request=command.request,
        intervals=(HistoryGapRepairInterval(workflowRunId=303, fromAttempt=1, throughAttempt=1),),
    )
    with pytest.raises(ValueError):
        HistoryGapRepairResult.model_validate(
            {
                "outcome": outcome,
                "operationId": command.operation_id,
                "receipt": receipt if has_receipt else None,
            }
        )
