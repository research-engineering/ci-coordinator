"""Exact projection of the admitted database compatibility profile."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import Literal, cast

PROFILE_RESOURCE_NAME = "database-compatibility-profile.v1.json"
PROFILE_SHA256 = "b11e19c4240b22a45457c6a0d9603fc6b26bd950c50cb6d049e82c213923990d"


@dataclass(frozen=True, slots=True)
class CompatibilityTimeouts:
    lock_timeout_ms: int
    statement_timeout_ms: int
    transaction_timeout_ms: int

    def __post_init__(self) -> None:
        values = (self.lock_timeout_ms, self.statement_timeout_ms, self.transaction_timeout_ms)
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("compatibility timeout values must be positive integers")
        if not self.lock_timeout_ms < self.statement_timeout_ms < self.transaction_timeout_ms:
            raise ValueError("compatibility timeout order must be lock < statement < transaction")


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    source_digest: str
    lineage_id: str
    protocol_version: int
    application_schema: str
    migration_head_relation: str
    database_major_version: int
    isolation_level: Literal["READ COMMITTED"]
    isolation_expected_value: Literal["read committed"]
    snapshot_isolation_level: Literal["READ COMMITTED"]
    snapshot_isolation_expected_value: Literal["read committed"]
    snapshot_read_only_statement: Literal["SET TRANSACTION READ ONLY"]
    snapshot_read_only_expected_value: Literal["on"]
    fence_class_id: int
    fence_object_id: int
    participant_timeouts: CompatibilityTimeouts
    migration_timeouts: CompatibilityTimeouts
    maximum_capabilities_per_revision: int
    maximum_required_capabilities_per_operation: int
    maximum_capability_id_bytes: int
    declaration_read_limit: int
    capability_read_limit: int
    revision_id_pattern: re.Pattern[str]
    capability_id_pattern: re.Pattern[str]


def load_bundled_profile() -> CompatibilityProfile:
    resource = files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME)
    profile_bytes = resource.read_bytes()
    digest = sha256(profile_bytes).hexdigest()
    if digest != PROFILE_SHA256:
        raise ValueError("bundled database compatibility profile digest does not match admission")
    return parse_profile(profile_bytes)


def parse_profile(profile_bytes: bytes) -> CompatibilityProfile:
    if not profile_bytes:
        raise ValueError("database compatibility profile must not be empty")
    try:
        value = json.loads(profile_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("database compatibility profile must be valid JSON") from error
    profile = _record(value, "profile")
    _exact_keys(
        profile,
        {
            "schemaVersion",
            "profileId",
            "ownerId",
            "lineageId",
            "protocolVersion",
            "database",
            "fence",
            "declaration",
            "transitionAlgebra",
            "capabilityAdmission",
            "dataCompatibility",
            "schemaAttestation",
            "schemaEvolution",
            "privilegeModel",
            "migrationExecution",
            "runtimeDatabaseBoundary",
            "readinessProjection",
            "rollout",
            "authorityPaths",
            "implementationInventory",
            "proofRouting",
            "references",
            "nonClaims",
        },
        "profile",
    )
    _literal(profile, "schemaVersion", "ci-database-compatibility-profile/v1", "profile")
    _literal(profile, "profileId", "ci-database-compatibility/v1", "profile")
    _literal(profile, "ownerId", "ci-coordinator.core", "profile")
    lineage_id = _nonempty_string(profile, "lineageId", "profile")
    protocol_version = _positive_integer(profile, "protocolVersion", "profile")
    database = _record_field(profile, "database", "profile")
    _exact_keys(
        database,
        {
            "engine",
            "majorVersion",
            "applicationSchema",
            "migrationHeadRelation",
            "qualificationLaw",
            "migrationAuthority",
            "transactionIsolation",
            "isolationEnforcement",
            "snapshotReadIsolationEnforcement",
        },
        "database",
    )
    _literal(database, "engine", "postgresql", "database")
    database_major_version = _positive_integer(database, "majorVersion", "database")
    if database_major_version != 18:
        raise ValueError("database compatibility profile must target PostgreSQL 18")
    application_schema = _nonempty_string(database, "applicationSchema", "database")
    migration_head_relation = _nonempty_string(database, "migrationHeadRelation", "database")
    _literal(database, "transactionIsolation", "read-committed", "database")
    isolation = _record_field(database, "isolationEnforcement", "database")
    _exact_keys(
        isolation,
        {
            "sqlalchemyExecutionOption",
            "configurationPhase",
            "verificationStatement",
            "verificationPhase",
            "acceptedValue",
            "mismatchFailure",
        },
        "isolationEnforcement",
    )
    _literal(isolation, "sqlalchemyExecutionOption", "READ COMMITTED", "isolationEnforcement")
    _literal(isolation, "acceptedValue", "read committed", "isolationEnforcement")
    snapshot_isolation = _record_field(
        database,
        "snapshotReadIsolationEnforcement",
        "database",
    )
    _exact_keys(
        snapshot_isolation,
        {
            "sqlalchemyExecutionOption",
            "configurationPhase",
            "readOnlyStatement",
            "isolationVerificationStatement",
            "readOnlyVerificationStatement",
            "verificationPhase",
            "acceptedIsolationValue",
            "acceptedReadOnlyValue",
            "snapshotBoundary",
            "mismatchFailure",
        },
        "snapshotReadIsolationEnforcement",
    )
    _literal(
        snapshot_isolation,
        "sqlalchemyExecutionOption",
        "READ COMMITTED",
        "snapshotReadIsolationEnforcement",
    )
    _literal(
        snapshot_isolation,
        "readOnlyStatement",
        "SET TRANSACTION READ ONLY",
        "snapshotReadIsolationEnforcement",
    )
    _literal(
        snapshot_isolation,
        "acceptedIsolationValue",
        "read committed",
        "snapshotReadIsolationEnforcement",
    )
    _literal(
        snapshot_isolation,
        "acceptedReadOnlyValue",
        "on",
        "snapshotReadIsolationEnforcement",
    )
    fence = _record_field(profile, "fence", "profile")
    _literal(fence, "keySpace", "two-int32", "fence")
    _literal(fence, "participantFunction", "pg_advisory_xact_lock_shared", "fence")
    _literal(fence, "migrationFunction", "pg_advisory_xact_lock", "fence")
    fence_class_id = _integer(fence, "classId", "fence")
    fence_object_id = _integer(fence, "objectId", "fence")
    timeouts = _record_field(fence, "timeoutProfiles", "fence")
    _literal(
        timeouts,
        "orderingLaw",
        "0 < lockTimeoutMs < statementTimeoutMs < transactionTimeoutMs",
        "timeoutProfiles",
    )
    if timeouts.get("zeroIsForbidden") is not True:
        raise ValueError("database compatibility profile must forbid zero timeouts")
    participant_timeouts = _timeouts(_record_field(timeouts, "participant", "timeoutProfiles"))
    migration_timeouts = _timeouts(_record_field(timeouts, "migration", "timeoutProfiles"))
    declaration = _record_field(profile, "declaration", "profile")
    _literal(declaration, "appendOnly", True, "declaration")
    maximum_capabilities = _positive_integer(
        declaration,
        "maximumCapabilitiesPerRevision",
        "declaration",
    )
    maximum_required = _positive_integer(
        declaration,
        "maximumRequiredCapabilitiesPerOperation",
        "declaration",
    )
    if maximum_required > maximum_capabilities:
        raise ValueError("operation capability limit must not exceed revision capability limit")
    maximum_capability_id_bytes = _positive_integer(
        declaration,
        "maximumCapabilityIdBytes",
        "declaration",
    )
    bounded_reads = _record_field(declaration, "boundedReadProtocol", "declaration")
    declaration_read_limit = _positive_integer(
        bounded_reads,
        "declarationLimit",
        "boundedReadProtocol",
    )
    capability_read_limit = _positive_integer(
        bounded_reads,
        "capabilityLimitPerRevision",
        "boundedReadProtocol",
    )
    if declaration_read_limit != 2 or capability_read_limit != maximum_capabilities + 1:
        raise ValueError("database compatibility bounded read limits are inconsistent")
    revision_pattern = _compile_pattern(
        _nonempty_string(declaration, "revisionIdPattern", "declaration")
    )
    capability_pattern = _compile_pattern(
        _nonempty_string(declaration, "capabilityIdPattern", "declaration")
    )
    privilege_model = _record_field(profile, "privilegeModel", "profile")
    identity_evidence = _record_field(privilege_model, "identityEvidence", "privilegeModel")
    _exact_keys(
        identity_evidence,
        {
            "runtimeSession",
            "migrationSchemaOwner",
            "membershipLaw",
            "grantLaw",
            "migrationHeadRead",
            "attestationPhase",
        },
        "privilegeModel.identityEvidence",
    )
    _literal(
        identity_evidence,
        "runtimeSession",
        "session_user = current_user",
        "privilegeModel.identityEvidence",
    )
    _literal(
        identity_evidence,
        "migrationSchemaOwner",
        "pg_namespace.nspowner(applicationSchema)",
        "privilegeModel.identityEvidence",
    )
    _literal(
        identity_evidence,
        "membershipLaw",
        "no pg_auth_members row where member = session_user",
        "privilegeModel.identityEvidence",
    )
    _literal(
        identity_evidence,
        "grantLaw",
        "runtime capability grants are direct, exact, and non-grantable",
        "privilegeModel.identityEvidence",
    )
    _literal(
        identity_evidence,
        "migrationHeadRead",
        "SELECT only on migrationHeadRelation",
        "privilegeModel.identityEvidence",
    )
    _literal(
        identity_evidence,
        "attestationPhase",
        "migration declaration attests static post-DDL facts; each runtime transaction "
        "separately attests exact session-principal grants after the shared fence and "
        "declaration admission before repository exposure",
        "privilegeModel.identityEvidence",
    )
    return CompatibilityProfile(
        source_digest=sha256(profile_bytes).hexdigest(),
        lineage_id=lineage_id,
        protocol_version=protocol_version,
        application_schema=application_schema,
        migration_head_relation=migration_head_relation,
        database_major_version=database_major_version,
        isolation_level="READ COMMITTED",
        isolation_expected_value="read committed",
        snapshot_isolation_level="READ COMMITTED",
        snapshot_isolation_expected_value="read committed",
        snapshot_read_only_statement="SET TRANSACTION READ ONLY",
        snapshot_read_only_expected_value="on",
        fence_class_id=fence_class_id,
        fence_object_id=fence_object_id,
        participant_timeouts=participant_timeouts,
        migration_timeouts=migration_timeouts,
        maximum_capabilities_per_revision=maximum_capabilities,
        maximum_required_capabilities_per_operation=maximum_required,
        maximum_capability_id_bytes=maximum_capability_id_bytes,
        declaration_read_limit=declaration_read_limit,
        capability_read_limit=capability_read_limit,
        revision_id_pattern=revision_pattern,
        capability_id_pattern=capability_pattern,
    )


def _timeouts(value: dict[str, object]) -> CompatibilityTimeouts:
    return CompatibilityTimeouts(
        lock_timeout_ms=_positive_integer(value, "lockTimeoutMs", "timeout profile"),
        statement_timeout_ms=_positive_integer(value, "statementTimeoutMs", "timeout profile"),
        transaction_timeout_ms=_positive_integer(value, "transactionTimeoutMs", "timeout profile"),
    )


def _record(value: object, context: str) -> dict[str, object]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise ValueError(f"{context} must be an object")
    return cast(dict[str, object], value)


def _record_field(record: dict[str, object], key: str, context: str) -> dict[str, object]:
    if key not in record:
        raise ValueError(f"{context}.{key} is required")
    return _record(record[key], f"{context}.{key}")


def _exact_keys(record: dict[str, object], expected: set[str], context: str) -> None:
    if set(record) != expected:
        raise ValueError(f"{context} contains unexpected or missing fields")


def _literal(record: dict[str, object], key: str, expected: object, context: str) -> None:
    if record.get(key) != expected or type(record.get(key)) is not type(expected):
        raise ValueError(f"{context}.{key} must equal its admitted literal")


def _nonempty_string(record: dict[str, object], key: str, context: str) -> str:
    value = record.get(key)
    if type(value) is not str or not value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError(f"{context}.{key} must be a non-empty Unicode scalar string")
    return value


def _positive_integer(record: dict[str, object], key: str, context: str) -> int:
    value = record.get(key)
    if type(value) is not int or value < 1:
        raise ValueError(f"{context}.{key} must be a positive integer")
    return value


def _integer(record: dict[str, object], key: str, context: str) -> int:
    value = record.get(key)
    if type(value) is not int or not -(2**31) <= value < 2**31:
        raise ValueError(f"{context}.{key} must be a signed 32-bit integer")
    return value


def _compile_pattern(value: str) -> re.Pattern[str]:
    try:
        return re.compile(value)
    except re.error as error:
        raise ValueError("database compatibility profile contains an invalid pattern") from error
