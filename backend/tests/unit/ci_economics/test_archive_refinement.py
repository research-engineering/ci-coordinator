from datetime import timedelta

import pytest

from ci_coordinator.ci_economics.archive_refinement import classify_archive_update
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics

from .archive_factories import ARCHIVE_TIME, archived_job, archived_statistics


def _changed(value: ArchivedAttemptStatistics, **fields: object) -> ArchivedAttemptStatistics:
    return ArchivedAttemptStatistics.model_validate({**value.model_dump(), **fields})


def test_unknown_partial_complete_progress_does_not_invent_conflict_or_duplicate_facts() -> None:
    unavailable = archived_statistics(population="unavailable", jobs=(), provider_total=None)
    empty_partial = archived_statistics(population="partial", jobs=(), provider_total=2)
    partial = archived_statistics(population="partial", provider_total=2)
    complete = archived_statistics(jobs=(archived_job(1), archived_job(2)), provider_total=2)
    for prior, incoming in (
        (unavailable, empty_partial),
        (empty_partial, partial),
        (partial, complete),
    ):
        assert classify_archive_update(prior, incoming) == "refined"
        assert classify_archive_update(incoming, prior) == "unchanged"
        assert classify_archive_update(prior, prior) == "unchanged"


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflowId", 405),
        ("workflowPath", ".github/workflows/other.yml"),
        ("event", "push"),
        ("conclusion", "failure"),
        ("runCreatedAt", ARCHIVE_TIME + timedelta(seconds=1)),
        ("providerJobTotal", 2),
    ],
)
def test_each_known_header_contradiction_is_a_conflict(field: str, value: object) -> None:
    original = archived_statistics(population="partial", provider_total=3)
    changed = _changed(original, **{field: value})
    assert classify_archive_update(original, changed) == "conflict"
    assert classify_archive_update(changed, original) == "conflict"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "Other"),
        ("conclusion", "failure"),
        ("labels", ("windows",)),
        ("runnerId", 9),
        ("runnerGroupId", 9),
        ("createdAt", ARCHIVE_TIME + timedelta(seconds=1)),
        ("startedAt", ARCHIVE_TIME + timedelta(seconds=61)),
        ("completedAt", ARCHIVE_TIME + timedelta(seconds=121)),
    ],
)
def test_each_known_job_operand_contradiction_is_a_conflict(field: str, value: object) -> None:
    original = archived_statistics()
    changed_job = type(original.jobs[0]).model_validate(
        {**original.jobs[0].model_dump(), field: value}
    )
    changed = _changed(original, jobs=(changed_job,))
    assert classify_archive_update(original, changed) == "conflict"


def test_nullable_job_operands_can_be_enriched_but_not_erased() -> None:
    complete = archived_statistics()
    job = complete.jobs[0]
    unknown_job = type(job).model_validate(
        {
            **job.model_dump(),
            "createdAt": None,
            "startedAt": None,
            "completedAt": None,
            "runnerId": None,
            "runnerGroupId": None,
        }
    )
    unknown = _changed(complete, jobs=(unknown_job,))
    assert classify_archive_update(unknown, complete) == "refined"
    assert classify_archive_update(complete, unknown) == "unchanged"


def test_disjoint_partials_are_incomparable_not_a_synthetic_complete_population() -> None:
    first = archived_statistics(population="partial", provider_total=2, jobs=(archived_job(1),))
    second = archived_statistics(population="partial", provider_total=2, jobs=(archived_job(2),))
    assert classify_archive_update(first, second) == "incomparable"
    assert classify_archive_update(second, first) == "incomparable"


def test_complete_population_rejects_substituted_members_even_with_equal_totals() -> None:
    first = archived_statistics(jobs=(archived_job(1),))
    second = archived_statistics(jobs=(archived_job(2),))
    assert classify_archive_update(first, second) == "conflict"


def test_unknown_workflow_provenance_can_be_refined_without_changing_head_identity() -> None:
    unknown = archived_statistics()
    known = _changed(unknown, workflowBlobSha="b" * 40)
    assert classify_archive_update(unknown, known) == "refined"
    different_head = _changed(
        unknown, attempt={**unknown.attempt.model_dump(), "headSha": "c" * 40}
    )
    assert classify_archive_update(unknown, different_head) == "conflict"


def test_explicit_conflict_never_becomes_exact_through_rescan() -> None:
    original = archived_statistics()
    conflicting = _changed(original, population="conflict")
    assert classify_archive_update(original, conflicting) == "conflict"
    assert classify_archive_update(conflicting, original) == "conflict"
