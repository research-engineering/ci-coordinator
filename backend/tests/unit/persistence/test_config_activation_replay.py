from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditEventInput,
    AuditEventRecord,
    append_event,
    verify_audit_event_integrity,
)
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ConfigEpochActivationRecord,
    ConfigEpochReplayCommand,
)
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository


@pytest.mark.parametrize(
    "mutant", [None, "outer", "unknown-schema", "hybrid", "missing-selector", "selector", "extra"]
)
def test_valid_generic_hashes_do_not_admit_an_unknown_or_hybrid_activation(
    mutant: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = RepositoryScope(1, 2)
    payload: dict[str, JsonValue] = {
        "schemaVersion": "config-epoch-activation-audit/v2",
        "operationId": "activation-1",
        "expectedRevision": None,
        "targetEpochId": "a" * 64,
        "proposalManifestId": "proposal:" + "b" * 32,
        "reviewOperationId": "selected-review",
        "authorityEvidenceHash": "c" * 64,
        "authorityObservedAt": "2026-09-26T10:00:00.000Z",
    }
    if mutant == "unknown-schema":
        payload["schemaVersion"] = "config-epoch-activation-audit/v3"
    elif mutant == "hybrid":
        payload["schemaVersion"] = "config-epoch-activation-audit/v1"
    elif mutant == "missing-selector":
        del payload["reviewOperationId"]
    elif mutant == "selector":
        payload["reviewOperationId"] = "another-review"
    elif mutant == "extra":
        payload["unadmitted"] = True
    appended = append_event(
        (),
        AuditEventInput(
            idempotency_key="config-epoch-activation:1:2:activation-1",
            installation_id=1,
            repository_id=2,
            subject_type="policy-decision",
            subject_id="config-epoch:1:2",
            event_type="config-epoch-activation/v2"
            if mutant == "outer"
            else "config-epoch-activation/v1",
            created_at="2026-09-26T10:00:01.000Z",
            actor="operator:123",
            payload=payload,
        ),
    )
    assert isinstance(appended, AuditAppendAppended)
    event = appended.record
    assert verify_audit_event_integrity(event) is None
    record = ConfigEpochActivationRecord(
        scope=scope,
        operation_id="activation-1",
        expected_revision=None,
        previous=None,
        active=ActiveConfigEpoch(scope, "a" * 64, 1),
        audit_event_id=event.audit_event_id,
        audit_input_hash=event.input_hash,
    )
    replay = ConfigEpochReplayCommand(
        scope=scope,
        target_epoch_id="a" * 64,
        expected_revision=None,
        operation_id="activation-1",
        actor="operator:123",
        proposal_manifest_id="proposal:" + "b" * 32,
        activation_version=2,
        review_operation_id="selected-review",
    )
    repository = _PostgresConfigEpochRepository(
        cast(AsyncConnection, object()),
        cast(_PostgresAuditEventRepository, object()),
        lambda: None,
        lambda: None,
    )

    async def load(audit_event_id: str) -> AuditEventRecord:
        assert audit_event_id == event.audit_event_id
        return event

    monkeypatch.setattr(repository, "_load_audit_event", load)
    assert asyncio.run(repository._same_operation(record, replay)) is (mutant is None)


@pytest.mark.parametrize(
    ("replay_version", "selector", "source_hash", "matches"),
    [
        (3, "registered-review", "d" * 64, True),
        (3, "another-review", "d" * 64, False),
        (3, "registered-review", "e" * 64, False),
        (2, "registered-review", None, False),
    ],
    ids=("exact", "other-review", "other-source", "proposal-selector"),
)
def test_registered_epoch_receipt_replays_only_its_exact_v3_selector(
    replay_version: int,
    selector: str,
    source_hash: str | None,
    matches: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Kills: v3 replay ignoring the source hash, or a v2 selector replaying a v3 receipt.
    scope = RepositoryScope(1, 2)
    appended = append_event(
        (),
        AuditEventInput(
            idempotency_key="config-epoch-activation:1:2:activation-1",
            installation_id=1,
            repository_id=2,
            subject_type="policy-decision",
            subject_id="config-epoch:1:2",
            event_type="config-epoch-activation/v1",
            created_at="2026-10-11T10:00:01.000Z",
            actor="operator:123",
            payload={
                "schemaVersion": "config-epoch-activation-audit/v3",
                "operationId": "activation-1",
                "expectedRevision": None,
                "targetEpochId": "a" * 64,
                "expectedSourceHash": "d" * 64,
                "registeredEpochReviewOperationId": "registered-review",
                "authorityEvidenceHash": "c" * 64,
                "authorityObservedAt": "2026-10-11T10:00:00.000Z",
            },
        ),
    )
    assert isinstance(appended, AuditAppendAppended)
    event = appended.record
    record = ConfigEpochActivationRecord(
        scope=scope,
        operation_id="activation-1",
        expected_revision=None,
        previous=None,
        active=ActiveConfigEpoch(scope, "a" * 64, 1),
        audit_event_id=event.audit_event_id,
        audit_input_hash=event.input_hash,
    )
    replay = ConfigEpochReplayCommand(
        scope=scope,
        target_epoch_id="a" * 64,
        expected_revision=None,
        operation_id="activation-1",
        actor="operator:123",
        proposal_manifest_id=None if replay_version == 3 else "proposal:" + "b" * 32,
        activation_version=cast(Any, replay_version),
        review_operation_id=selector,
        expected_source_hash=source_hash,
    )
    repository = _PostgresConfigEpochRepository(
        cast(AsyncConnection, object()),
        cast(_PostgresAuditEventRepository, object()),
        lambda: None,
        lambda: None,
    )

    async def load(audit_event_id: str) -> AuditEventRecord:
        assert audit_event_id == event.audit_event_id
        return event

    monkeypatch.setattr(repository, "_load_audit_event", load)
    assert asyncio.run(repository._same_operation(record, replay)) is matches
