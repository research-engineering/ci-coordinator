"""Canonical PostgreSQL row codec for immutable terminal shadow evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

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
from ci_coordinator.shadow_mode import (
    CoverageRelation,
    FullCiObservation,
    FullCiResult,
    ShadowCandidate,
    ShadowComparison,
    ShadowComparisonClassification,
    ShadowEvidenceKey,
    ShadowEvidenceRecord,
)

_RECORD_SCHEMA_VERSION = "ci-shadow-evidence-record/v2"


class ShadowEvidenceCodecError(CanonicalRowCodecError):
    pass


def encode_shadow_evidence_row(
    record: ShadowEvidenceRecord,
    profile: ShadowReconciliationStateProfile,
) -> dict[str, object]:
    if type(record) is not ShadowEvidenceRecord:
        raise ShadowEvidenceCodecError("shadow evidence record must be exact")
    key = record.key
    profile_id = _profile_id(key.profile_id, profile)
    repository = _repository(key.repository, profile)
    event = _event(key.event, profile)
    surface = _surface(key.surface, profile)
    mapping = _record_mapping(record)
    canonical = encode_canonical_object(
        mapping,
        maximum_bytes=profile.shadow_record_canonical_bytes,
        context="shadow evidence record",
    )
    return {
        "profile_id": profile_id,
        "repository": repository,
        "event": event,
        "surface": surface,
        "record_canonical_json": canonical,
        "semantic_hash": semantic_hash(_semantic_mapping(record)),
    }


def decode_shadow_evidence_row(
    row: dict[str, object],
    profile: ShadowReconciliationStateProfile,
) -> ShadowEvidenceRecord:
    profile_id = _profile_id(row.get("profile_id"), profile)
    repository = _repository(row.get("repository"), profile)
    event = _event(row.get("event"), profile)
    surface = _surface(row.get("surface"), profile)
    mapping = decode_canonical_object(
        row.get("record_canonical_json"),
        maximum_bytes=profile.shadow_record_canonical_bytes,
        context="shadow evidence record",
    )
    record = _record_from_mapping(mapping)
    if record.key != ShadowEvidenceKey(profile_id, repository, event, surface):
        raise ShadowEvidenceCodecError("stored shadow evidence key does not match its record")
    stored_hash = require_digest(row.get("semantic_hash"), "stored shadow evidence semantic hash")
    if stored_hash != semantic_hash(_semantic_mapping(record)):
        raise ShadowEvidenceCodecError("stored shadow evidence semantic hash is invalid")
    return record


def validate_shadow_profile_id(
    profile_id: object,
    profile: ShadowReconciliationStateProfile,
) -> str:
    return _profile_id(profile_id, profile)


def _record_mapping(record: ShadowEvidenceRecord) -> dict[str, object]:
    return {
        "schemaVersion": _RECORD_SCHEMA_VERSION,
        "profileId": record.profile_id,
        "observedAt": _timestamp(record.observed_at),
        "comparison": _comparison_mapping(record.comparison),
    }


def _semantic_mapping(record: ShadowEvidenceRecord) -> dict[str, object]:
    return {
        "schemaVersion": _RECORD_SCHEMA_VERSION,
        "profileId": record.profile_id,
        "comparison": _comparison_mapping(record.comparison),
    }


def _record_from_mapping(mapping: dict[str, object]) -> ShadowEvidenceRecord:
    require_exact_keys(
        mapping, {"schemaVersion", "profileId", "observedAt", "comparison"}, "record"
    )
    if mapping["schemaVersion"] != _RECORD_SCHEMA_VERSION:
        raise ShadowEvidenceCodecError("stored shadow evidence schema version is unsupported")
    profile_id = _text(mapping["profileId"], "shadow evidence profile id")
    observed_at = _parse_timestamp(mapping["observedAt"])
    comparison = _comparison_from_mapping(mapping["comparison"])
    try:
        return ShadowEvidenceRecord(profile_id, observed_at, comparison)
    except (TypeError, ValueError) as error:
        raise ShadowEvidenceCodecError("stored shadow evidence record is invalid") from error


def _comparison_mapping(comparison: ShadowComparison) -> dict[str, object]:
    if type(comparison) is not ShadowComparison:
        raise ShadowEvidenceCodecError("shadow comparison must be exact")
    return {
        "candidate": _candidate_mapping(comparison.candidate),
        "observation": (
            None if comparison.observation is None else _observation_mapping(comparison.observation)
        ),
        "classification": comparison.classification.value,
        "reason": comparison.reason,
        "mismatchedFields": list(comparison.mismatched_fields),
    }


def _comparison_from_mapping(value: object) -> ShadowComparison:
    mapping = _mapping(value, "shadow comparison")
    require_exact_keys(
        mapping,
        {"candidate", "observation", "classification", "reason", "mismatchedFields"},
        "shadow comparison",
    )
    observation_value = mapping["observation"]
    observation = (
        None if observation_value is None else _observation_from_mapping(observation_value)
    )
    mismatches = _text_tuple(mapping["mismatchedFields"], "shadow comparison mismatched fields")
    try:
        return ShadowComparison(
            candidate=_candidate_from_mapping(mapping["candidate"]),
            observation=observation,
            classification=ShadowComparisonClassification(
                _text(mapping["classification"], "shadow comparison classification")
            ),
            reason=_text(mapping["reason"], "shadow comparison reason"),
            mismatched_fields=mismatches,
        )
    except (TypeError, ValueError) as error:
        raise ShadowEvidenceCodecError("stored shadow comparison is invalid") from error


def _candidate_mapping(candidate: ShadowCandidate) -> dict[str, object]:
    if type(candidate) is not ShadowCandidate:
        raise ShadowEvidenceCodecError("shadow candidate must be exact")
    return {
        "repo": candidate.repo,
        "event": candidate.event,
        "baseSha": candidate.base_sha,
        "headSha": candidate.head_sha,
        "configEpoch": candidate.config_epoch,
        "policyHash": candidate.policy_hash,
        "diffHash": candidate.diff_hash,
        "graphHash": candidate.graph_hash,
        "baselinePlan": candidate.baseline_plan,
        "candidatePlan": candidate.candidate_plan,
        "surface": candidate.surface,
        "coverageRelation": candidate.coverage_relation.value,
        "actualFullCiResult": candidate.actual_full_ci_result.value,
        "unsafeCandidate": candidate.unsafe_candidate,
    }


def _candidate_from_mapping(value: object) -> ShadowCandidate:
    mapping = _mapping(value, "shadow candidate")
    require_exact_keys(
        mapping,
        {
            "repo",
            "event",
            "baseSha",
            "headSha",
            "configEpoch",
            "policyHash",
            "diffHash",
            "graphHash",
            "baselinePlan",
            "candidatePlan",
            "surface",
            "coverageRelation",
            "actualFullCiResult",
            "unsafeCandidate",
        },
        "shadow candidate",
    )
    if type(mapping["unsafeCandidate"]) is not bool:
        raise ShadowEvidenceCodecError("stored shadow candidate unsafe flag is invalid")
    try:
        return ShadowCandidate(
            repo=_text(mapping["repo"], "shadow candidate repository"),
            event=_text(mapping["event"], "shadow candidate event"),
            base_sha=_text(mapping["baseSha"], "shadow candidate base SHA"),
            head_sha=_text(mapping["headSha"], "shadow candidate head SHA"),
            config_epoch=_text(mapping["configEpoch"], "shadow candidate config epoch"),
            policy_hash=_text(mapping["policyHash"], "shadow candidate policy hash"),
            diff_hash=_text(mapping["diffHash"], "shadow candidate diff hash"),
            graph_hash=_text(mapping["graphHash"], "shadow candidate graph hash"),
            baseline_plan=_text(mapping["baselinePlan"], "shadow candidate baseline plan"),
            candidate_plan=_text(mapping["candidatePlan"], "shadow candidate candidate plan"),
            surface=_text(mapping["surface"], "shadow candidate surface"),
            coverage_relation=CoverageRelation(
                _text(mapping["coverageRelation"], "shadow candidate coverage relation")
            ),
            actual_full_ci_result=FullCiResult(
                _text(mapping["actualFullCiResult"], "shadow candidate FullCI result")
            ),
            unsafe_candidate=mapping["unsafeCandidate"],
        )
    except (TypeError, ValueError) as error:
        raise ShadowEvidenceCodecError("stored shadow candidate is invalid") from error


def _observation_mapping(observation: FullCiObservation) -> dict[str, object]:
    if type(observation) is not FullCiObservation:
        raise ShadowEvidenceCodecError("FullCI observation must be exact")
    return {
        "repo": observation.repo,
        "event": observation.event,
        "baseSha": observation.base_sha,
        "headSha": observation.head_sha,
        "configEpoch": observation.config_epoch,
        "policyHash": observation.policy_hash,
        "diffHash": observation.diff_hash,
        "graphHash": observation.graph_hash,
        "baselinePlan": observation.baseline_plan,
        "candidatePlan": observation.candidate_plan,
        "actualFullCiResult": observation.actual_full_ci_result.value,
    }


def _observation_from_mapping(value: object) -> FullCiObservation:
    mapping = _mapping(value, "FullCI observation")
    require_exact_keys(
        mapping,
        {
            "repo",
            "event",
            "baseSha",
            "headSha",
            "configEpoch",
            "policyHash",
            "diffHash",
            "graphHash",
            "baselinePlan",
            "candidatePlan",
            "actualFullCiResult",
        },
        "FullCI observation",
    )
    try:
        return FullCiObservation(
            repo=_text(mapping["repo"], "FullCI observation repository"),
            event=_text(mapping["event"], "FullCI observation event"),
            base_sha=_text(mapping["baseSha"], "FullCI observation base SHA"),
            head_sha=_text(mapping["headSha"], "FullCI observation head SHA"),
            config_epoch=_text(mapping["configEpoch"], "FullCI observation config epoch"),
            policy_hash=_text(mapping["policyHash"], "FullCI observation policy hash"),
            diff_hash=_text(mapping["diffHash"], "FullCI observation diff hash"),
            graph_hash=_text(mapping["graphHash"], "FullCI observation graph hash"),
            baseline_plan=_text(mapping["baselinePlan"], "FullCI observation baseline plan"),
            candidate_plan=_text(mapping["candidatePlan"], "FullCI observation candidate plan"),
            actual_full_ci_result=FullCiResult(
                _text(mapping["actualFullCiResult"], "FullCI observation result")
            ),
        )
    except (TypeError, ValueError) as error:
        raise ShadowEvidenceCodecError("stored FullCI observation is invalid") from error


def _timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ShadowEvidenceCodecError("shadow evidence timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    text = _text(value, "shadow evidence timestamp")
    if not text.endswith("Z"):
        raise ShadowEvidenceCodecError("stored shadow evidence timestamp must use UTC Z notation")
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError as error:
        raise ShadowEvidenceCodecError("stored shadow evidence timestamp is invalid") from error
    if _timestamp(parsed) != text:
        raise ShadowEvidenceCodecError("stored shadow evidence timestamp is not canonical")
    return parsed


def _profile_id(value: object, profile: ShadowReconciliationStateProfile) -> str:
    return _bounded(value, profile.profile_id_utf8_bytes, "shadow evidence profile id")


def _repository(value: object, profile: ShadowReconciliationStateProfile) -> str:
    return _bounded(value, profile.shadow_repository_utf8_bytes, "shadow evidence repository")


def _event(value: object, profile: ShadowReconciliationStateProfile) -> str:
    return _bounded(value, profile.shadow_event_utf8_bytes, "shadow evidence event")


def _surface(value: object, profile: ShadowReconciliationStateProfile) -> str:
    return _bounded(value, profile.shadow_surface_utf8_bytes, "shadow evidence surface")


def _bounded(value: object, maximum_bytes: int, context: str) -> str:
    try:
        return require_bounded_text(value, maximum_bytes=maximum_bytes, context=context)
    except CanonicalRowCodecError as error:
        raise ShadowEvidenceCodecError(str(error)) from error


def _mapping(value: object, context: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ShadowEvidenceCodecError(f"stored {context} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, context: str) -> str:
    if type(value) is not str or not value:
        raise ShadowEvidenceCodecError(f"stored {context} must be non-empty text")
    return value


def _text_tuple(value: object, context: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        raise ShadowEvidenceCodecError(f"stored {context} must be non-empty text values")
    return tuple(cast(list[str], value))
