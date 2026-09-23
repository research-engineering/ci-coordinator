from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.control_plane_identity import (
    GitHubReviewerEvidence,
    GitHubReviewerPrincipal,
    ReviewerStepUpBinding,
)
from ci_coordinator.proposal_review import (
    ProposalReviewCommand,
    ProposalReviewDraft,
    ProposalReviewRecord,
    RepositoryAttestationEvidence,
    RepositoryAttestationTransaction,
    compare_policy_drafts,
)
from ci_coordinator.workflow_discovery import (
    RepositoryIdentity,
    RepositoryWorkflowSnapshot,
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryService,
    WorkflowSource,
)
from ci_coordinator.workflow_discovery.source import git_blob_sha1

SCOPE = RepositoryScope(1, 2)
NOW = datetime(2026, 9, 2, 10, tzinfo=UTC)
ACTOR = "keycloak-human:v1:" + "1" * 64
PROFILE_DIGEST = "2" * 64
REVIEWER = GitHubReviewerEvidence(17, "maintainer", "maintain")


def completed_discovery(
    *,
    revision: str = "a" * 40,
    signal: str = "Full CI",
) -> WorkflowDiscoveryCompleted:
    content = f"""\
name: CI
on:
  workflow_dispatch:
  push:
jobs:
  full:
    name: {signal}
    runs-on: ubuntu-latest
    steps:
      - run: python -m pytest
""".encode()
    source = WorkflowSource(
        ".github/workflows/ci.yml",
        git_blob_sha1(content),
        len(content),
        content,
    )
    snapshot = RepositoryWorkflowSnapshot.create(
        repository=RepositoryIdentity(SCOPE, "example", "repo", "master"),
        revision=revision,
        sources=(source,),
    )
    result = asyncio.run(
        WorkflowDiscoveryService(authorizer=_Allow(), reader=_Reader(snapshot))(
            actor=ACTOR,
            scope=SCOPE,
            revision=None,
        )
    )
    assert isinstance(result, WorkflowDiscoveryCompleted)
    assert result.proposal.state == "reviewable"
    return result


def review_record(
    completed: WorkflowDiscoveryCompleted | None = None,
    *,
    manifest_id: str | None = None,
    provider_revision: str | None = None,
    proposal_digest: str | None = None,
    expires_at: datetime | None = None,
    reviewer: GitHubReviewerEvidence = REVIEWER,
) -> ProposalReviewRecord:
    discovered = completed or completed_discovery()
    proposal = discovered.proposal
    target = proposal.admission
    assert type(target) is ValidatedEpochDraft
    command = ProposalReviewCommand(
        scope=SCOPE,
        operation_id="review-1",
        expected_manifest_id=manifest_id or proposal.manifest_id,
        expected_active=None,
        actor=ACTOR,
    )
    retained_revision = provider_revision or discovered.report.revision
    retained_proposal_digest = proposal_digest or proposal.proposal_digest
    expiry = expires_at or NOW + timedelta(minutes=5)
    attestation = attestation_for(
        command=command,
        revision=retained_revision,
        proposal_digest=retained_proposal_digest,
        expires_at=expiry,
        reviewer=reviewer,
    )
    draft = ProposalReviewDraft(
        command=command,
        provider_revision=retained_revision,
        inventory_digest=discovered.report.inventory_digest,
        proposal_digest=retained_proposal_digest,
        target=target,
        semantic_diff=compare_policy_drafts(None, target),
    )
    return ProposalReviewRecord.from_draft(
        draft,
        attestation=attestation,
        audit_event_id="audit_" + "5" * 32,
        audit_input_hash="6" * 64,
    )


def attestation_for(
    *,
    command: ProposalReviewCommand,
    revision: str,
    proposal_digest: str,
    expires_at: datetime | None = None,
    reviewer: GitHubReviewerEvidence = REVIEWER,
) -> RepositoryAttestationEvidence:
    expiry = expires_at or NOW + timedelta(minutes=5)
    active = command.expected_active
    return RepositoryAttestationEvidence(
        RepositoryAttestationTransaction(
            b"3" * 32,
            ReviewerStepUpBinding(
                session_handle_digest=b"4" * 32,
                initiating_actor=ACTOR,
                scope=command.scope,
                operation_id=command.operation_id,
                proposal_manifest_id=command.expected_manifest_id,
                revision=revision,
                proposal_digest=proposal_digest,
                expected_active_epoch_id=None if active is None else active.epoch_id,
                expected_active_revision=None if active is None else active.revision,
                authority_profile_digest=PROFILE_DIGEST,
                issued_at=NOW,
                expires_at=expiry,
            ),
        ),
        GitHubReviewerPrincipal(
            evidence=reviewer,
            observed_at=NOW,
            expires_at=expiry,
        ),
    )


@dataclass
class _Allow:
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        return actor == ACTOR and scope == SCOPE


@dataclass
class _Reader:
    snapshot: RepositoryWorkflowSnapshot

    async def read(
        self,
        *,
        scope: RepositoryScope,
        revision: str | None,
    ) -> RepositoryWorkflowSnapshot:
        assert scope == SCOPE
        assert revision is None
        return self.snapshot
