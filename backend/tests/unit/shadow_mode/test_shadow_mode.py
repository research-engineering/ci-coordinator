from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta

import pytest

from ci_coordinator.kernel import hash_object
from ci_coordinator.shadow_mode import (
    CoverageRelation,
    FullCiObservation,
    FullCiResult,
    RolloutEvidenceProfile,
    RolloutEvidenceState,
    SafeObservation,
    ShadowCandidate,
    ShadowComparison,
    ShadowComparisonClassification,
    ShadowEvidenceConflict,
    ShadowEvidenceConflictError,
    ShadowEvidenceKey,
    ShadowEvidenceRecord,
    ShadowEvidenceStored,
    SurfaceEvidenceRequirement,
    UnsafeOmissionFinding,
    classify_unsafe_omission,
    compare_full_ci,
    derive_metrics,
    evaluate_rollout_evidence,
    record_and_compare,
)

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def test_safe_replay_is_positive_evidence_without_enforcement_block() -> None:
    candidate = shadow_candidate()

    comparison = compare_full_ci(candidate, matching_observation(candidate))

    assert comparison.classification is ShadowComparisonClassification.SAFE
    assert comparison.is_positive_evidence is True
    assert comparison.blocks_enforcement is False
    assert classify_unsafe_omission(comparison) == SafeObservation(surface="backend-tests")


def test_unsafe_omission_blocks_its_named_surface() -> None:
    candidate = shadow_candidate(coverage_relation=CoverageRelation.OMITTED)

    comparison = compare_full_ci(candidate, matching_observation(candidate))
    finding = classify_unsafe_omission(comparison)

    assert comparison.classification is ShadowComparisonClassification.UNSAFE
    assert comparison.blocks_enforcement is True
    assert finding == UnsafeOmissionFinding(
        surface="backend-tests",
        reason="candidate omits a required validation surface",
    )
    assert finding.blocks_enforcement is True


def test_missing_full_ci_observation_is_unknown_and_never_positive_evidence() -> None:
    comparison = compare_full_ci(shadow_candidate(), None)

    assert comparison.classification is ShadowComparisonClassification.UNKNOWN
    assert comparison.is_positive_evidence is False
    assert comparison.blocks_enforcement is True
    assert classify_unsafe_omission(comparison) is None


def test_replay_mismatch_blocks_the_candidate_surface() -> None:
    candidate = shadow_candidate()
    observation = replace(matching_observation(candidate), config_epoch="epoch-2")

    comparison = compare_full_ci(candidate, observation)

    assert comparison.classification is ShadowComparisonClassification.REPLAY_MISMATCH
    assert comparison.surface == "backend-tests"
    assert comparison.blocks_enforcement is True
    assert comparison.mismatched_fields == ("config_epoch",)
    assert classify_unsafe_omission(comparison) is None


def test_contradictory_full_ci_result_is_a_replay_mismatch_not_safe() -> None:
    candidate = shadow_candidate()
    observation = replace(
        matching_observation(candidate),
        actual_full_ci_result=FullCiResult.FAILED,
    )

    comparison = compare_full_ci(candidate, observation)

    assert comparison.classification is ShadowComparisonClassification.REPLAY_MISMATCH
    assert comparison.is_positive_evidence is False
    assert comparison.blocks_enforcement is True
    assert comparison.mismatched_fields == ("actual_full_ci_result",)


def test_recording_only_records_shadow_evidence_and_cannot_mutate_candidate() -> None:
    candidate = shadow_candidate()
    recorder = RecordingRecorder()

    comparison = asyncio.run(
        record_and_compare(
            candidate,
            matching_observation(candidate),
            profile_id="profile-1",
            observed_at=NOW,
            recorder=recorder,
        )
    )

    assert candidate == shadow_candidate()
    assert recorder.records == [
        ShadowEvidenceRecord(profile_id="profile-1", observed_at=NOW, comparison=comparison)
    ]
    with pytest.raises(FrozenInstanceError):
        # noinspection PyDataclass
        candidate.surface = "changed"  # type: ignore[misc]


def test_unknown_comparison_is_not_persisted_and_cannot_inflate_evidence() -> None:
    recorder = RecordingRecorder()

    comparison = asyncio.run(
        record_and_compare(
            shadow_candidate(),
            None,
            profile_id="profile-1",
            observed_at=NOW,
            recorder=recorder,
        )
    )

    assert comparison.classification is ShadowComparisonClassification.UNKNOWN
    assert recorder.records == []


def test_shadow_evidence_conflict_blocks_a_seemingly_safe_comparison() -> None:
    candidate = shadow_candidate()
    comparison = compare_full_ci(candidate, matching_observation(candidate))
    recorder = ConflictingRecorder(
        ShadowEvidenceRecord(profile_id="profile-1", observed_at=NOW, comparison=comparison)
    )

    with pytest.raises(ShadowEvidenceConflictError):
        asyncio.run(
            record_and_compare(
                candidate,
                matching_observation(candidate),
                profile_id="profile-1",
                observed_at=NOW,
                recorder=recorder,
            )
        )


def test_metrics_are_derived_from_comparison_outcomes_only() -> None:
    safe_candidate = shadow_candidate()
    unsafe_candidate = shadow_candidate(coverage_relation=CoverageRelation.OMITTED)
    mismatch_candidate = shadow_candidate(config_epoch="epoch-3")
    comparisons = (
        compare_full_ci(safe_candidate, matching_observation(safe_candidate)),
        compare_full_ci(unsafe_candidate, matching_observation(unsafe_candidate)),
        compare_full_ci(safe_candidate, None),
        compare_full_ci(mismatch_candidate, matching_observation(shadow_candidate())),
    )

    assert derive_metrics(comparisons).total == 4
    assert derive_metrics(comparisons).safe == 1
    assert derive_metrics(comparisons).unsafe == 1
    assert derive_metrics(comparisons).unknown == 1
    assert derive_metrics(comparisons).replay_mismatch == 1


def test_rollout_profile_requires_fresh_non_vacuous_safe_evidence_for_every_surface() -> None:
    candidate = shadow_candidate()
    later_candidate = shadow_candidate(event="delivery-2")
    profile = RolloutEvidenceProfile(
        (
            SurfaceEvidenceRequirement("backend-tests", 2, 60, 300),
            SurfaceEvidenceRequirement("frontend-tests", 1, 1, 300),
        ),
    )
    records = (
        evidence(candidate, NOW - timedelta(seconds=70), profile_id=profile.profile_id),
        evidence(later_candidate, NOW - timedelta(seconds=10), profile_id=profile.profile_id),
    )

    assessment = evaluate_rollout_evidence(profile, records, now=NOW)

    assert assessment.satisfied is False
    assert assessment.surfaces[0].state is RolloutEvidenceState.SATISFIED
    assert assessment.surfaces[1].state is RolloutEvidenceState.MISSING


@pytest.mark.parametrize(
    "thresholds",
    (
        (9_007_199_254_740_992, 1, 1),
        (1, 9_007_199_254_740_992, 1),
        (1, 1, 9_007_199_254_740_992),
    ),
)
def test_rollout_requirement_rejects_threshold_outside_canonical_identity_domain(
    thresholds: tuple[int, int, int],
) -> None:
    with pytest.raises(ValueError, match="must be a positive JSON-safe integer"):
        SurfaceEvidenceRequirement("backend-tests", *thresholds)


def test_rollout_profile_identity_is_canonical_and_versioned() -> None:
    backend = SurfaceEvidenceRequirement("backend-tests", 2, 60, 300)
    frontend = SurfaceEvidenceRequirement("frontend-tests", 1, 1, 300)

    profile = RolloutEvidenceProfile((frontend, backend))

    assert profile.requirements == (backend, frontend)
    assert profile == RolloutEvidenceProfile((backend, frontend))
    assert profile.profile_id == hash_object(
        {
            "schemaVersion": "ci-shadow-rollout-evidence-profile/v1",
            "evaluatorVersion": "ci-shadow-rollout-evidence-evaluator/v1",
            "requirements": [
                {
                    "surface": "backend-tests",
                    "minimumComparisonCount": 2,
                    "minimumObservationSeconds": 60,
                    "maximumEvidenceAgeSeconds": 300,
                },
                {
                    "surface": "frontend-tests",
                    "minimumComparisonCount": 1,
                    "minimumObservationSeconds": 1,
                    "maximumEvidenceAgeSeconds": 300,
                },
            ],
        }
    )


@pytest.mark.parametrize(
    "changed_requirement",
    (
        SurfaceEvidenceRequirement("backend-tests", 3, 60, 300),
        SurfaceEvidenceRequirement("backend-tests", 2, 61, 300),
        SurfaceEvidenceRequirement("backend-tests", 2, 60, 301),
    ),
)
def test_rollout_profile_identity_changes_with_every_threshold(
    changed_requirement: SurfaceEvidenceRequirement,
) -> None:
    baseline = SurfaceEvidenceRequirement("backend-tests", 2, 60, 300)

    assert (
        RolloutEvidenceProfile((baseline,)).profile_id
        != RolloutEvidenceProfile((changed_requirement,)).profile_id
    )


def test_shadow_evidence_key_remains_profile_repository_event_surface() -> None:
    candidate = shadow_candidate()
    profile = RolloutEvidenceProfile((SurfaceEvidenceRequirement("backend-tests", 2, 60, 300),))

    record = evidence(candidate, NOW, profile_id=profile.profile_id)

    assert tuple(field.name for field in fields(record.key)) == (
        "profile_id",
        "repository",
        "event",
        "surface",
    )
    assert record.key == ShadowEvidenceKey(
        profile_id=profile.profile_id,
        repository="acme/ci-coordinator",
        event="delivery-1",
        surface="backend-tests",
    )


def test_rollout_profile_rejects_duplicate_durable_evidence_keys() -> None:
    candidate = shadow_candidate()
    profile = RolloutEvidenceProfile((SurfaceEvidenceRequirement("backend-tests", 2, 60, 300),))

    with pytest.raises(ValueError, match="must not repeat a durable key"):
        evaluate_rollout_evidence(
            profile,
            (
                evidence(
                    candidate,
                    NOW - timedelta(seconds=70),
                    profile_id=profile.profile_id,
                ),
                evidence(
                    candidate,
                    NOW - timedelta(seconds=10),
                    profile_id=profile.profile_id,
                ),
            ),
            now=NOW,
        )


def test_shadow_comparison_rejects_forged_safe_classification() -> None:
    candidate = shadow_candidate(coverage_relation=CoverageRelation.OMITTED)

    with pytest.raises(ValueError, match="does not match canonical classification"):
        ShadowComparison(
            candidate=candidate,
            observation=matching_observation(candidate),
            classification=ShadowComparisonClassification.SAFE,
            reason="replay identity, FullCI result, and coverage relation agree",
        )


def test_rollout_profile_rejects_blocked_or_stale_evidence_even_after_safe_samples() -> None:
    candidate = shadow_candidate()
    profile = RolloutEvidenceProfile((SurfaceEvidenceRequirement("backend-tests", 1, 1, 60),))
    stale = evaluate_rollout_evidence(
        profile,
        (
            evidence(
                candidate,
                NOW - timedelta(seconds=61),
                profile_id=profile.profile_id,
            ),
        ),
        now=NOW,
    )
    unsafe = replace(
        candidate,
        coverage_relation=CoverageRelation.OMITTED,
        event="delivery-2",
    )
    blocked = evaluate_rollout_evidence(
        profile,
        (
            evidence(
                candidate,
                NOW - timedelta(seconds=10),
                profile_id=profile.profile_id,
            ),
            evidence(
                unsafe,
                NOW - timedelta(seconds=1),
                profile_id=profile.profile_id,
            ),
        ),
        now=NOW,
    )

    assert stale.satisfied is False
    assert stale.surfaces[0].state is RolloutEvidenceState.STALE
    assert blocked.satisfied is False
    assert blocked.surfaces[0].state is RolloutEvidenceState.BLOCKED


def test_rollout_profile_does_not_count_evidence_from_another_profile() -> None:
    candidate = shadow_candidate()
    profile = RolloutEvidenceProfile((SurfaceEvidenceRequirement("backend-tests", 1, 1, 60),))
    other_profile = RolloutEvidenceProfile((SurfaceEvidenceRequirement("backend-tests", 2, 1, 60),))
    record = ShadowEvidenceRecord(
        profile_id=other_profile.profile_id,
        observed_at=NOW - timedelta(seconds=10),
        comparison=compare_full_ci(candidate, matching_observation(candidate)),
    )

    assessment = evaluate_rollout_evidence(profile, (record,), now=NOW)

    assert assessment.satisfied is False
    assert assessment.surfaces[0].state is RolloutEvidenceState.MISSING


@pytest.mark.parametrize(
    "field_name",
    ("policy_hash", "graph_hash", "baseline_plan", "candidate_plan"),
)
def test_candidate_rejects_missing_required_replay_identity(field_name: str) -> None:
    with pytest.raises(ValueError, match=f"{field_name} must be a non-empty string"):
        shadow_candidate(**{field_name: ""})


def shadow_candidate(**overrides: object) -> ShadowCandidate:
    values: dict[str, object] = {
        "repo": "acme/ci-coordinator",
        "event": "delivery-1",
        "base_sha": "base-sha",
        "head_sha": "head-sha",
        "config_epoch": "epoch-1",
        "policy_hash": "policy-hash",
        "diff_hash": "diff-hash",
        "graph_hash": "graph-hash",
        "baseline_plan": "baseline-plan-hash",
        "candidate_plan": "candidate-plan-hash",
        "surface": "backend-tests",
        "coverage_relation": CoverageRelation.COVERED,
        "actual_full_ci_result": FullCiResult.PASSED,
        "unsafe_candidate": False,
    }
    values.update(overrides)
    return ShadowCandidate(**values)  # type: ignore[arg-type]


def matching_observation(candidate: ShadowCandidate) -> FullCiObservation:
    return FullCiObservation(
        repo=candidate.repo,
        event=candidate.event,
        base_sha=candidate.base_sha,
        head_sha=candidate.head_sha,
        config_epoch=candidate.config_epoch,
        policy_hash=candidate.policy_hash,
        diff_hash=candidate.diff_hash,
        graph_hash=candidate.graph_hash,
        baseline_plan=candidate.baseline_plan,
        candidate_plan=candidate.candidate_plan,
        actual_full_ci_result=candidate.actual_full_ci_result,
    )


def evidence(
    candidate: ShadowCandidate,
    observed_at: datetime,
    *,
    profile_id: str,
) -> ShadowEvidenceRecord:
    return ShadowEvidenceRecord(
        profile_id=profile_id,
        observed_at=observed_at,
        comparison=compare_full_ci(candidate, matching_observation(candidate)),
    )


class RecordingRecorder:
    def __init__(self) -> None:
        self.records: list[ShadowEvidenceRecord] = []

    async def record(self, record: ShadowEvidenceRecord) -> ShadowEvidenceStored:
        self.records.append(record)
        return ShadowEvidenceStored(record)


class ConflictingRecorder:
    def __init__(self, existing: ShadowEvidenceRecord) -> None:
        self._existing = existing

    async def record(self, record: ShadowEvidenceRecord) -> ShadowEvidenceConflict:
        del record
        return ShadowEvidenceConflict(self._existing)
