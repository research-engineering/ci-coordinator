"""Stable GitHub attempt evidence promotion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from ci_economics.factories import CONTRACT

from ci_coordinator.ci_economics import ProviderAttemptDeferred, ProviderAttemptSnapshot
from ci_coordinator.ci_economics.collection import ProviderCollectionFailureReason
from ci_coordinator.ci_economics.model import AttemptIdentity, RunnerIdentity, WorkflowConclusion
from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.integrations.github.ci_economics_provider import GitHubCiEconomicsProvider
from ci_coordinator.integrations.github.reconciliation_observer import (
    GitHubActionsReconciliationObserver,
    GitHubReconciliationObservationError,
    ObservationFailureReason,
)
from ci_coordinator.integrations.github.reconciliation_observer_decoding import ProviderJob
from ci_coordinator.reconciliation import ReconciliationSubject
from ci_coordinator.reconciliation.observation import SignalStatus

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
SUBJECT = ReconciliationSubject.create(
    installation_id=101,
    repository_id=202,
    event_name="push",
    ref="refs/heads/main",
    base_sha="a" * 40,
    head_sha="b" * 40,
    workflow_run_id=303,
    run_attempt=2,
)
RUNNER = RunnerIdentity(11, "runner-a", 12, "linux")
SOURCE = ReconciliationCollectionSource(SUBJECT, CONTRACT)
PROVIDER_SOURCE = ProviderRunCollectionSource(SOURCE.attempt, NOW, "2026-03-10", "e" * 64)


@dataclass
class _Observer:
    outcomes: list[tuple[ProviderJob, ...] | GitHubReconciliationObservationError]
    attempts: list[AttemptIdentity] = field(default_factory=list)

    async def load_attempt_identity(self, attempt: AttemptIdentity) -> tuple[ProviderJob, ...]:
        self.attempts.append(attempt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, GitHubReconciliationObservationError):
            raise outcome
        return outcome


def _provider(observer: _Observer) -> GitHubCiEconomicsProvider:
    return GitHubCiEconomicsProvider(cast(GitHubActionsReconciliationObserver, observer))


def _job(
    job_id: int,
    *,
    status: SignalStatus = "completed",
    conclusion: WorkflowConclusion | None = "success",
    completed_at: datetime = NOW + timedelta(minutes=2),
) -> ProviderJob:
    return ProviderJob(
        job_id=job_id,
        name=f"job-{job_id}",
        status=status,
        conclusion=conclusion,
        head_sha=SUBJECT.head_sha,
        started_at=NOW,
        completed_at=completed_at,
        labels=("linux", "self-hosted"),
        runner=RUNNER,
    )


@pytest.mark.parametrize(
    "source", [SOURCE, PROVIDER_SOURCE], ids=["reconciliation", "provider-run"]
)
def test_two_equal_complete_reads_promote_one_provider_snapshot(source: CollectionSource) -> None:
    jobs = (_job(2), _job(1, conclusion="action_required"))
    observer = _Observer([jobs, tuple(reversed(jobs))])

    outcome = asyncio.run(_provider(observer).load_stable(source))

    assert isinstance(outcome, ProviderAttemptSnapshot)
    assert observer.attempts == [source.attempt, source.attempt]
    assert outcome.subject_id == source.source_id
    assert outcome.attempt == source.attempt
    assert [job.provider_job_id for job in outcome.jobs] == [1, 2]
    assert outcome.jobs[0].conclusion == "action_required"
    assert all(job.delivery_id is None for job in outcome.jobs)


@pytest.mark.parametrize(
    ("first", "second", "reason"),
    [
        ((), (), "provider_incomplete"),
        ((_job(1, status="in_progress", conclusion=None),), (), "provider_not_terminal"),
        ((_job(1),), (), "provider_incomplete"),
        (
            (_job(1),),
            (_job(1, completed_at=NOW + timedelta(minutes=3)),),
            "provider_unstable",
        ),
    ],
    ids=["first-empty", "first-active", "second-empty", "changed-fact"],
)
def test_incomplete_nonterminal_or_changing_reads_are_never_promoted(
    first: tuple[ProviderJob, ...],
    second: tuple[ProviderJob, ...],
    reason: ProviderCollectionFailureReason,
) -> None:
    observer = _Observer([first, second])

    outcome = asyncio.run(_provider(observer).load_stable(SOURCE))

    assert outcome == ProviderAttemptDeferred(reason)


@pytest.mark.parametrize(
    ("provider_reason", "collection_reason"),
    [
        (ObservationFailureReason.PROVIDER_UNAVAILABLE, "provider_unavailable"),
        (ObservationFailureReason.PROVIDER_BINDING_MISMATCH, "provider_binding_mismatch"),
        (ObservationFailureReason.REPOSITORY_MISMATCH, "provider_binding_mismatch"),
        (ObservationFailureReason.JOBS_TRUNCATED, "provider_incomplete"),
        (ObservationFailureReason.JOBS_MALFORMED, "provider_malformed"),
    ],
)
def test_provider_errors_are_closed_into_collection_failure_algebra(
    provider_reason: ObservationFailureReason,
    collection_reason: ProviderCollectionFailureReason,
) -> None:
    observer = _Observer([GitHubReconciliationObservationError(provider_reason)])

    outcome = asyncio.run(_provider(observer).load_stable(SOURCE))

    assert outcome == ProviderAttemptDeferred(collection_reason)


@pytest.mark.parametrize("source", [SUBJECT, SOURCE.attempt, None])
def test_provider_rejects_a_non_source_before_reading(source: object) -> None:
    observer = _Observer([])
    with pytest.raises(TypeError, match="exact collection source"):
        asyncio.run(_provider(observer).load_stable(cast(CollectionSource, source)))
    assert not observer.attempts


@pytest.mark.parametrize("sha_length", [41, 64])
def test_valid_reconciliation_outside_economics_identity_domain_is_deferred(
    sha_length: int,
) -> None:
    subject = ReconciliationSubject.create(
        installation_id=101,
        repository_id=202,
        event_name="push",
        ref="refs/heads/main",
        base_sha="a" * sha_length,
        head_sha="b" * sha_length,
        workflow_run_id=303,
        run_attempt=2,
    )
    observer = _Observer([])

    outcome = asyncio.run(
        _provider(observer).load_stable(ReconciliationCollectionSource(subject, CONTRACT))
    )

    assert outcome == ProviderAttemptDeferred("provider_malformed")
    assert not observer.attempts
