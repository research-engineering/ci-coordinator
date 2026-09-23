from datetime import UTC, datetime

from ci_coordinator.app.shadow_reconciliation import ShadowReconciliationProjector
from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.kernel import FixedClock
from ci_coordinator.reconciliation import (
    CandidateEvidenceContext,
    OmittedSignal,
    ReconciliationAttemptClaim,
    ReconciliationClaimAcquired,
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationFinding,
    ReconciliationResult,
    ReconciliationSubject,
    acquire_reconciliation_claim,
    initial_convergence_state,
)
from ci_coordinator.shadow_mode import ShadowComparisonClassification

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)
SUBJECT = ReconciliationSubject.create(
    installation_id=100,
    repository_id=200,
    event_name="push",
    ref="refs/heads/main",
    base_sha="a" * 40,
    head_sha="b" * 40,
    workflow_run_id=7001,
    run_attempt=1,
)
OMISSION = OmittedSignal("backend-tests", "Backend tests")
SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)
POLICY = ReconciliationConvergencePolicy()
CONTEXT = CandidateEvidenceContext(
    profile_id="profile-1",
    repository="example-org/ci-coordinator",
    config_epoch="epoch-1",
    policy_hash="policy-1",
    diff_hash="diff-1",
    graph_hash="graph-1",
    baseline_plan_id="full-ci-1",
    candidate_plan_id="candidate-1",
    omitted_signals=(OMISSION,),
)
_acquired = acquire_reconciliation_claim(
    initial_convergence_state(NOW, POLICY),
    SUBJECT,
    ReconciliationContract((SIGNAL,), (), CONTEXT),
    0,
    worker_id="a" * 64,
    now=NOW,
    policy=POLICY,
)
assert isinstance(_acquired, ReconciliationClaimAcquired)
CLAIM: ReconciliationAttemptClaim = _acquired.claim


def test_successful_full_ci_projects_positive_evidence_for_each_candidate_omission() -> None:
    records = ShadowReconciliationProjector(FixedClock(NOW)).project(
        CLAIM,
        ReconciliationResult(SUBJECT.subject_id, "success", ()),
    )

    assert len(records) == 1
    record = records[0]
    assert record.key.profile_id == "profile-1"
    assert record.key.event == SUBJECT.subject_id
    assert record.key.surface == "backend-tests"
    assert record.observed_at == NOW
    assert record.comparison.classification is ShadowComparisonClassification.SAFE


def test_failed_candidate_omission_projects_blocking_evidence() -> None:
    records = ShadowReconciliationProjector(FixedClock(NOW)).project(
        CLAIM,
        ReconciliationResult(
            SUBJECT.subject_id,
            "failure",
            (
                ReconciliationFinding(
                    "failed_expected_signal",
                    "backend-tests",
                    ("observation-1",),
                    "expected signal Backend tests concluded failure",
                ),
            ),
        ),
    )

    assert len(records) == 1
    assert records[0].comparison.classification is ShadowComparisonClassification.UNSAFE
    assert records[0].comparison.candidate.unsafe_candidate is True


def test_any_full_ci_provider_failure_blocks_every_candidate_omission() -> None:
    records = ShadowReconciliationProjector(FixedClock(NOW)).project(
        CLAIM,
        ReconciliationResult(
            SUBJECT.subject_id,
            "failure",
            (
                ReconciliationFinding(
                    "failed_expected_signal",
                    "frontend-tests",
                    ("observation-2",),
                    "expected signal Frontend tests concluded failure",
                ),
            ),
        ),
    )

    assert len(records) == 1
    assert records[0].comparison.classification is ShadowComparisonClassification.UNSAFE


def test_inconclusive_candidate_signal_or_non_candidate_produces_no_evidence() -> None:
    projector = ShadowReconciliationProjector(FixedClock(NOW))
    acquired = acquire_reconciliation_claim(
        initial_convergence_state(NOW),
        SUBJECT,
        ReconciliationContract((SIGNAL,), ()),
        0,
        worker_id="b" * 64,
        now=NOW,
    )
    assert isinstance(acquired, ReconciliationClaimAcquired)
    no_candidate = acquired.claim

    assert (
        projector.project(
            CLAIM,
            ReconciliationResult(
                SUBJECT.subject_id,
                "failure",
                (
                    ReconciliationFinding(
                        "skipped_without_proof",
                        "backend-tests",
                        ("observation-3",),
                        "expected signal Backend tests was skipped without proof",
                    ),
                ),
            ),
        )
        == ()
    )
    assert (
        projector.project(
            CLAIM,
            ReconciliationResult("another-subject", "success", ()),
        )
        == ()
    )
    assert (
        projector.project(
            no_candidate,
            ReconciliationResult(SUBJECT.subject_id, "success", ()),
        )
        == ()
    )
