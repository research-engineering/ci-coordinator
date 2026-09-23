"""Canonical reconciliation observation row codec."""

from __future__ import annotations

from typing import cast

from ci_coordinator.persistence._reconciliation_codec_support import (
    ReconciliationStateCodecError,
    _positive,
    _positive_revision,
    _text,
)
from ci_coordinator.persistence.canonical_row import (
    CanonicalRowCodecError,
    decode_canonical_object,
    encode_canonical_object,
    require_bounded_text,
    require_digest,
    require_exact_keys,
    semantic_hash,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.reconciliation import ReconciliationSubject, SignalObservation
from ci_coordinator.reconciliation.observation import SignalConclusion, SignalStatus

_OBSERVATION_SCHEMA_VERSION = "ci-reconciliation-observation/v1"
_OBSERVATION_STATUSES = frozenset({"in_progress", "completed"})
_OBSERVATION_CONCLUSIONS = frozenset(
    {"success", "failure", "cancelled", "timed_out", "skipped", "neutral", "unknown"}
)


def encode_observation_row(
    subject: ReconciliationSubject,
    revision: int,
    observation: SignalObservation,
    profile: ShadowReconciliationStateProfile,
) -> dict[str, object]:
    if type(subject) is not ReconciliationSubject or type(observation) is not SignalObservation:
        raise ReconciliationStateCodecError("reconciliation subject and observation must be exact")
    if observation.subject_id != subject.subject_id:
        raise ReconciliationStateCodecError("reconciliation observation belongs to another subject")
    if observation.workflow_run_id != subject.workflow_run_id:
        raise ReconciliationStateCodecError(
            "reconciliation observation belongs to another workflow run"
        )
    if observation.run_attempt != subject.run_attempt:
        raise ReconciliationStateCodecError(
            "reconciliation observation belongs to another run attempt"
        )
    canonical = encode_canonical_object(
        _observation_mapping(observation),
        maximum_bytes=profile.reconciliation_observation_canonical_bytes,
        context="reconciliation observation",
    )
    return {
        "subject_id": subject.subject_id,
        "observation_id": _observation_id(observation.observation_id, profile),
        "revision": _positive_revision(revision, "reconciliation observation revision"),
        "observation_canonical_json": canonical,
        "semantic_hash": semantic_hash(_observation_mapping(observation)),
    }


def decode_observation_row(
    row: dict[str, object],
    subject: ReconciliationSubject,
    profile: ShadowReconciliationStateProfile,
) -> tuple[int, SignalObservation]:
    stored_subject_id = require_digest(
        row.get("subject_id"), "stored reconciliation observation subject"
    )
    if stored_subject_id != subject.subject_id:
        raise ReconciliationStateCodecError(
            "stored reconciliation observation belongs to another subject"
        )
    observation_id = _observation_id(row.get("observation_id"), profile)
    revision = _positive_revision(row.get("revision"), "stored reconciliation observation revision")
    mapping = decode_canonical_object(
        row.get("observation_canonical_json"),
        maximum_bytes=profile.reconciliation_observation_canonical_bytes,
        context="reconciliation observation",
    )
    observation = _observation_from_mapping(mapping)
    if observation.observation_id != observation_id or observation.subject_id != subject.subject_id:
        raise ReconciliationStateCodecError("stored reconciliation observation identity is invalid")
    if observation.workflow_run_id != subject.workflow_run_id:
        raise ReconciliationStateCodecError(
            "stored reconciliation observation belongs to another workflow run"
        )
    if observation.run_attempt != subject.run_attempt:
        raise ReconciliationStateCodecError(
            "stored reconciliation observation belongs to another run attempt"
        )
    stored_hash = require_digest(row.get("semantic_hash"), "stored reconciliation observation hash")
    if stored_hash != semantic_hash(_observation_mapping(observation)):
        raise ReconciliationStateCodecError("stored reconciliation observation hash is invalid")
    return revision, observation


def _observation_mapping(observation: SignalObservation) -> dict[str, object]:
    return {
        "schemaVersion": _OBSERVATION_SCHEMA_VERSION,
        "observationId": observation.observation_id,
        "subjectId": observation.subject_id,
        "signalId": observation.signal_id,
        "workflowRunId": observation.workflow_run_id,
        "runAttempt": observation.run_attempt,
        "providerJobId": observation.provider_job_id,
        "status": observation.status,
        "conclusion": observation.conclusion,
    }


def _observation_from_mapping(mapping: dict[str, object]) -> SignalObservation:
    require_exact_keys(
        mapping,
        {
            "schemaVersion",
            "observationId",
            "subjectId",
            "signalId",
            "workflowRunId",
            "runAttempt",
            "providerJobId",
            "status",
            "conclusion",
        },
        "reconciliation observation",
    )
    if mapping["schemaVersion"] != _OBSERVATION_SCHEMA_VERSION:
        raise ReconciliationStateCodecError(
            "stored reconciliation observation schema version is unsupported"
        )
    status = _text(mapping["status"], "reconciliation observation status")
    conclusion = mapping["conclusion"]
    if status not in _OBSERVATION_STATUSES:
        raise ReconciliationStateCodecError("stored reconciliation observation status is invalid")
    if conclusion is not None and (
        type(conclusion) is not str or conclusion not in _OBSERVATION_CONCLUSIONS
    ):
        raise ReconciliationStateCodecError(
            "stored reconciliation observation conclusion is invalid"
        )
    try:
        return SignalObservation(
            observation_id=_text(mapping["observationId"], "reconciliation observation id"),
            subject_id=require_digest(mapping["subjectId"], "reconciliation observation subject"),
            signal_id=_text(mapping["signalId"], "reconciliation signal id"),
            workflow_run_id=_positive(
                mapping["workflowRunId"],
                "reconciliation observation workflow run id",
            ),
            run_attempt=_positive(
                mapping["runAttempt"],
                "reconciliation observation run attempt",
            ),
            provider_job_id=_positive(
                mapping["providerJobId"],
                "reconciliation observation provider job id",
            ),
            status=cast(SignalStatus, status),
            conclusion=cast(SignalConclusion | None, conclusion),
        )
    except (TypeError, ValueError) as error:
        raise ReconciliationStateCodecError(
            "stored reconciliation observation is invalid"
        ) from error


def _observation_id(value: object, profile: ShadowReconciliationStateProfile) -> str:
    try:
        return require_bounded_text(
            value,
            maximum_bytes=profile.reconciliation_observation_id_utf8_bytes,
            context="reconciliation observation id",
        )
    except CanonicalRowCodecError as error:
        raise ReconciliationStateCodecError(str(error)) from error
