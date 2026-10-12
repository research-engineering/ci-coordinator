"""Activation-authority witnesses for a reviewed registered epoch (CI-018)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import cast

import pytest
from registered_epoch_review_support import (
    REGISTRANT,
    RegisteredEpochStore,
    ScriptedDiscovery,
    admitted_draft,
    discovered_inventory,
    registered_review_record,
    registered_subject,
)
from repository_activation_support import ACTOR, NOW, PROFILE_DIGEST, REVIEWER, SCOPE

from ci_coordinator.app.repository_activation import RepositoryActivationAuthorityService
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpoch, ActiveConfigEpochSnapshot
from ci_coordinator.control_plane_identity import GitHubReviewerEvidence
from ci_coordinator.proposal_review import (
    GitHubReviewerRejected,
    GitHubReviewerUnavailable,
    RegisteredEpochActivationAuthority,
    RegisteredEpochActivationGranted,
    RegisteredEpochActivationStore,
    RegisteredEpochRegistrant,
    RegisteredEpochReviewRecord,
    RegisteredEpochSubject,
    RegisteredEpochWithoutReceipt,
    RepositoryActivationRejected,
    RepositoryActivationStore,
    RepositoryActivationUnavailable,
)
from ci_coordinator.workflow_discovery import (
    WorkflowDiscoveryOutcome,
    WorkflowDiscoveryUnavailable,
)

_DRAFT = admitted_draft()
_INVENTORY = discovered_inventory()
_REVIEW = registered_review_record(_INVENTORY, _DRAFT)


@dataclass
class _PermissionReader:
    result: GitHubReviewerEvidence | Exception = REVIEWER
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
class _UntouchedProposalStore:
    calls: list[str] = field(default_factory=list)

    def __getattr__(self, name: str) -> object:
        async def record(*_args: object, **_kwargs: object) -> object:
            self.calls.append(name)
            raise AssertionError(f"registered activation touched proposal store: {name}")

        return record


def _store() -> RegisteredEpochStore:
    return RegisteredEpochStore(
        lookup=registered_subject(_DRAFT),
        candidate=_REVIEW,
        now=NOW + timedelta(seconds=2),
    )


def _authorize(
    store: RegisteredEpochStore,
    *,
    permission: _PermissionReader | None = None,
    discovery: ScriptedDiscovery | None = None,
    review_operation_id: str = "registered-review-1",
    source_hash: str = _DRAFT.source_hash,
) -> object:
    service = RepositoryActivationAuthorityService(
        store=cast(RepositoryActivationStore, _UntouchedProposalStore()),
        registered_epoch_store=cast(RegisteredEpochActivationStore, store),
        permission_reader=permission or _PermissionReader(),
        discovery=discovery or ScriptedDiscovery([_INVENTORY]),
        authority_profile_digest=PROFILE_DIGEST,
    )
    return asyncio.run(
        service.authorize_registered_epoch(
            actor=ACTOR,
            scope=SCOPE,
            target_epoch_id=_DRAFT.epoch_id,
            expected_source_hash=source_hash,
            review_operation_id=review_operation_id,
        )
    )


def _stages(store: RegisteredEpochStore) -> list[object]:
    return [call[0] if isinstance(call, tuple) else call for call in store.calls]


def test_authority_binds_fresh_recheck_after_db_time_and_rediscovery() -> None:
    store = _store()
    permission = _PermissionReader()
    discovery = ScriptedDiscovery([_INVENTORY])

    result = _authorize(store, permission=permission, discovery=discovery)

    assert isinstance(result, RegisteredEpochActivationGranted)
    assert result.authority.review is _REVIEW
    assert result.authority.rechecked_at == NOW + timedelta(seconds=2)
    assert _stages(store) == ["candidate", "active", "epoch", "current_time"]
    assert permission.calls == [(SCOPE, REVIEWER.user_id, REVIEWER.login)]
    assert discovery.calls == [(ACTOR, SCOPE, None)]


def test_registered_evidence_hash_binds_the_registrant_and_review_receipt() -> None:
    # Kills: omitting the registrant from the registered-epoch evidence projection.
    rechecked_at = NOW + timedelta(seconds=1)
    authority = RegisteredEpochActivationAuthority(_REVIEW, REVIEWER, rechecked_at)
    other_registrant = RegisteredEpochActivationAuthority(
        replace(
            _REVIEW,
            registrant=RegisteredEpochRegistrant(
                operation_id="register-0",
                actor=REGISTRANT.actor,
                audit_event_id=REGISTRANT.audit_event_id,
            ),
        ),
        REVIEWER,
        rechecked_at,
    )

    assert len(authority.evidence_hash) == 64
    assert (
        authority.evidence_hash
        == RegisteredEpochActivationAuthority(_REVIEW, REVIEWER, rechecked_at).evidence_hash
    )
    assert authority.evidence_hash != other_registrant.evidence_hash


@pytest.mark.parametrize(
    ("candidate", "selector", "source_hash"),
    [
        (None, "registered-review-1", _DRAFT.source_hash),
        (_REVIEW, "another-review", _DRAFT.source_hash),
        (_REVIEW, "registered-review-1", "0" * 64),
    ],
    ids=("absent-receipt", "other-operation", "source-mismatch"),
)
def test_missing_or_foreign_receipt_rejects_before_provider_io(
    candidate: object, selector: str, source_hash: str
) -> None:
    store = _store()
    store.candidate = cast(RegisteredEpochReviewRecord | None, candidate)
    permission = _PermissionReader()
    discovery = ScriptedDiscovery([_INVENTORY])

    result = _authorize(
        store,
        permission=permission,
        discovery=discovery,
        review_operation_id=selector,
        source_hash=source_hash,
    )

    assert isinstance(result, RepositoryActivationRejected)
    assert permission.calls == []
    assert discovery.calls == []


@pytest.mark.parametrize(
    ("current_revision", "granted"),
    [(1, True), (3, False)],
    ids=("reviewed-a-at-1", "a-b-a-at-3"),
)
def test_aba_baseline_rejects_before_provider_io(current_revision: int, granted: bool) -> None:
    # Kills: comparing only the active epoch id instead of the complete (epoch, revision) pointer.
    base_draft = admitted_draft(signal="Baseline")
    reviewed_base = ActiveConfigEpochSnapshot(
        ActiveConfigEpoch(SCOPE, base_draft.epoch_id, 1), base_draft
    )
    store = _store()
    store.candidate = registered_review_record(_INVENTORY, _DRAFT, base=reviewed_base)
    store.active = ActiveConfigEpochSnapshot(
        ActiveConfigEpoch(SCOPE, base_draft.epoch_id, current_revision), base_draft
    )
    permission = _PermissionReader()

    result = _authorize(store, permission=permission)

    assert isinstance(result, RegisteredEpochActivationGranted) is granted
    assert (permission.calls != []) is granted


def test_first_activation_race_rejects_a_receipt_that_reviewed_exact_absence() -> None:
    store = _store()
    store.active = ActiveConfigEpochSnapshot(ActiveConfigEpoch(SCOPE, _DRAFT.epoch_id, 1), _DRAFT)
    permission = _PermissionReader()

    result = _authorize(store, permission=permission)

    assert isinstance(result, RepositoryActivationRejected)
    assert permission.calls == []


@pytest.mark.parametrize(
    ("lookup", "expected"),
    [
        (None, RepositoryActivationRejected),
        (RegisteredEpochWithoutReceipt(), RepositoryActivationRejected),
        (
            RegisteredEpochSubject(
                draft=_DRAFT,
                registrant=RegisteredEpochRegistrant(
                    operation_id="register-0",
                    actor=REGISTRANT.actor,
                    audit_event_id=REGISTRANT.audit_event_id,
                ),
            ),
            RepositoryActivationRejected,
        ),
    ],
    ids=("epoch-absent", "receipt-absent", "registrant-changed"),
)
def test_retained_epoch_is_reloaded_and_must_match_the_reviewed_registrant(
    lookup: object, expected: type
) -> None:
    store = _store()
    store.lookup = cast(RegisteredEpochWithoutReceipt | None, lookup)
    permission = _PermissionReader()

    result = _authorize(store, permission=permission)

    assert isinstance(result, expected)
    assert permission.calls == []


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (discovered_inventory(revision="b" * 40), RepositoryActivationRejected),
        (discovered_inventory(shape="changed"), RepositoryActivationRejected),
        (discovered_inventory(shape="open_graph"), RepositoryActivationRejected),
        (WorkflowDiscoveryUnavailable("overloaded"), RepositoryActivationUnavailable),
        (WorkflowDiscoveryUnavailable("provider_unavailable"), RepositoryActivationUnavailable),
    ],
    ids=("head-moved", "inventory-changed", "graph-open", "overloaded", "unavailable"),
)
def test_activation_time_rediscovery_requires_the_exact_reviewed_inventory(
    current: WorkflowDiscoveryOutcome, expected: type
) -> None:
    # Kills: provisional owner decision D4 relaxed to revision-only or digest-only freshness.
    result = _authorize(_store(), discovery=ScriptedDiscovery([current]))

    assert isinstance(result, expected)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (GitHubReviewerRejected("revoked"), RepositoryActivationRejected),
        (GitHubReviewerUnavailable("down"), RepositoryActivationUnavailable),
        (
            GitHubReviewerEvidence(REVIEWER.user_id, REVIEWER.login, "admin"),
            RepositoryActivationRejected,
        ),
    ],
    ids=("revoked", "provider-unavailable", "permission-changed"),
)
def test_github_recheck_failure_or_change_is_not_authority(
    failure: GitHubReviewerEvidence | Exception, expected: type
) -> None:
    result = _authorize(_store(), permission=_PermissionReader(failure))

    assert isinstance(result, expected)


def test_expired_receipt_time_is_rejected_after_recheck() -> None:
    store = _store()
    store.now = _REVIEW.attestation.reviewer.expires_at

    result = _authorize(store)

    assert isinstance(result, RepositoryActivationRejected)


@pytest.mark.parametrize("stage", ["candidate", "active", "epoch", "time"])
def test_storage_unavailable_at_each_activation_stage_is_unavailable(stage: str) -> None:
    store = _store()
    store.unavailable_at = stage
    permission = _PermissionReader()

    result = _authorize(store, permission=permission)

    assert isinstance(result, RepositoryActivationUnavailable)
    assert permission.calls == []
