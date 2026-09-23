from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ci_coordinator.config_control import (
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_control.epoch_integrity import (
    EpochDraftIntegrityError,
    assert_admitted_epoch_draft,
)
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ConfigEpochActivationCommand,
    ConfigEpochActivationRecord,
    ConfigEpochReplayCommand,
    prepare_config_epoch_activation,
)


def test_epoch_registration_rejects_a_shape_valid_forged_draft() -> None:
    draft = _admitted_draft()

    assert assert_admitted_epoch_draft(draft) is draft
    with pytest.raises(EpochDraftIntegrityError, match="does not match"):
        assert_admitted_epoch_draft(replace(draft, epoch_id="0" * 64))


def test_prepared_activation_binds_exact_command_facts_and_is_single_use() -> None:
    scope = RepositoryScope(installation_id=1, repository_id=2)
    command = ConfigEpochActivationCommand(
        scope=scope,
        target_epoch_id="a" * 64,
        expected_revision=None,
        operation_id="operation-1",
        actor="operator:123",
        occurred_at="2026-07-14T12:00:00.000Z",
        proposal_manifest_id="proposal:" + "b" * 32,
        authority_evidence_hash="c" * 64,
        authority_observed_at="2026-07-14T11:59:59.000Z",
    )

    prepared = prepare_config_epoch_activation(command)
    event = prepared._take_audit_event()

    assert prepared.command == command
    assert event.idempotency_key == "config-epoch-activation:1:2:operation-1"
    assert event.event_type == "config-epoch-activation/v1"
    assert event.subject_id == "config-epoch:1:2"
    assert json.loads(event.payload_canonical_bytes) == {
        "authorityEvidenceHash": "c" * 64,
        "authorityObservedAt": "2026-07-14T11:59:59.000Z",
        "expectedRevision": None,
        "operationId": "operation-1",
        "proposalManifestId": "proposal:" + "b" * 32,
        "schemaVersion": "config-epoch-activation-audit/v1",
        "targetEpochId": "a" * 64,
    }
    assert ConfigEpochReplayCommand.from_activation(command) == ConfigEpochReplayCommand(
        scope=scope,
        target_epoch_id="a" * 64,
        expected_revision=None,
        operation_id="operation-1",
        actor="operator:123",
        proposal_manifest_id="proposal:" + "b" * 32,
    )
    with pytest.raises(RuntimeError, match="already been consumed"):
        prepared._take_audit_event()


def test_activation_record_rejects_a_non_successor_or_cross_scope_history() -> None:
    scope = RepositoryScope(installation_id=1, repository_id=2)
    first = ActiveConfigEpoch(scope=scope, epoch_id="a" * 64, revision=1)

    with pytest.raises(ValueError, match="successor activation revisions"):
        ConfigEpochActivationRecord(
            scope=scope,
            operation_id="operation-2",
            expected_revision=1,
            previous=first,
            active=ActiveConfigEpoch(scope=scope, epoch_id="b" * 64, revision=3),
            audit_event_id="audit_" + "c" * 32,
            audit_input_hash="d" * 64,
        )


def _admitted_draft() -> ValidatedEpochDraft:
    source = (
        b'{"schemaVersion":"ci-repository-policy/v1","repository":'
        b'{"installationId":1,"repositoryId":2,"owner":"acme","name":"repo",'
        b'"defaultBranch":"main","dynamicCi":null,"rules":[{"name":"main",'
        b'"on":{"event":"push","branches":["main"]},"mode":"observe",'
        b'"timing":{"expectedSignalTimeoutSeconds":3600,'
        b'"absenceVerificationWindowSeconds":300,"absencePollLookbackSeconds":3600,'
        b'"lateFindingWindowSeconds":86400,"mutableDecisionWindowSeconds":300},'
        b'"expectedSignals":[{"kind":"workflow","name":"CI",'
        b'"workflowFile":"ci.yml","source":"native",'
        b'"requiredConclusion":"success","required":true}],'
        b'"omittedSignals":[]}]}}'
    )
    result = admit_policy_document(source, "json")
    assert type(result) is ValidatedEpochDraft
    return result
