"""Project terminal Full CI signal outcomes into immutable shadow evidence."""

from __future__ import annotations

from ci_coordinator.kernel import Clock
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.reconciliation import (
    ReconciliationAttemptClaim,
    ReconciliationFinding,
    ReconciliationResult,
)
from ci_coordinator.shadow_mode import (
    CoverageRelation,
    FullCiObservation,
    FullCiResult,
    ShadowCandidate,
    ShadowEvidenceRecord,
    compare_full_ci,
)


class ShadowReconciliationProjector:
    """Build per-omission evidence only from conclusive Full CI outcomes."""

    def __init__(self, clock: Clock, runtime_metrics: RuntimeMetrics | None = None) -> None:
        self._clock = clock
        self._runtime_metrics = runtime_metrics

    def project(
        self,
        claim: ReconciliationAttemptClaim,
        result: ReconciliationResult,
    ) -> tuple[ShadowEvidenceRecord, ...]:
        context = claim.contract.candidate_evidence
        if (
            context is None
            or result.subject_id != claim.subject.subject_id
            or result.state not in {"success", "failure"}
        ):
            return ()
        observed_at = self._clock.now()
        full_ci_result = _full_ci_result(result)
        if full_ci_result is FullCiResult.MISSING:
            return ()
        records: list[ShadowEvidenceRecord] = []
        for signal in context.omitted_signals:
            candidate = ShadowCandidate(
                repo=context.repository,
                event=claim.subject.subject_id,
                base_sha=claim.subject.base_sha,
                head_sha=claim.subject.head_sha,
                config_epoch=context.config_epoch,
                policy_hash=context.policy_hash,
                diff_hash=context.diff_hash,
                graph_hash=context.graph_hash,
                baseline_plan=context.baseline_plan_id,
                candidate_plan=context.candidate_plan_id,
                surface=signal.signal_id,
                coverage_relation=CoverageRelation.COVERED,
                actual_full_ci_result=full_ci_result,
                unsafe_candidate=full_ci_result is FullCiResult.FAILED,
            )
            observation = FullCiObservation(
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
                actual_full_ci_result=full_ci_result,
            )
            comparison = compare_full_ci(candidate, observation)
            if self._runtime_metrics is not None:
                self._runtime_metrics.shadow_comparison(comparison.classification)
            records.append(
                ShadowEvidenceRecord(
                    profile_id=context.profile_id,
                    observed_at=observed_at,
                    comparison=comparison,
                )
            )
        return tuple(records)


def _full_ci_result(result: ReconciliationResult) -> FullCiResult:
    if result.state == "success":
        return FullCiResult.PASSED
    if any(_is_conclusive_failure(finding) for finding in result.findings):
        return FullCiResult.FAILED
    return FullCiResult.MISSING


def _is_conclusive_failure(finding: ReconciliationFinding) -> bool:
    return finding.kind == "failed_expected_signal"
