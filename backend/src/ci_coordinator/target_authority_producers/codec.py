"""Strict canonical codecs for independently produced authority inputs."""

from __future__ import annotations

from typing import Protocol, cast

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.kernel.canonical_json import bounded_canonical_json
from ci_coordinator.kernel.strict_json import StrictJsonError, load_strict_json
from ci_coordinator.target_authority_relation import (
    AuthorityField,
    AuthorityFieldEntry,
    TargetAuthorityKey,
    TargetAuthoritySubject,
)
from ci_coordinator.target_authority_relation.model import (
    TARGET_AUTHORITY_SUBJECT_SCHEMA,
    AuthorityDisposition,
    TargetAuthorityRowFamily,
)

from .model import (
    _CANDIDATE_JSON_LIMITS,
    _MAX_CANDIDATE_DOCUMENT_BYTES,
    OBSERVATION_CANDIDATE_SET_SCHEMA,
    OWNER_PROJECTION_POLICY_SCHEMA,
    REGISTRATION_CANDIDATE_SET_SCHEMA,
    ObservationCandidateSet,
    ObservedCandidate,
    ObservedCandidateKind,
    OwnerProjectionPolicy,
    ProjectionRule,
    RegistrationCandidateSet,
    TargetAuthorityProducerError,
)


def encode_observation_candidates(value: ObservationCandidateSet) -> bytes:
    return _encode_exact(value, ObservationCandidateSet)


def decode_observation_candidates(content: bytes) -> ObservationCandidateSet:
    mapping = _canonical_mapping(content, OBSERVATION_CANDIDATE_SET_SCHEMA)
    _exact_keys(
        mapping,
        {
            "schemaVersion",
            "scope",
            "sourceCommitId",
            "sourceBindingDigest",
            "workflowManifestDigest",
            "discoveryReportDigest",
            "providerAuthorityDigest",
            "targetArtifactEpochDigest",
            "targetPolicyDigest",
            "validationCatalogDigest",
            "targetRegistryDigest",
            "candidates",
        },
        "observation candidate set",
    )
    try:
        value = ObservationCandidateSet(
            scope=_scope(mapping["scope"]),
            source_commit_id=_string(mapping["sourceCommitId"], "source commit"),
            source_binding_digest=_string(
                mapping["sourceBindingDigest"],
                "source binding digest",
            ),
            workflow_manifest_digest=_string(
                mapping["workflowManifestDigest"],
                "workflow manifest digest",
            ),
            discovery_report_digest=_string(
                mapping["discoveryReportDigest"],
                "discovery report digest",
            ),
            provider_authority_digest=_string(
                mapping["providerAuthorityDigest"],
                "provider authority digest",
            ),
            target_artifact_epoch_digest=_string(
                mapping["targetArtifactEpochDigest"],
                "target artifact epoch digest",
            ),
            target_policy_digest=_string(
                mapping["targetPolicyDigest"],
                "target policy digest",
            ),
            validation_catalog_digest=_string(
                mapping["validationCatalogDigest"],
                "validation catalog digest",
            ),
            target_registry_digest=_string(
                mapping["targetRegistryDigest"],
                "target registry digest",
            ),
            candidates=_candidates(mapping["candidates"]),
        )
    except (TypeError, ValueError) as error:
        raise _codec_error("observation candidate values are invalid") from error
    return _require_round_trip(value, content)


def encode_registration_candidates(value: RegistrationCandidateSet) -> bytes:
    return _encode_exact(value, RegistrationCandidateSet)


def decode_registration_candidates(content: bytes) -> RegistrationCandidateSet:
    mapping = _canonical_mapping(content, REGISTRATION_CANDIDATE_SET_SCHEMA)
    _exact_keys(
        mapping,
        {
            "schemaVersion",
            "scope",
            "targetArtifactEpochDigest",
            "targetPolicyDigest",
            "validationCatalogDigest",
            "targetRegistryDigest",
            "candidates",
        },
        "registration candidate set",
    )
    try:
        value = RegistrationCandidateSet(
            scope=_scope(mapping["scope"]),
            target_artifact_epoch_digest=_string(
                mapping["targetArtifactEpochDigest"],
                "target artifact epoch digest",
            ),
            target_policy_digest=_string(
                mapping["targetPolicyDigest"],
                "target policy digest",
            ),
            validation_catalog_digest=_string(
                mapping["validationCatalogDigest"],
                "validation catalog digest",
            ),
            target_registry_digest=_string(
                mapping["targetRegistryDigest"],
                "target registry digest",
            ),
            candidates=_candidates(mapping["candidates"]),
        )
    except (TypeError, ValueError) as error:
        raise _codec_error("registration candidate values are invalid") from error
    return _require_round_trip(value, content)


def encode_owner_projection_policy(value: OwnerProjectionPolicy) -> bytes:
    return _encode_exact(value, OwnerProjectionPolicy)


def decode_owner_projection_policy(content: bytes) -> OwnerProjectionPolicy:
    mapping = _canonical_mapping(content, OWNER_PROJECTION_POLICY_SCHEMA)
    _exact_keys(
        mapping,
        {
            "schemaVersion",
            "subject",
            "workflowManifestDigest",
            "targetArtifactEpochDigest",
            "observationAuthorityDomainDigest",
            "registrationDeclarationDomainDigest",
            "rules",
        },
        "owner projection policy",
    )
    try:
        value = OwnerProjectionPolicy(
            subject=_subject(mapping["subject"]),
            workflow_manifest_digest=_string(
                mapping["workflowManifestDigest"],
                "workflow manifest digest",
            ),
            target_artifact_epoch_digest=_string(
                mapping["targetArtifactEpochDigest"],
                "target artifact epoch digest",
            ),
            observation_authority_domain_digest=_string(
                mapping["observationAuthorityDomainDigest"],
                "observation authority domain digest",
            ),
            registration_declaration_domain_digest=_string(
                mapping["registrationDeclarationDomainDigest"],
                "registration declaration domain digest",
            ),
            rules=tuple(_projection_rule(item) for item in _list(mapping["rules"], "rules")),
        )
    except (TypeError, ValueError) as error:
        raise _codec_error("owner projection policy values are invalid") from error
    return _require_round_trip(value, content)


class _CanonicalDocument(Protocol):
    @property
    def canonical_bytes(self) -> bytes: ...


def _encode_exact(value: object, expected_type: type[object]) -> bytes:
    if type(value) is not expected_type:
        raise TypeError(f"producer codec requires an exact {expected_type.__name__}")
    return cast(_CanonicalDocument, value).canonical_bytes


def _canonical_mapping(content: bytes, schema: str) -> dict[str, object]:
    if type(content) is not bytes:
        raise TypeError("producer codec requires exact bytes")
    try:
        value = load_strict_json(
            content,
            max_bytes=_MAX_CANDIDATE_DOCUMENT_BYTES,
            resource_limits=_CANDIDATE_JSON_LIMITS,
        )
        mapping = _object(value, "producer document")
        canonical = bounded_canonical_json(
            mapping,
            max_bytes=_MAX_CANDIDATE_DOCUMENT_BYTES,
            resource_limits=_CANDIDATE_JSON_LIMITS,
        )
    except (StrictJsonError, TypeError, ValueError) as error:
        raise _codec_error("producer document is not admitted JSON") from error
    if mapping.get("schemaVersion") != schema:
        raise _codec_error("producer document schema is not admitted")
    if canonical != content:
        raise _codec_error("producer document must already be canonical")
    return mapping


def _scope(value: object) -> RepositoryScope:
    mapping = _object(value, "repository scope")
    _exact_keys(mapping, {"installationId", "repositoryId"}, "repository scope")
    return RepositoryScope(
        _integer(mapping["installationId"], "installation id"),
        _integer(mapping["repositoryId"], "repository id"),
    )


def _subject(value: object) -> TargetAuthoritySubject:
    mapping = _object(value, "target-authority subject")
    _exact_keys(
        mapping,
        {"schemaVersion", "installationId", "repositoryId", "authorityId"},
        "target-authority subject",
    )
    if mapping["schemaVersion"] != TARGET_AUTHORITY_SUBJECT_SCHEMA:
        raise _codec_error("target-authority subject schema is not admitted")
    return TargetAuthoritySubject(
        RepositoryScope(
            _integer(mapping["installationId"], "installation id"),
            _integer(mapping["repositoryId"], "repository id"),
        ),
        _string(mapping["authorityId"], "authority id"),
    )


def _candidates(value: object) -> tuple[ObservedCandidate, ...]:
    return tuple(_candidate(item) for item in _list(value, "candidates"))


def _candidate(value: object) -> ObservedCandidate:
    mapping = _object(value, "observed candidate")
    _exact_keys(
        mapping,
        {"candidateId", "kind", "sourceLocator", "fields"},
        "observed candidate",
    )
    return ObservedCandidate(
        candidate_id=_string(mapping["candidateId"], "candidate id"),
        kind=cast(ObservedCandidateKind, _string(mapping["kind"], "candidate kind")),
        source_locator=_string(mapping["sourceLocator"], "source locator"),
        fields=tuple(_field_entry(item) for item in _list(mapping["fields"], "candidate fields")),
    )


def _field_entry(value: object) -> AuthorityFieldEntry:
    mapping = _object(value, "authority field entry")
    _exact_keys(mapping, {"name", "field"}, "authority field entry")
    return AuthorityFieldEntry(
        _string(mapping["name"], "authority field name"),
        _field(mapping["field"]),
    )


def _field(value: object) -> AuthorityField:
    mapping = _object(value, "authority field")
    state = _string(mapping.get("state"), "authority field state")
    if state == "present":
        _exact_keys(mapping, {"state", "value"}, "present authority field")
        return AuthorityField.present(mapping["value"])
    if state == "not_applicable":
        _exact_keys(mapping, {"state"}, "not-applicable authority field")
        return AuthorityField.not_applicable()
    if state == "unknown":
        _exact_keys(mapping, {"state", "reason"}, "unknown authority field")
        return AuthorityField.unknown(_string(mapping["reason"], "unknown reason"))
    raise _codec_error("authority field state is not admitted")


def _projection_rule(value: object) -> ProjectionRule:
    mapping = _object(value, "projection rule")
    _exact_keys(
        mapping,
        {
            "candidateId",
            "evidenceDigest",
            "rowKey",
            "disposition",
            "semanticOwner",
            "sourceLocator",
        },
        "projection rule",
    )
    return ProjectionRule(
        candidate_id=_string(mapping["candidateId"], "candidate id"),
        evidence_digest=_string(mapping["evidenceDigest"], "candidate evidence digest"),
        row_key=_row_key(mapping["rowKey"]),
        disposition=cast(
            AuthorityDisposition,
            _string(mapping["disposition"], "projection disposition"),
        ),
        semantic_owner=_string(mapping["semanticOwner"], "semantic owner"),
        source_locator=_string(mapping["sourceLocator"], "source locator"),
    )


def _row_key(value: object) -> TargetAuthorityKey:
    mapping = _object(value, "target-authority key")
    _exact_keys(mapping, {"family", "memberId"}, "target-authority key")
    return TargetAuthorityKey(
        cast(TargetAuthorityRowFamily, _string(mapping["family"], "row family")),
        _string(mapping["memberId"], "member id"),
    )


def _require_round_trip[T: _CanonicalDocument](value: T, content: bytes) -> T:
    if value.canonical_bytes != content:
        raise _codec_error("producer document failed its exact round trip")
    return value


def _object(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise _codec_error(f"{name} must be an exact string-keyed object")
    return cast(dict[str, object], value)


def _exact_keys(mapping: dict[str, object], expected: set[str], name: str) -> None:
    if set(mapping) != expected:
        raise _codec_error(f"{name} keys are not exact")


def _list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise _codec_error(f"{name} must be an exact array")
    return cast(list[object], value)


def _string(value: object, name: str) -> str:
    if type(value) is not str:
        raise _codec_error(f"{name} must be an exact string")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise _codec_error(f"{name} must be an exact integer")
    return value


def _codec_error(message: str) -> TargetAuthorityProducerError:
    return TargetAuthorityProducerError("codec_rejected", message)
