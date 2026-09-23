"""CI economics value-object invariants."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import cast

import pytest

from ci_coordinator.ci_economics import (
    MAX_ECONOMICS_PAGE_SIZE,
    MAX_JOB_LABEL_CODE_POINTS,
    MAX_JOBS_PER_ATTEMPT,
    MEASUREMENT_DEFINITION_VERSION,
    AttemptEconomics,
    AttemptIdentity,
    AttemptSnapshot,
    AttemptSummary,
    AttemptSummaryPage,
    DurationAggregate,
    EvidenceQuality,
    JobTiming,
    PlannedRoute,
    ProviderAttemptSnapshot,
    RunnerIdentity,
    WorkflowConclusion,
    WorkflowJobFact,
    decode_attempt_cursor,
    encode_attempt_cursor,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .factories import ATTEMPT, CONTRACT, NOW, SUBJECT, job, provider_snapshot, retained_snapshot

_EXACT_DURATION = DurationAggregate("exact", 1, 1, 1, None)
_DIVERGENT_DURATION = DurationAggregate("exact", 2, 2, 2, None)
_CONFLICT_DURATION = DurationAggregate(
    "conflict",
    None,
    0,
    1,
    "conflicting_workflow_job_evidence",
)
_OTHER_ATTEMPT = replace(ATTEMPT, workflow_run_id=ATTEMPT.workflow_run_id + 1)
_SUMMARY = AttemptSummary(
    SUBJECT.subject_id,
    ATTEMPT,
    CONTRACT.contract_hash,
    "unknown",
    1,
    retained_snapshot().snapshot_digest,
    NOW,
)
_ECONOMICS = AttemptEconomics(
    retained_snapshot(),
    MEASUREMENT_DEFINITION_VERSION,
    _EXACT_DURATION,
    _EXACT_DURATION,
    _EXACT_DURATION,
    "d" * 64,
)

type _InvalidFactory = Callable[[], object]


@pytest.mark.parametrize(
    ("runner_id", "runner_name", "runner_group_id", "runner_group_name"),
    [
        (1, None, None, None),
        (None, "runner", None, None),
        (1, "runner", 2, None),
        (1, "runner", None, "group"),
        (None, None, 2, "group"),
    ],
)
def test_runner_identity_rejects_partial_identity_pairs(
    runner_id: int | None,
    runner_name: str | None,
    runner_group_id: int | None,
    runner_group_name: str | None,
) -> None:
    with pytest.raises(ValueError):
        RunnerIdentity(runner_id, runner_name, runner_group_id, runner_group_name)


@pytest.mark.parametrize(
    ("runner_id", "runner_name", "runner_group_id", "runner_group_name"),
    [
        (None, None, None, None),
        (1, "runner", None, None),
        (1, "runner", 2, "group"),
    ],
)
def test_runner_identity_accepts_complete_identity_pairs(
    runner_id: int | None,
    runner_name: str | None,
    runner_group_id: int | None,
    runner_group_name: str | None,
) -> None:
    assert (
        RunnerIdentity(
            runner_id,
            runner_name,
            runner_group_id,
            runner_group_name,
        ).runner_id
        == runner_id
    )


@pytest.mark.parametrize(
    ("queue", "runner_occupancy", "attempt_wall", "message"),
    [
        (_DIVERGENT_DURATION, _EXACT_DURATION, _EXACT_DURATION, "coverage crosses"),
        (_EXACT_DURATION, _DIVERGENT_DURATION, _EXACT_DURATION, "coverage crosses"),
        (_EXACT_DURATION, _EXACT_DURATION, _DIVERGENT_DURATION, "coverage crosses"),
        (_CONFLICT_DURATION, _EXACT_DURATION, _EXACT_DURATION, "conflict must invalidate"),
        (
            _CONFLICT_DURATION,
            _CONFLICT_DURATION,
            _EXACT_DURATION,
            "conflict must invalidate",
        ),
    ],
    ids=(
        "queue-cardinality",
        "occupancy-cardinality",
        "wall-cardinality",
        "one-conflict",
        "two-conflicts",
    ),
)
def test_attempt_economics_rejects_inconsistent_direct_aggregate_shapes(
    queue: DurationAggregate,
    runner_occupancy: DurationAggregate,
    attempt_wall: DurationAggregate,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        AttemptEconomics(
            snapshot=retained_snapshot(),
            definition_version=MEASUREMENT_DEFINITION_VERSION,
            queue=queue,
            runner_occupancy=runner_occupancy,
            attempt_wall=attempt_wall,
            observation_set_hash="d" * 64,
        )


def test_provider_snapshot_rejects_more_than_the_profile_job_bound() -> None:
    jobs = tuple(
        job(provider_job_id=job_id, created_at=None, delivery_id=None)
        for job_id in range(1, MAX_JOBS_PER_ATTEMPT + 2)
    )

    with pytest.raises(ValueError, match="job count is outside"):
        provider_snapshot(*jobs)


def test_duration_aggregate_rejects_a_non_json_safe_value() -> None:
    with pytest.raises(ValueError, match="JSON-safe"):
        DurationAggregate("exact", MAX_SAFE_JSON_INTEGER + 1, 1, 1, None)


@pytest.mark.parametrize(
    ("factory", "error", "message"),
    [
        (
            lambda: replace(ATTEMPT, scope=cast(RepositoryScope, object())),
            TypeError,
            "exact repository scope",
        ),
        (lambda: replace(ATTEMPT, workflow_run_id=0), ValueError, "positive JSON-safe"),
        (lambda: RunnerIdentity(0, "runner", None, None), ValueError, "positive JSON-safe"),
        (lambda: RunnerIdentity(1, "", None, None), ValueError, "bounded non-empty"),
        (
            lambda: JobTiming(NOW, NOW.replace(hour=9), NOW),
            ValueError,
            "timestamps must be monotonic",
        ),
        (
            lambda: JobTiming(datetime(2026, 9, 4), None, None),
            ValueError,
            "timezone-aware",
        ),
        (
            lambda: replace(job(), attempt=cast(AttemptIdentity, object())),
            TypeError,
            "exact attempt identity",
        ),
        (lambda: job(provider_job_id=0), ValueError, "positive JSON-safe"),
        (
            lambda: job(conclusion=cast(WorkflowConclusion, "unknown")),
            ValueError,
            "conclusion is invalid",
        ),
        (
            lambda: replace(job(), timing=cast(JobTiming, object())),
            TypeError,
            "exact timing",
        ),
        (
            lambda: replace(job(), runner=cast(RunnerIdentity, object())),
            TypeError,
            "exact runner identity",
        ),
        (lambda: replace(job(), semantic_hash="0" * 64), ValueError, "does not match"),
        (lambda: job(name=""), ValueError, "bounded non-empty"),
        (
            lambda: job(labels=cast(tuple[str, ...], ["linux"])),
            ValueError,
            "cardinality bound",
        ),
        (lambda: job(labels=("",)), ValueError, "invalid text"),
        (
            lambda: job(labels=("x" * (MAX_JOB_LABEL_CODE_POINTS + 1),)),
            ValueError,
            "invalid text",
        ),
        (lambda: job(labels=("z", "a")), ValueError, "sorted and unique"),
        (lambda: job(delivery_id=""), ValueError, "bounded non-empty"),
        (
            lambda: replace(retained_snapshot(), attempt=cast(AttemptIdentity, object())),
            TypeError,
            "exact attempt identity",
        ),
        (
            lambda: replace(
                retained_snapshot(),
                planned_route=cast(PlannedRoute, "other"),
            ),
            ValueError,
            "route",
        ),
        (
            lambda: replace(retained_snapshot(), recorded_at=cast(datetime, None)),
            ValueError,
            "recording time",
        ),
        (
            lambda: replace(
                retained_snapshot(),
                retain_until=retained_snapshot().recorded_at,
            ),
            ValueError,
            "retention must follow",
        ),
        (
            lambda: _retained_with_jobs(
                cast(
                    tuple[WorkflowJobFact, ...],
                    [job(created_at=None, delivery_id=None)],
                )
            ),
            TypeError,
            "exact tuple",
        ),
        (lambda: _retained_with_jobs(()), ValueError, "job count"),
        (
            lambda: _retained_with_jobs(
                (
                    job(provider_job_id=405, created_at=None, delivery_id=None),
                    job(provider_job_id=404, created_at=None, delivery_id=None),
                ),
            ),
            ValueError,
            "strictly ordered",
        ),
        (
            lambda: _retained_with_jobs((job(created_at=None),)),
            ValueError,
            "provider-only facts",
        ),
        (
            lambda: _retained_with_jobs(
                (job(attempt=_OTHER_ATTEMPT, created_at=None, delivery_id=None),),
            ),
            ValueError,
            "one attempt",
        ),
        (lambda: replace(retained_snapshot(), snapshot_digest="0" * 64), ValueError, "digest"),
        (
            lambda: replace(provider_snapshot(), attempt=cast(AttemptIdentity, object())),
            TypeError,
            "exact attempt identity",
        ),
        (
            lambda: _provider_with_jobs(
                cast(
                    tuple[WorkflowJobFact, ...],
                    [job(created_at=None, delivery_id=None)],
                )
            ),
            TypeError,
            "exact tuple",
        ),
        (lambda: _provider_with_jobs(()), ValueError, "job count"),
        (
            lambda: _provider_with_jobs(
                (
                    job(provider_job_id=405, created_at=None, delivery_id=None),
                    job(provider_job_id=404, created_at=None, delivery_id=None),
                ),
            ),
            ValueError,
            "strictly ordered",
        ),
        (
            lambda: _provider_with_jobs((job(created_at=None),)),
            ValueError,
            "provider-only facts",
        ),
        (lambda: replace(provider_snapshot(), snapshot_digest="0" * 64), ValueError, "digest"),
        (
            lambda: DurationAggregate(
                cast(EvidenceQuality, "other"),
                None,
                0,
                1,
                "reason",
            ),
            ValueError,
            "quality",
        ),
        (lambda: DurationAggregate("exact", 1, 2, 1, None), ValueError, "coverage"),
        (
            lambda: DurationAggregate("exact", None, 1, 1, None),
            ValueError,
            "complete known coverage",
        ),
        (
            lambda: DurationAggregate("unknown", 1, 1, 1, "reason"),
            ValueError,
            "cannot expose a value",
        ),
        (
            lambda: DurationAggregate("partial", None, 0, 1, "reason"),
            ValueError,
            "strict non-empty partial coverage",
        ),
        (
            lambda: DurationAggregate("exact", 1, 1, 1, "reason"),
            ValueError,
            "cannot have an unavailable reason",
        ),
        (
            lambda: DurationAggregate("unknown", None, 0, 1, None),
            ValueError,
            "bounded non-empty",
        ),
        (
            lambda: replace(_ECONOMICS, snapshot=cast(AttemptSnapshot, object())),
            TypeError,
            "exact snapshot",
        ),
        (
            lambda: replace(_ECONOMICS, definition_version="v2"),
            ValueError,
            "version is unsupported",
        ),
        (
            lambda: replace(_ECONOMICS, queue=cast(DurationAggregate, object())),
            TypeError,
            "exact aggregates",
        ),
        (
            lambda: replace(_SUMMARY, attempt=cast(AttemptIdentity, object())),
            TypeError,
            "exact attempt identity",
        ),
        (
            lambda: replace(
                _SUMMARY,
                planned_route=cast(PlannedRoute, "other"),
            ),
            ValueError,
            "planned route",
        ),
        (lambda: replace(_SUMMARY, job_count=0), ValueError, "job count"),
        (
            lambda: replace(_SUMMARY, recorded_at=cast(datetime, None)),
            ValueError,
            "recorded_at is required",
        ),
        (
            lambda: AttemptSummaryPage(cast(tuple[AttemptSummary, ...], [_SUMMARY]), None),
            TypeError,
            "exact items",
        ),
        (
            lambda: AttemptSummaryPage((_SUMMARY,) * (MAX_ECONOMICS_PAGE_SIZE + 1), None),
            ValueError,
            "cardinality bound",
        ),
        (
            lambda: AttemptSummaryPage((_SUMMARY, _SUMMARY), None),
            ValueError,
            "strictly newest-first",
        ),
        (lambda: AttemptSummaryPage((), "invalid"), ValueError, "final item"),
        (
            lambda: encode_attempt_cursor(cast(datetime, None), SUBJECT.subject_id),
            ValueError,
            "time is required",
        ),
        (lambda: decode_attempt_cursor("invalid"), ValueError, "cursor is invalid"),
        (
            lambda: decode_attempt_cursor("2026-99-04T10:00:00.000000Z." + "a" * 64),
            ValueError,
            "cursor time is invalid",
        ),
        (
            lambda: decode_attempt_cursor("2026-09-04T10:00:00.000000z." + "a" * 64),
            ValueError,
            "not canonical",
        ),
    ],
)
def test_value_objects_reject_states_outside_the_closed_evidence_algebra(
    factory: _InvalidFactory,
    error: type[BaseException],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        factory()


def test_content_identities_and_cursor_bind_their_canonical_values() -> None:
    provider_job = job(created_at=None, delivery_id=None)
    cursor = _SUMMARY.cursor

    assert ATTEMPT.identity_hash == hash_object(ATTEMPT.canonical_mapping())
    assert provider_job.natural_identity_hash == hash_object(
        {"attempt": ATTEMPT.canonical_mapping(), "providerJobId": provider_job.provider_job_id}
    )
    assert decode_attempt_cursor(cursor) == (_SUMMARY.recorded_at, _SUMMARY.subject_id)


def _retained_with_jobs(
    jobs: tuple[WorkflowJobFact, ...] | list[WorkflowJobFact],
) -> AttemptSnapshot:
    return replace(
        retained_snapshot(),
        snapshot_digest=_snapshot_digest_for(tuple(jobs)),
        jobs=cast(tuple[WorkflowJobFact, ...], jobs),
    )


def _provider_with_jobs(
    jobs: tuple[WorkflowJobFact, ...] | list[WorkflowJobFact],
) -> ProviderAttemptSnapshot:
    return replace(
        provider_snapshot(),
        snapshot_digest=_snapshot_digest_for(tuple(jobs)),
        jobs=cast(tuple[WorkflowJobFact, ...], jobs),
    )


def _snapshot_digest_for(jobs: tuple[WorkflowJobFact, ...]) -> str:
    return hash_object(
        {
            "attempt": ATTEMPT.canonical_mapping(),
            "jobs": [item.canonical_mapping() for item in jobs],
        }
    )
