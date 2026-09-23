"""Shared fail-closed classification for reviewer evidence reads."""

from __future__ import annotations

from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubOutcome,
    GitHubSuccess,
    GitHubUnavailable,
)
from ci_coordinator.proposal_review import GitHubReviewerRejected, GitHubReviewerUnavailable


def admit_reviewer_success(
    outcome: GitHubOutcome,
    *,
    operation: str,
    path: str,
) -> GitHubSuccess:
    if isinstance(outcome, GitHubUnavailable):
        if outcome.failure.kind in {"forbidden", "not_found"}:
            raise GitHubReviewerRejected("GitHub reviewer evidence was rejected")
        raise GitHubReviewerUnavailable("GitHub reviewer evidence is unavailable")
    if (
        not isinstance(outcome, GitHubSuccess)
        or outcome.response.pagination.termination != "not_paginated"
        or outcome.request.operation != operation
        or outcome.request.path != path
        or outcome.request.api_version != GITHUB_API_VERSION
    ):
        raise GitHubReviewerUnavailable("GitHub reviewer provider binding failed")
    return outcome
