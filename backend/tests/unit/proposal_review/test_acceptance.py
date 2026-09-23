from __future__ import annotations

import json
from copy import copy, deepcopy
from dataclasses import replace
from typing import cast

import pytest
from repository_activation_support import ACTOR, SCOPE, attestation_for

from ci_coordinator.audit_replay import PreparedAuditEvent
from ci_coordinator.config_control import (
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_epochs import ActiveConfigEpoch
from ci_coordinator.kernel import canonical_json
from ci_coordinator.proposal_review import (
    PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE,
    PreparedProposalReview,
    ProposalReviewCommand,
    ProposalReviewDraft,
    RepositoryAttestationEvidence,
    compare_policy_drafts,
    prepare_proposal_review,
)

PROPOSAL_DIGEST = "f" * 64


def test_operation_identity_rejects_postgresql_unrepresentable_nul() -> None:
    with pytest.raises(ValueError, match="cannot contain NUL"):
        ProposalReviewCommand(
            scope=SCOPE,
            operation_id="review\0operation",
            expected_manifest_id="proposal:" + "a" * 32,
            expected_active=None,
            actor="owner:42",
        )


def test_prepared_acceptance_binds_minimal_redacted_audit_evidence_once() -> None:
    target = _draft()
    command = ProposalReviewCommand(
        scope=SCOPE,
        operation_id="review-1",
        expected_manifest_id="proposal:" + "a" * 32,
        expected_active=None,
        actor=ACTOR,
    )
    review = ProposalReviewDraft(
        command=command,
        provider_revision="b" * 40,
        inventory_digest="c" * 64,
        proposal_digest=PROPOSAL_DIGEST,
        target=target,
        semantic_diff=compare_policy_drafts(None, target),
    )

    attestation = attestation_for(
        command=command,
        revision=review.provider_revision,
        proposal_digest=review.proposal_digest,
    )
    prepared = prepare_proposal_review(
        review,
        attestation=attestation,
        occurred_at="2026-07-19T10:00:00.000Z",
    )
    event = prepared._take_audit_event()

    assert event.event_type == PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE
    assert event.actor == ACTOR
    assert event.subject_id == command.expected_manifest_id
    assert event.installation_id == 1
    assert event.repository_id == 2
    payload = json.loads(event.payload_canonical_bytes)
    assert "Full CI" not in repr(payload)
    assert payload["targetEpochId"] == target.epoch_id
    assert payload["attestationIssuedAt"] == (attestation.transaction.binding.issued_at.isoformat())
    assert payload["sessionHandleDigest"] == (
        attestation.transaction.binding.session_handle_digest.hex()
    )
    with pytest.raises(ValueError, match="already consumed"):
        prepared._take_audit_event()

    for operation in (copy, deepcopy):
        fresh = prepare_proposal_review(
            review,
            attestation=attestation,
            occurred_at="2026-07-19T10:00:00.000Z",
        )
        with pytest.raises(TypeError, match="cannot be copied"):
            operation(fresh)


def test_prepared_capability_and_cross_scope_review_cannot_be_forged() -> None:
    target = _draft()
    command = ProposalReviewCommand(
        scope=SCOPE,
        operation_id="review-1",
        expected_manifest_id="proposal:" + "a" * 32,
        expected_active=None,
        actor=ACTOR,
    )
    review = ProposalReviewDraft(
        command=command,
        provider_revision="b" * 40,
        inventory_digest="c" * 64,
        proposal_digest=PROPOSAL_DIGEST,
        target=target,
        semantic_diff=compare_policy_drafts(None, target),
    )

    with pytest.raises(TypeError, match="cannot be constructed"):
        PreparedProposalReview(
            object(),
            draft=review,
            attestation=cast(RepositoryAttestationEvidence, object()),
            audit_event=cast(PreparedAuditEvent, object()),
        )

    active = ActiveConfigEpoch(SCOPE, target.epoch_id, 1)
    with pytest.raises(ValueError, match="crosses its scope or epoch bindings"):
        replace(review, command=replace(command, expected_active=active))


def _draft() -> ValidatedEpochDraft:
    result = admit_policy_document(
        canonical_json(
            {
                "schemaVersion": "ci-repository-policy/v1",
                "repository": {
                    "installationId": SCOPE.installation_id,
                    "repositoryId": SCOPE.repository_id,
                    "owner": "example",
                    "name": "repo",
                    "defaultBranch": "master",
                    "rules": [
                        {
                            "name": "default-ci",
                            "on": {"event": "push", "branches": ["master"]},
                            "mode": "observe",
                            "expectedSignals": [
                                {
                                    "kind": "workflow",
                                    "name": "Full CI",
                                    "workflowFile": ".github/workflows/ci.yml",
                                    "source": "native",
                                    "requiredConclusion": "success",
                                    "required": True,
                                }
                            ],
                            "omittedSignals": [],
                        }
                    ],
                    "dynamicCi": None,
                },
            }
        ),
        "json",
    )
    assert isinstance(result, ValidatedEpochDraft)
    return result
