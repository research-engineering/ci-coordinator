from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast

import pytest
from repository_activation_support import (
    ACTOR,
    NOW,
    PROFILE_DIGEST,
    REVIEWER,
    SCOPE,
    completed_discovery,
    review_record,
)

from ci_coordinator.app.repository_activation import RepositoryActivationAuthorityService
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import ActiveConfigEpoch, ActiveConfigEpochSnapshot
from ci_coordinator.control_plane_identity import GitHubReviewerEvidence
from ci_coordinator.proposal_review import (
    GitHubReviewerRejected,
    GitHubReviewerUnavailable,
    ProposalReviewRecord,
    ProposalReviewStoreUnavailable,
    RepositoryActivationGranted,
    RepositoryActivationRejected,
    RepositoryActivationStore,
    RepositoryActivationUnavailable,
)
from ci_coordinator.workflow_discovery import (
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryOutcome,
    WorkflowDiscoveryUnavailable,
)


def test_authorization_ages_permission_from_db_time_before_provider_io() -> None:
    completed = completed_discovery()
    review = review_record(completed)
    store = _Store(review, NOW + timedelta(seconds=2))
    permission = _PermissionReader(REVIEWER)
    discovery = _Discovery(completed)

    result = asyncio.run(
        _service(store, permission, discovery).authorize(
            actor=ACTOR,
            scope=SCOPE,
            target_epoch_id=review.target_epoch_id,
            proposal_manifest_id=review.command.expected_manifest_id,
        )
    )

    assert isinstance(result, RepositoryActivationGranted)
    assert result.authority.review is review
    assert result.authority.rechecked_at == NOW + timedelta(seconds=2)
    assert permission.calls == [(SCOPE, REVIEWER.user_id, REVIEWER.login)]
    assert discovery.calls == [(ACTOR, SCOPE, None)]
    assert store.calls == [
        (SCOPE, review.target_epoch_id, review.command.expected_manifest_id),
        ("active", SCOPE),
        "current_time",
    ]


def test_changed_active_baseline_stops_before_provider_io() -> None:
    completed = completed_discovery()
    review = review_record(completed)
    target = completed.proposal.admission
    assert type(target) is ValidatedEpochDraft
    store = _Store(
        review,
        NOW,
        active=ActiveConfigEpochSnapshot(
            active=ActiveConfigEpoch(SCOPE, target.epoch_id, 1),
            draft=target,
        ),
    )
    permission = _PermissionReader(REVIEWER)
    discovery = _Discovery(completed)

    result = asyncio.run(
        _service(store, permission, discovery).authorize(
            actor=ACTOR,
            scope=SCOPE,
            target_epoch_id=review.target_epoch_id,
            proposal_manifest_id=review.command.expected_manifest_id,
        )
    )

    assert isinstance(result, RepositoryActivationRejected)
    assert store.calls == [
        (SCOPE, review.target_epoch_id, review.command.expected_manifest_id),
        ("active", SCOPE),
    ]
    assert permission.calls == []
    assert discovery.calls == []


@pytest.mark.parametrize(
    ("review", "target_epoch_id", "manifest_id"),
    [
        (review_record(provider_revision="b" * 40), None, None),
        (review_record(proposal_digest="c" * 64), None, None),
        (review_record(), "d" * 64, None),
        (review_record(), None, "proposal:" + "e" * 32),
    ],
    ids=("revision", "proposal-digest", "target-epoch", "manifest"),
)
def test_each_changed_proposal_binding_fails_closed(
    review: ProposalReviewRecord,
    target_epoch_id: str | None,
    manifest_id: str | None,
) -> None:
    completed = completed_discovery()
    result = asyncio.run(
        _service(
            _Store(review, NOW + timedelta(seconds=1)),
            _PermissionReader(REVIEWER),
            _Discovery(completed),
        ).authorize(
            actor=ACTOR,
            scope=SCOPE,
            target_epoch_id=target_epoch_id or review.target_epoch_id,
            proposal_manifest_id=manifest_id or review.command.expected_manifest_id,
        )
    )

    assert isinstance(result, RepositoryActivationRejected)


@pytest.mark.parametrize(
    ("provider_result", "expected_type"),
    [
        (
            GitHubReviewerEvidence(REVIEWER.user_id, REVIEWER.login, "admin"),
            RepositoryActivationRejected,
        ),
        (GitHubReviewerRejected("revoked"), RepositoryActivationRejected),
        (GitHubReviewerUnavailable("provider unavailable"), RepositoryActivationUnavailable),
    ],
    ids=("changed", "revoked", "unavailable"),
)
def test_permission_change_revocation_and_unavailability_are_distinct_failures(
    provider_result: GitHubReviewerEvidence | Exception,
    expected_type: type[RepositoryActivationRejected] | type[RepositoryActivationUnavailable],
) -> None:
    completed = completed_discovery()
    review = review_record(completed)
    result = asyncio.run(
        _service(
            _Store(review, NOW + timedelta(seconds=1)),
            _PermissionReader(provider_result),
            _Discovery(completed),
        ).authorize(
            actor=ACTOR,
            scope=SCOPE,
            target_epoch_id=review.target_epoch_id,
            proposal_manifest_id=review.command.expected_manifest_id,
        )
    )

    assert isinstance(result, expected_type)


@pytest.mark.parametrize(
    ("outcome", "expected_type"),
    [
        (WorkflowDiscoveryForbidden(), RepositoryActivationRejected),
        (WorkflowDiscoveryUnavailable("provider_unavailable"), RepositoryActivationUnavailable),
    ],
    ids=("forbidden", "unavailable"),
)
def test_discovery_failure_uses_the_pre_provider_db_time_sample(
    outcome: WorkflowDiscoveryOutcome,
    expected_type: type[RepositoryActivationRejected] | type[RepositoryActivationUnavailable],
) -> None:
    review = review_record()
    store = _Store(review, NOW + timedelta(seconds=1))

    result = asyncio.run(
        _service(store, _PermissionReader(REVIEWER), _Discovery(outcome)).authorize(
            actor=ACTOR,
            scope=SCOPE,
            target_epoch_id=review.target_epoch_id,
            proposal_manifest_id=review.command.expected_manifest_id,
        )
    )

    assert isinstance(result, expected_type)
    assert store.calls[-1] == "current_time"


def test_receipt_expiry_uses_database_time_and_rejects_the_boundary() -> None:
    completed = completed_discovery()
    review = review_record(completed)

    result = asyncio.run(
        _service(
            _Store(review, NOW + timedelta(minutes=5)),
            _PermissionReader(REVIEWER),
            _Discovery(completed),
        ).authorize(
            actor=ACTOR,
            scope=SCOPE,
            target_epoch_id=review.target_epoch_id,
            proposal_manifest_id=review.command.expected_manifest_id,
        )
    )

    assert isinstance(result, RepositoryActivationRejected)


def test_long_discovery_cannot_postdate_permission_evidence() -> None:
    completed = completed_discovery()
    review = review_record(completed)
    store = _Store(review, NOW + timedelta(seconds=1))

    def advance_past_recheck_lifetime() -> None:
        store.now += timedelta(seconds=40)

    result = asyncio.run(
        _service(
            store,
            _PermissionReader(REVIEWER),
            _Discovery(completed, on_call=advance_past_recheck_lifetime),
        ).authorize(
            actor=ACTOR,
            scope=SCOPE,
            target_epoch_id=review.target_epoch_id,
            proposal_manifest_id=review.command.expected_manifest_id,
        )
    )

    assert isinstance(result, RepositoryActivationGranted)
    assert result.authority.rechecked_at == NOW + timedelta(seconds=1)
    assert store.now == NOW + timedelta(seconds=41)


@pytest.mark.parametrize("failure_owner", ["permission", "discovery"])
def test_unclassified_provider_programming_errors_propagate(failure_owner: str) -> None:
    completed = completed_discovery()
    review = review_record(completed)
    permission = _PermissionReader(
        TypeError("provider implementation defect") if failure_owner == "permission" else REVIEWER
    )
    discovery = _Discovery(
        completed,
        failure=(
            TypeError("discovery implementation defect") if failure_owner == "discovery" else None
        ),
    )

    with pytest.raises(TypeError, match="implementation defect"):
        asyncio.run(
            _service(_Store(review, NOW), permission, discovery).authorize(
                actor=ACTOR,
                scope=SCOPE,
                target_epoch_id=review.target_epoch_id,
                proposal_manifest_id=review.command.expected_manifest_id,
            )
        )


@dataclass
class _Store:
    review: ProposalReviewRecord | None
    now: datetime
    unavailable_at: str | None = None
    active: ActiveConfigEpochSnapshot | None = None
    calls: list[object] = field(default_factory=list)

    async def load_activation_candidate(
        self,
        *,
        scope: RepositoryScope,
        target_epoch_id: str,
        proposal_manifest_id: str,
    ) -> ProposalReviewRecord | None:
        self.calls.append((scope, target_epoch_id, proposal_manifest_id))
        if self.unavailable_at == "load":
            raise ProposalReviewStoreUnavailable
        return self.review

    async def current_time(self) -> datetime:
        self.calls.append("current_time")
        if self.unavailable_at == "time":
            raise ProposalReviewStoreUnavailable
        return self.now

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self.calls.append(("active", scope))
        if self.unavailable_at == "active":
            raise ProposalReviewStoreUnavailable
        return self.active


@dataclass
class _PermissionReader:
    result: GitHubReviewerEvidence | Exception
    calls: list[tuple[RepositoryScope, int, str]] = field(default_factory=list)

    async def recheck(
        self,
        *,
        scope: RepositoryScope,
        reviewer_user_id: int,
        reviewer_login: str,
    ) -> GitHubReviewerEvidence:
        self.calls.append((scope, reviewer_user_id, reviewer_login))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class _Discovery:
    outcome: WorkflowDiscoveryOutcome
    calls: list[tuple[str, RepositoryScope, str | None]] = field(default_factory=list)
    on_call: Callable[[], None] | None = None
    failure: Exception | None = None

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        revision: str | None,
    ) -> WorkflowDiscoveryOutcome:
        self.calls.append((actor, scope, revision))
        if self.on_call is not None:
            self.on_call()
        if self.failure is not None:
            raise self.failure
        return self.outcome


def _service(
    store: _Store,
    permission: _PermissionReader,
    discovery: _Discovery,
) -> RepositoryActivationAuthorityService:
    return RepositoryActivationAuthorityService(
        store=cast(RepositoryActivationStore, store),
        permission_reader=permission,
        discovery=discovery,
        authority_profile_digest=PROFILE_DIGEST,
    )
