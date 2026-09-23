"""Strict canonical codec for replayable unactivated evidence."""

from __future__ import annotations

from typing import cast

from ci_coordinator.kernel.canonical_json import bounded_canonical_json
from ci_coordinator.kernel.strict_json import StrictJsonError, load_strict_json
from ci_coordinator.target_authority_relation import decode_expected_relation

from .model import (
    EVIDENCE_JSON_LIMITS,
    EVIDENCE_ROLES,
    MAX_ARTIFACT_DOCUMENT_BYTES,
    MAX_EVIDENCE_BUNDLE_BYTES,
    TARGET_AUTHORITY_EVIDENCE_SCHEMA,
    AdmittedTargetAuthorityEvidence,
    EvidenceArtifact,
    EvidenceRole,
    TargetAuthorityEvidenceError,
    UnactivatedEvidenceBundle,
)
from .replay import replay_target_authority_evidence


def encode_target_authority_evidence(value: AdmittedTargetAuthorityEvidence) -> bytes:
    if type(value) is not AdmittedTargetAuthorityEvidence:
        raise TypeError("evidence codec requires an exact admitted value")
    replayed = replay_target_authority_evidence(value.bundle)
    if replayed.values != value.values:
        raise TargetAuthorityEvidenceError(
            "admitted_value_mismatch",
            "admitted evidence values differ from bundle replay",
        )
    return value.bundle.canonical_bytes


def decode_target_authority_evidence(content: bytes) -> AdmittedTargetAuthorityEvidence:
    mapping = _canonical_mapping(content)
    _exact_keys(
        mapping,
        {
            "schemaVersion",
            "authorityState",
            "subject",
            "epoch",
            "artifacts",
            "bundleDigest",
        },
        "evidence bundle",
    )
    if mapping["schemaVersion"] != TARGET_AUTHORITY_EVIDENCE_SCHEMA:
        raise _codec_error("bundle_schema_rejected", "evidence bundle schema is not admitted")
    if mapping["authorityState"] != "unactivated":
        raise _codec_error("authority_state_rejected", "evidence cannot claim activation")
    artifacts = tuple(_artifact(item) for item in _list(mapping["artifacts"], "evidence artifacts"))
    if tuple(artifact.role for artifact in artifacts) != EVIDENCE_ROLES:
        raise _codec_error(
            "artifact_role_closure_rejected",
            "evidence artifact roles are not exactly closed and canonical",
        )
    try:
        expected = decode_expected_relation(artifacts[2].content)
    except (TypeError, ValueError) as error:
        raise _codec_error(
            "owner_codec_rejected",
            "expected relation did not pass its owner codec",
        ) from error
    if (
        mapping["subject"] != expected.subject.to_mapping()
        or mapping["epoch"] != expected.epoch.to_mapping()
    ):
        raise _codec_error(
            "bundle_identity_mismatch",
            "bundle subject or epoch differs from the retained expected relation",
        )
    try:
        bundle = UnactivatedEvidenceBundle(expected.subject, expected.epoch, artifacts)
    except (TypeError, ValueError) as error:
        raise _codec_error("bundle_rejected", "evidence bundle values are invalid") from error
    if _string(mapping["bundleDigest"], "bundle digest") != bundle.bundle_digest:
        raise _codec_error(
            "bundle_digest_mismatch",
            "evidence bundle digest does not bind its exact body",
        )
    if bundle.canonical_bytes != content:
        raise _codec_error("bundle_round_trip_mismatch", "evidence bundle round trip changed")
    return replay_target_authority_evidence(bundle)


def _canonical_mapping(content: bytes) -> dict[str, object]:
    if type(content) is not bytes:
        raise TypeError("evidence codec requires exact bytes")
    try:
        value = load_strict_json(
            content,
            max_bytes=MAX_EVIDENCE_BUNDLE_BYTES,
            resource_limits=EVIDENCE_JSON_LIMITS,
        )
        mapping = _object(value, "evidence bundle")
        canonical = bounded_canonical_json(
            mapping,
            max_bytes=MAX_EVIDENCE_BUNDLE_BYTES,
            resource_limits=EVIDENCE_JSON_LIMITS,
        )
    except (StrictJsonError, TypeError, ValueError) as error:
        raise _codec_error(
            "bundle_json_rejected",
            "evidence bundle is not admitted JSON",
        ) from error
    if canonical != content:
        raise _codec_error("bundle_not_canonical", "evidence bundle must already be canonical")
    return mapping


def _artifact(value: object) -> EvidenceArtifact:
    mapping = _object(value, "evidence artifact")
    _exact_keys(
        mapping,
        {
            "role",
            "ownerSchema",
            "byteCount",
            "sha256",
            "artifactDigest",
            "document",
        },
        "evidence artifact",
    )
    role = cast(EvidenceRole, _string(mapping["role"], "artifact role"))
    owner_schema = _string(mapping["ownerSchema"], "artifact owner schema")
    document = _object(mapping["document"], "artifact document")
    try:
        content = bounded_canonical_json(
            document,
            max_bytes=MAX_ARTIFACT_DOCUMENT_BYTES,
            resource_limits=EVIDENCE_JSON_LIMITS,
        )
        artifact = EvidenceArtifact(role, owner_schema, content)
    except (TypeError, ValueError) as error:
        raise _codec_error("artifact_rejected", "evidence artifact values are invalid") from error
    if (
        _integer(mapping["byteCount"], "artifact byte count") != len(content)
        or _string(mapping["sha256"], "artifact SHA-256") != artifact.sha256
        or _string(mapping["artifactDigest"], "artifact digest") != artifact.artifact_digest
    ):
        raise _codec_error(
            "artifact_identity_mismatch",
            "evidence artifact metadata does not bind its exact owner document",
        )
    return artifact


def _object(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise _codec_error("object_rejected", f"{name} must be an exact string-keyed object")
    return cast(dict[str, object], value)


def _exact_keys(mapping: dict[str, object], expected: set[str], name: str) -> None:
    if set(mapping) != expected:
        raise _codec_error("key_set_rejected", f"{name} keys are not exact")


def _list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise _codec_error("array_rejected", f"{name} must be an exact array")
    return cast(list[object], value)


def _string(value: object, name: str) -> str:
    if type(value) is not str:
        raise _codec_error("string_rejected", f"{name} must be an exact string")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise _codec_error("integer_rejected", f"{name} must be an exact integer")
    return value


def _codec_error(code: str, message: str) -> TargetAuthorityEvidenceError:
    return TargetAuthorityEvidenceError(code, message)
