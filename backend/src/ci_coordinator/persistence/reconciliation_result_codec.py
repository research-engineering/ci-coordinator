"""Canonical reconciliation result row codec."""

from __future__ import annotations

from typing import cast

from ci_coordinator.persistence._reconciliation_codec_support import (
    ReconciliationStateCodecError,
    _mapping,
    _revision,
    _text,
    _text_list,
)
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
    require_digest,
    require_exact_keys,
    semantic_hash,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.reconciliation import (
    ReconciliationFinding,
    ReconciliationResult,
    ReconciliationSubject,
)
from ci_coordinator.reconciliation.findings import ReconciliationFindingKind, ReconciliationState

_RESULT_SCHEMA_VERSION = "ci-reconciliation-result/v1"
_RESULT_STATES = frozenset({"success", "failure", "conflict"})
_FINDING_KINDS = frozenset(
    {
        "missing_expected_contract",
        "missing_expected_signal",
        "incomplete_expected_signal",
        "failed_expected_signal",
        "skipped_without_proof",
        "neutral_without_policy",
        "omitted_execution",
        "contradictory_observation",
        "reconciliation_timed_out",
        "reconciliation_attempts_exhausted",
        "ambiguous_provider_signal",
    }
)


def encode_result_row(
    subject: ReconciliationSubject,
    revision: int,
    result: ReconciliationResult,
    profile: ShadowReconciliationStateProfile,
) -> dict[str, object]:
    if type(subject) is not ReconciliationSubject or type(result) is not ReconciliationResult:
        raise ReconciliationStateCodecError("reconciliation subject and result must be exact")
    if result.subject_id != subject.subject_id:
        raise ReconciliationStateCodecError("reconciliation result belongs to another subject")
    if result.state == "pending":
        raise ReconciliationStateCodecError("pending reconciliation result is not durable")
    canonical = encode_canonical_object(
        _result_mapping(result),
        maximum_bytes=profile.reconciliation_result_canonical_bytes,
        context="reconciliation result",
    )
    return {
        "subject_id": subject.subject_id,
        "revision": _revision(revision, "reconciliation result revision"),
        "result_canonical_json": canonical,
        "semantic_hash": semantic_hash(_result_mapping(result)),
    }


def decode_result_row(
    row: dict[str, object],
    subject: ReconciliationSubject,
    profile: ShadowReconciliationStateProfile,
) -> tuple[int, ReconciliationResult]:
    stored_subject_id = require_digest(
        row.get("subject_id"), "stored reconciliation result subject"
    )
    if stored_subject_id != subject.subject_id:
        raise ReconciliationStateCodecError(
            "stored reconciliation result belongs to another subject"
        )
    revision = _revision(row.get("revision"), "stored reconciliation result revision")
    mapping = decode_canonical_object(
        row.get("result_canonical_json"),
        maximum_bytes=profile.reconciliation_result_canonical_bytes,
        context="reconciliation result",
    )
    result = _result_from_mapping(mapping)
    if result.subject_id != subject.subject_id:
        raise ReconciliationStateCodecError("stored reconciliation result identity is invalid")
    stored_hash = require_digest(row.get("semantic_hash"), "stored reconciliation result hash")
    if stored_hash != semantic_hash(_result_mapping(result)):
        raise ReconciliationStateCodecError("stored reconciliation result hash is invalid")
    return revision, result


def _result_mapping(result: ReconciliationResult) -> dict[str, object]:
    return {
        "schemaVersion": _RESULT_SCHEMA_VERSION,
        "subjectId": result.subject_id,
        "state": result.state,
        "findings": [
            {
                "kind": finding.kind,
                "signalId": finding.signal_id,
                "observationIds": list(finding.observation_ids),
                "message": finding.message,
            }
            for finding in result.findings
        ],
    }


def _result_from_mapping(mapping: dict[str, object]) -> ReconciliationResult:
    require_exact_keys(
        mapping, {"schemaVersion", "subjectId", "state", "findings"}, "reconciliation result"
    )
    if mapping["schemaVersion"] != _RESULT_SCHEMA_VERSION:
        raise ReconciliationStateCodecError(
            "stored reconciliation result schema version is unsupported"
        )
    state = _text(mapping["state"], "reconciliation result state")
    if state not in _RESULT_STATES:
        raise ReconciliationStateCodecError("stored reconciliation result state is invalid")
    findings_value = mapping["findings"]
    if type(findings_value) is not list:
        raise ReconciliationStateCodecError("stored reconciliation result findings must be a list")
    findings: list[ReconciliationFinding] = []
    for item in findings_value:
        finding = _mapping(item, "reconciliation finding")
        require_exact_keys(
            finding,
            {"kind", "signalId", "observationIds", "message"},
            "reconciliation finding",
        )
        kind = _text(finding["kind"], "reconciliation finding kind")
        if kind not in _FINDING_KINDS:
            raise ReconciliationStateCodecError("stored reconciliation finding kind is invalid")
        signal_id = finding["signalId"]
        if signal_id is not None:
            signal_id = _text(signal_id, "reconciliation finding signal id")
        observation_ids = _text_list(
            finding["observationIds"], "reconciliation finding observation ids"
        )
        try:
            findings.append(
                ReconciliationFinding(
                    kind=cast(ReconciliationFindingKind, kind),
                    signal_id=signal_id,
                    observation_ids=observation_ids,
                    message=_text(finding["message"], "reconciliation finding message"),
                )
            )
        except (TypeError, ValueError) as error:
            raise ReconciliationStateCodecError(
                "stored reconciliation finding is invalid"
            ) from error
    try:
        return ReconciliationResult(
            subject_id=require_digest(mapping["subjectId"], "reconciliation result subject"),
            state=cast(ReconciliationState, state),
            findings=tuple(findings),
        )
    except (TypeError, ValueError) as error:
        raise ReconciliationStateCodecError("stored reconciliation result is invalid") from error
