"""Domain witnesses for the registered-epoch review subject (CI-018)."""

from __future__ import annotations

import json
from copy import copy
from dataclasses import replace

import pytest
from registered_epoch_review_support import (
    REGISTRANT,
    admitted_draft,
    registered_attestation,
    registered_command,
    registered_subject,
)
from repository_activation_support import SCOPE

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpoch
from ci_coordinator.proposal_review import (
    PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE,
    REGISTERED_EPOCH_REVIEW_ACCEPTED_EVENT_TYPE,
    PreparedRegisteredEpochReview,
    RegisteredEpochReviewCommand,
    RegisteredEpochReviewDraft,
    RegisteredEpochReviewRecord,
    compare_policy_drafts,
    is_pair_owned_proposal_review_event_type,
    prepare_registered_epoch_review,
)

_DRAFT = admitted_draft()
_REVISION = "b" * 40
_INVENTORY = "c" * 64


def _review_draft(
    command: RegisteredEpochReviewCommand | None = None,
) -> RegisteredEpochReviewDraft:
    return RegisteredEpochReviewDraft(
        command=command or registered_command(_DRAFT),
        provider_revision=_REVISION,
        inventory_digest=_INVENTORY,
        subject=registered_subject(_DRAFT),
        semantic_diff=compare_policy_drafts(None, _DRAFT),
    )


@pytest.mark.parametrize(
    ("operation_id", "target_epoch_id", "source_hash"),
    [
        ("review\0operation", _DRAFT.epoch_id, _DRAFT.source_hash),
        ("review", "A" * 64, _DRAFT.source_hash),
        ("review", _DRAFT.epoch_id, "short"),
    ],
    ids=("nul-operation", "uppercase-epoch", "short-source-hash"),
)
def test_command_rejects_nul_operation_and_non_digest_subject(
    operation_id: str, target_epoch_id: str, source_hash: str
) -> None:
    with pytest.raises(ValueError):
        RegisteredEpochReviewCommand(
            scope=SCOPE,
            operation_id=operation_id,
            target_epoch_id=target_epoch_id,
            expected_source_hash=source_hash,
            expected_active=None,
            actor="owner:42",
        )


@pytest.mark.parametrize(
    "command",
    [
        registered_command(_DRAFT, source_hash="0" * 64),
        registered_command(admitted_draft(signal="Other")),
    ],
    ids=("source-hash-mismatch", "other-epoch"),
)
def test_draft_rejects_a_retained_epoch_other_than_the_bound_subject(
    command: RegisteredEpochReviewCommand,
) -> None:
    with pytest.raises(ValueError):
        RegisteredEpochReviewDraft(
            command=command,
            provider_revision=_REVISION,
            inventory_digest=_INVENTORY,
            subject=registered_subject(_DRAFT),
            semantic_diff=compare_policy_drafts(None, _DRAFT),
        )


def test_prepared_review_audit_binds_subject_registrant_inventory_and_is_single_use() -> None:
    draft = _review_draft()
    attestation = registered_attestation(
        draft.command, revision=_REVISION, inventory_digest=_INVENTORY
    )

    prepared = prepare_registered_epoch_review(
        draft,
        attestation=attestation,
        occurred_at="2026-10-11T12:00:00.000Z",
    )
    event = prepared._take_audit_event()
    payload = json.loads(event.payload_canonical_bytes)

    assert event.event_type == REGISTERED_EPOCH_REVIEW_ACCEPTED_EVENT_TYPE
    assert event.subject_id == f"registered-epoch:{_DRAFT.epoch_id}"
    assert event.idempotency_key == f"registered-epoch-review:1:2:{draft.command.operation_id}"
    assert payload["schemaVersion"] == "registered-epoch-review-acceptance-audit/v1"
    assert payload["targetEpochId"] == _DRAFT.epoch_id
    assert payload["sourceHash"] == _DRAFT.source_hash
    assert payload["providerRevision"] == _REVISION
    assert payload["inventoryDigest"] == _INVENTORY
    assert payload["registrationOperationId"] == REGISTRANT.operation_id
    assert payload["registrantActor"] == REGISTRANT.actor
    assert payload["registrationAuditEventId"] == REGISTRANT.audit_event_id
    assert payload["initiatingActor"] == draft.command.actor
    assert "proposalManifestId" not in payload
    with pytest.raises(ValueError, match="consumed"):
        prepared._take_audit_event()
    with pytest.raises(TypeError):
        copy(prepared)
    with pytest.raises(TypeError):
        PreparedRegisteredEpochReview(
            object(), draft=draft, attestation=attestation, audit_event=event
        )


@pytest.mark.parametrize(
    ("revision", "inventory"),
    [("d" * 40, _INVENTORY), (_REVISION, "e" * 64)],
    ids=("revision", "inventory"),
)
def test_preparation_and_record_reject_an_attestation_for_another_inventory(
    revision: str, inventory: str
) -> None:
    # Kills: dropping either the revision or the inventory-digest attestation binding.
    draft = _review_draft()
    attestation = registered_attestation(
        draft.command, revision=revision, inventory_digest=inventory
    )

    with pytest.raises(ValueError, match="attestation binding"):
        prepare_registered_epoch_review(
            draft, attestation=attestation, occurred_at="2026-10-11T12:00:00.000Z"
        )
    with pytest.raises(ValueError, match="attestation binding"):
        RegisteredEpochReviewRecord.from_draft(
            draft,
            attestation=attestation,
            audit_event_id="audit_" + "5" * 32,
            audit_input_hash="6" * 64,
        )


def test_review_scope_must_bind_the_expected_active_pointer_scope() -> None:
    with pytest.raises(ValueError, match="expected active"):
        replace(
            registered_command(_DRAFT),
            expected_active=ActiveConfigEpoch(RepositoryScope(9, 9), "a" * 64, 1),
        )
    assert registered_command(_DRAFT).scope == SCOPE


@pytest.mark.parametrize(
    ("value", "owned"),
    [
        (PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE, True),
        (REGISTERED_EPOCH_REVIEW_ACCEPTED_EVENT_TYPE, True),
        ("config-epoch-registration/v1", False),
        (["registered-epoch-review-accepted/v1"], False),
    ],
)
def test_both_review_subject_events_are_pair_owned_and_reject_generic_append(
    value: object, owned: bool
) -> None:
    assert is_pair_owned_proposal_review_event_type(value) is owned
