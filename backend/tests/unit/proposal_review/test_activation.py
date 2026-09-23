from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from repository_activation_support import NOW, REVIEWER, review_record

from ci_coordinator.control_plane_identity import GitHubReviewerEvidence
from ci_coordinator.proposal_review import RepositoryActivationAuthority


def test_exact_recheck_produces_stable_content_addressed_authority() -> None:
    review = review_record()

    first = RepositoryActivationAuthority(review, REVIEWER, NOW + timedelta(seconds=1))
    second = RepositoryActivationAuthority(review, REVIEWER, NOW + timedelta(seconds=1))

    assert first == second
    assert first.evidence_hash == second.evidence_hash


@pytest.mark.parametrize(
    "rechecked",
    [
        GitHubReviewerEvidence(18, REVIEWER.login, REVIEWER.permission),
        GitHubReviewerEvidence(REVIEWER.user_id, "other-maintainer", REVIEWER.permission),
        GitHubReviewerEvidence(REVIEWER.user_id, REVIEWER.login, "admin"),
    ],
    ids=("user-id", "login", "permission"),
)
def test_any_changed_reviewer_component_invalidates_authority(
    rechecked: GitHubReviewerEvidence,
) -> None:
    with pytest.raises(ValueError, match="reviewer evidence changed"):
        RepositoryActivationAuthority(review_record(), rechecked, NOW + timedelta(seconds=1))


@pytest.mark.parametrize(
    "observed_at",
    [NOW - timedelta(microseconds=1), NOW + timedelta(minutes=5)],
    ids=("before-receipt", "at-expiry"),
)
def test_db_observation_must_be_inside_the_half_open_receipt_window(
    observed_at: datetime,
) -> None:
    with pytest.raises(ValueError, match="outside the review receipt"):
        RepositoryActivationAuthority(
            review_record(),
            REVIEWER,
            observed_at,
        )


def test_authority_hash_binds_the_exact_db_observation_time() -> None:
    review = review_record()
    earlier = RepositoryActivationAuthority(review, REVIEWER, NOW + timedelta(seconds=1))
    later = replace(earlier, rechecked_at=NOW + timedelta(seconds=2))

    assert earlier.evidence_hash != later.evidence_hash
