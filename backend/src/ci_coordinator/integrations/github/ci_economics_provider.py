"""GitHub adapter for stable, bounded CI economics attempt evidence."""

from __future__ import annotations

from ci_coordinator.ci_economics import (
    AttemptIdentity,
    JobTiming,
    ProviderAttemptDeferred,
    ProviderAttemptSnapshot,
    WorkflowJobFact,
)
from ci_coordinator.ci_economics.collection import ProviderCollectionFailureReason
from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.integrations.github.reconciliation_observer import (
    GitHubActionsReconciliationObserver,
    GitHubReconciliationObservationError,
    ObservationFailureReason,
)
from ci_coordinator.integrations.github.reconciliation_observer_decoding import ProviderJob
from ci_coordinator.kernel import hash_object


class GitHubCiEconomicsProvider:
    """Promote two equal complete provider reads into one semantic snapshot."""

    def __init__(self, observer: GitHubActionsReconciliationObserver) -> None:
        self._observer = observer

    async def load_stable(
        self,
        source: CollectionSource,
    ) -> ProviderAttemptSnapshot | ProviderAttemptDeferred:
        if (
            type(source) is not ReconciliationCollectionSource
            and type(source) is not ProviderRunCollectionSource
        ):
            raise TypeError("CI economics provider requires an exact collection source")
        try:
            attempt = source.attempt
        except ValueError:
            return ProviderAttemptDeferred("provider_malformed")
        try:
            first = await self._observer.load_attempt_identity(attempt)
            if not first:
                return ProviderAttemptDeferred("provider_incomplete")
            if not _all_terminal(first):
                return ProviderAttemptDeferred("provider_not_terminal")
            second = await self._observer.load_attempt_identity(attempt)
            if not second:
                return ProviderAttemptDeferred("provider_incomplete")
            if not _all_terminal(second):
                return ProviderAttemptDeferred("provider_not_terminal")
        except GitHubReconciliationObservationError as error:
            return ProviderAttemptDeferred(_failure_reason(error.reason))

        first_snapshot = _provider_snapshot(source, first)
        second_snapshot = _provider_snapshot(source, second)
        if first_snapshot.snapshot_digest != second_snapshot.snapshot_digest:
            return ProviderAttemptDeferred("provider_unstable")
        return second_snapshot


def _all_terminal(jobs: tuple[ProviderJob, ...]) -> bool:
    return all(job.status == "completed" and job.conclusion is not None for job in jobs)


def _provider_snapshot(
    source: CollectionSource,
    jobs: tuple[ProviderJob, ...],
) -> ProviderAttemptSnapshot:
    attempt = source.attempt
    facts = tuple(_provider_job_fact(attempt, job) for job in sorted(jobs, key=lambda x: x.job_id))
    digest = hash_object(
        {
            "attempt": attempt.canonical_mapping(),
            "jobs": [job.canonical_mapping() for job in facts],
        }
    )
    return ProviderAttemptSnapshot(
        subject_id=source.source_id,
        attempt=attempt,
        snapshot_digest=digest,
        jobs=facts,
    )


def _provider_job_fact(attempt: AttemptIdentity, job: ProviderJob) -> WorkflowJobFact:
    if job.status != "completed" or job.conclusion is None:
        raise ValueError("provider snapshot requires terminal jobs")
    timing = JobTiming(None, job.started_at, job.completed_at)
    mapping = {
        "attempt": attempt.canonical_mapping(),
        "providerJobId": job.job_id,
        "name": job.name,
        "conclusion": job.conclusion,
        "timing": timing.canonical_mapping(),
        "labels": list(job.labels),
        "runner": job.runner.canonical_mapping(),
    }
    return WorkflowJobFact(
        attempt=attempt,
        provider_job_id=job.job_id,
        name=job.name,
        conclusion=job.conclusion,
        timing=timing,
        labels=job.labels,
        runner=job.runner,
        semantic_hash=hash_object(mapping),
        delivery_id=None,
    )


def _failure_reason(reason: ObservationFailureReason) -> ProviderCollectionFailureReason:
    if reason is ObservationFailureReason.PROVIDER_UNAVAILABLE:
        return "provider_unavailable"
    if reason in {
        ObservationFailureReason.PROVIDER_BINDING_MISMATCH,
        ObservationFailureReason.REPOSITORY_MISMATCH,
    }:
        return "provider_binding_mismatch"
    if reason is ObservationFailureReason.JOBS_TRUNCATED:
        return "provider_incomplete"
    return "provider_malformed"
