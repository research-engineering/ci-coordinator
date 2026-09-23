"""Exact row projection for mutable CI economics collection state."""

from __future__ import annotations

from datetime import datetime
from typing import Final, cast

from ci_coordinator.ci_economics.collection import (
    CollectionFailureReason,
    CollectionFinalOutcome,
    CollectionState,
    CollectionStatus,
    CollectionTerminalReason,
)
from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.config_control import RepositoryScope

PROVIDER_SOURCE_COLUMNS: Final = (
    "installation_id",
    "repository_id",
    "workflow_run_id",
    "run_attempt",
    "head_sha",
    "provider_api_version",
    "source_evidence_digest",
)

COLLECTION_TRANSITION_COLUMNS: Final = (
    "status",
    "revision",
    "attempt_count",
    "backoff_seconds",
    "next_attempt_at",
    "claim_generation",
    "lease_owner_id",
    "lease_token",
    "lease_acquired_at",
    "lease_expires_at",
    "last_failure_reason",
    "final_outcome",
    "terminal_reason",
    "completed_at",
    "expired_at",
)


def encode_collection_record(state: CollectionState, source: CollectionSource) -> dict[str, object]:
    if type(source) not in (ReconciliationCollectionSource, ProviderRunCollectionSource):
        raise TypeError("collection row encoding requires an exact source")
    state_row = encode_collection_state(state)
    if state.subject_id != source.source_id or (
        isinstance(source, ProviderRunCollectionSource)
        and state.source_created_at != source.run_created_at
    ):
        raise ValueError("collection state differs from its exact source identity or time")
    row: dict[str, object] = {
        **state_row,
        "source_kind": source.kind,
        "legacy_subject_id": None,
        **dict.fromkeys(PROVIDER_SOURCE_COLUMNS),
    }
    if isinstance(source, ReconciliationCollectionSource):
        row["legacy_subject_id"] = source.subject.subject_id
    else:
        row.update(
            installation_id=source.attempt.scope.installation_id,
            repository_id=source.attempt.scope.repository_id,
            workflow_run_id=source.attempt.workflow_run_id,
            run_attempt=source.attempt.run_attempt,
            head_sha=source.attempt.head_sha,
            provider_api_version=source.provider_api_version,
            source_evidence_digest=source.source_evidence_digest,
        )
    return row


def decode_collection_source(
    row: dict[str, object],
    *,
    reconciliation: ReconciliationCollectionSource | None = None,
) -> CollectionSource:
    required = {"subject_id", "source_kind", "legacy_subject_id", *PROVIDER_SOURCE_COLUMNS}
    if not required <= row.keys():
        raise ValueError("collection source columns are incomplete")
    source_id = _text(row, "subject_id")
    kind = _text(row, "source_kind")
    if kind == "reconciliation":
        if (
            type(reconciliation) is not ReconciliationCollectionSource
            or _text(row, "legacy_subject_id") != source_id
            or reconciliation.source_id != source_id
            or any(row[column] is not None for column in PROVIDER_SOURCE_COLUMNS)
        ):
            raise ValueError("collection source differs from its exact reconciliation link")
        return reconciliation
    if kind != "provider_run" or row["legacy_subject_id"] is not None or reconciliation is not None:
        raise ValueError("collection source kind or reconciliation link is invalid")
    source = ProviderRunCollectionSource(
        AttemptIdentity(
            RepositoryScope(_integer(row, "installation_id"), _integer(row, "repository_id")),
            _integer(row, "workflow_run_id"),
            _integer(row, "run_attempt"),
            _text(row, "head_sha"),
        ),
        _time(row, "source_created_at"),
        _text(row, "provider_api_version"),
        _text(row, "source_evidence_digest"),
    )
    if source.source_id != source_id:
        raise ValueError("collection source identity differs from its provider attempt")
    return source


def encode_collection_state(state: CollectionState) -> dict[str, object]:
    if type(state) is not CollectionState:
        raise TypeError("collection row encoding requires an exact state")
    return {
        "subject_id": state.subject_id,
        "policy_hash": state.policy_hash,
        "source_created_at": state.source_created_at,
        "deadline_at": state.deadline_at,
        "evidence_retain_until": state.evidence_retain_until,
        "tombstone_retain_until": state.tombstone_retain_until,
        "status": state.status,
        "revision": state.revision,
        "attempt_count": state.attempt_count,
        "max_attempts": state.max_attempts,
        "backoff_seconds": state.backoff_seconds,
        "max_backoff_seconds": state.max_backoff_seconds,
        "next_attempt_at": state.next_attempt_at,
        "claim_generation": state.claim_generation,
        "lease_owner_id": state.lease_owner_id,
        "lease_token": state.lease_token,
        "lease_acquired_at": state.lease_acquired_at,
        "lease_expires_at": state.lease_expires_at,
        "last_failure_reason": state.last_failure_reason,
        "final_outcome": state.final_outcome,
        "terminal_reason": state.terminal_reason,
        "completed_at": state.completed_at,
        "expired_at": state.expired_at,
    }


def encode_collection_transition(state: CollectionState) -> dict[str, object]:
    row = encode_collection_state(state)
    return {column: row[column] for column in COLLECTION_TRANSITION_COLUMNS}


def decode_collection_state(row: dict[str, object]) -> CollectionState:
    return CollectionState(
        subject_id=_text(row, "subject_id"),
        policy_hash=_text(row, "policy_hash"),
        source_created_at=_time(row, "source_created_at"),
        deadline_at=_time(row, "deadline_at"),
        evidence_retain_until=_time(row, "evidence_retain_until"),
        tombstone_retain_until=_time(row, "tombstone_retain_until"),
        status=cast(CollectionStatus, _text(row, "status")),
        revision=_integer(row, "revision"),
        attempt_count=_integer(row, "attempt_count"),
        max_attempts=_integer(row, "max_attempts"),
        backoff_seconds=_integer(row, "backoff_seconds"),
        max_backoff_seconds=_integer(row, "max_backoff_seconds"),
        next_attempt_at=_optional_time(row, "next_attempt_at"),
        claim_generation=_integer(row, "claim_generation"),
        lease_owner_id=_optional_text(row, "lease_owner_id"),
        lease_token=_optional_text(row, "lease_token"),
        lease_acquired_at=_optional_time(row, "lease_acquired_at"),
        lease_expires_at=_optional_time(row, "lease_expires_at"),
        last_failure_reason=cast(
            CollectionFailureReason | None,
            _optional_text(row, "last_failure_reason"),
        ),
        final_outcome=cast(
            CollectionFinalOutcome | None,
            _optional_text(row, "final_outcome"),
        ),
        terminal_reason=cast(
            CollectionTerminalReason | None,
            _optional_text(row, "terminal_reason"),
        ),
        completed_at=_optional_time(row, "completed_at"),
        expired_at=_optional_time(row, "expired_at"),
    )


def _text(row: dict[str, object], name: str) -> str:
    value = row.get(name)
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _optional_text(row: dict[str, object], name: str) -> str | None:
    value = row.get(name)
    return None if value is None else _text(row, name)


def _integer(row: dict[str, object], name: str) -> int:
    value = row.get(name)
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _time(row: dict[str, object], name: str) -> datetime:
    value = row.get(name)
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _optional_time(row: dict[str, object], name: str) -> datetime | None:
    value = row.get(name)
    return None if value is None else _time(row, name)
