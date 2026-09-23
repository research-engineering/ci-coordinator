"""Finite immutable row, subject, epoch, and producer algebra."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, Self

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.kernel.canonical_json import CanonicalJsonError, bounded_canonical_json
from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.kernel.strict_json import StrictJsonError, load_strict_json

from ._canonical import domain_digest, encode_mapping
from .limits import (
    MAX_FIELD_NAME_BYTES,
    MAX_FIELD_VALUE_BYTES,
    MAX_MEMBER_ID_BYTES,
    MAX_OWNER_ID_BYTES,
    MAX_PRODUCER_ID_BYTES,
    MAX_PRODUCER_VERSION_BYTES,
    MAX_RELATION_ROWS,
    MAX_ROW_BYTES,
    MAX_ROW_FIELDS,
    MAX_SOURCE_LOCATOR_BYTES,
    RELATION_JSON_LIMITS,
)

type AuthorityFieldState = Literal["present", "not_applicable", "unknown"]
type AuthorityDisposition = Literal["authority", "owner_approved_non_authority"]
type RelationEpochPhase = Literal["native_baseline", "adapted_target"]
type TargetAuthorityRowFamily = Literal[
    "consumer_contract_scenario",
    "job",
    "provider_gate",
    "provider_repository",
    "target_policy",
    "target_registry_adapter",
    "target_registry_metadata",
    "target_registry_profile",
    "target_registry_workflow",
    "validation_obligation",
    "validation_profile",
    "validation_witness",
    "workflow",
]

TARGET_AUTHORITY_ROW_SCHEMA: Final = "ci-coordinator.target-authority-row/v1"
TARGET_AUTHORITY_SUBJECT_SCHEMA: Final = "ci-coordinator.target-authority-subject/v1"
TARGET_AUTHORITY_EPOCH_SCHEMA: Final = "ci-coordinator.target-authority-epoch/v1"
TARGET_AUTHORITY_PRODUCER_SCHEMA: Final = "ci-coordinator.target-authority-producer/v1"

_DIGEST = re.compile(r"[0-9a-f]{64}")
_FIELD_NAME = re.compile(r"[a-z][A-Za-z0-9]{0,63}")

_ROW_FIELDS: Final[dict[TargetAuthorityRowFamily, tuple[frozenset[str], frozenset[str]]]] = {
    "consumer_contract_scenario": (
        frozenset({"definition", "sourceIdentity"}),
        frozenset(),
    ),
    "job": (
        frozenset({"needs", "roleSet", "semanticProjection", "workflowIdentity"}),
        frozenset({"executionKind", "profileSet"}),
    ),
    "provider_gate": (
        frozenset({"enforcementState", "providerProjection"}),
        frozenset(
            {
                "bypassActors",
                "checkAppBinding",
                "mergeQueueRelation",
                "requiredCheckIdentity",
            }
        ),
    ),
    "provider_repository": (
        frozenset({"apiVersion", "defaultBranch", "repositoryIdentity"}),
        frozenset(),
    ),
    "target_policy": (frozenset({"compiledPolicy"}), frozenset()),
    "target_registry_adapter": (frozenset({"definition"}), frozenset()),
    "target_registry_metadata": (frozenset({"definition"}), frozenset()),
    "target_registry_profile": (frozenset({"definition"}), frozenset()),
    "target_registry_workflow": (frozenset({"definition"}), frozenset()),
    "validation_obligation": (frozenset({"definition"}), frozenset()),
    "validation_profile": (
        frozenset({"definition", "evidenceIdentity"}),
        frozenset(),
    ),
    "validation_witness": (frozenset({"definition"}), frozenset()),
    "workflow": (
        frozenset({"activeState", "contentIdentity", "semanticProjection", "triggerSurface"}),
        frozenset(),
    ),
}


@dataclass(frozen=True, slots=True)
class AuthorityField:
    state: AuthorityFieldState
    canonical_value: bytes | None = None
    reason: str | None = None

    @classmethod
    def present(cls, value: object) -> Self:
        return cls(
            "present",
            bounded_canonical_json(
                value,
                max_bytes=MAX_FIELD_VALUE_BYTES,
                resource_limits=RELATION_JSON_LIMITS,
            ),
        )

    @classmethod
    def not_applicable(cls) -> Self:
        return cls("not_applicable")

    @classmethod
    def unknown(cls, reason: str) -> Self:
        return cls("unknown", reason=reason)

    def __post_init__(self) -> None:
        if self.state == "present":
            if type(self.canonical_value) is not bytes or self.reason is not None:
                raise TypeError("present authority field requires exact canonical bytes")
            try:
                value = load_strict_json(
                    self.canonical_value,
                    max_bytes=MAX_FIELD_VALUE_BYTES,
                )
                recanonicalized = bounded_canonical_json(
                    value,
                    max_bytes=MAX_FIELD_VALUE_BYTES,
                    resource_limits=RELATION_JSON_LIMITS,
                )
            except (CanonicalJsonError, StrictJsonError) as error:
                raise ValueError("authority field value is not admitted canonical JSON") from error
            if recanonicalized != self.canonical_value:
                raise ValueError("authority field value must already be canonical")
            return
        if self.state == "not_applicable":
            if self.canonical_value is not None or self.reason is not None:
                raise ValueError("not-applicable authority field cannot carry a value or reason")
            return
        if self.state == "unknown":
            if self.canonical_value is not None:
                raise ValueError("unknown authority field cannot carry a value")
            require_text(self.reason, "authority field unknown reason", maximum_bytes=512)
            return
        raise ValueError("authority field state is not admitted")

    def to_mapping(self) -> dict[str, object]:
        if self.state == "present":
            if self.canonical_value is None:
                raise AssertionError("validated present field lost its canonical value")
            return {
                "state": self.state,
                "value": load_strict_json(self.canonical_value, max_bytes=MAX_FIELD_VALUE_BYTES),
            }
        if self.state == "unknown":
            return {"state": self.state, "reason": self.reason}
        return {"state": self.state}


@dataclass(frozen=True, slots=True)
class AuthorityFieldEntry:
    name: str
    field: AuthorityField

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or _FIELD_NAME.fullmatch(self.name) is None
            or len(self.name.encode("utf-8")) > MAX_FIELD_NAME_BYTES
        ):
            raise ValueError("authority field name is not canonical")
        if type(self.field) is not AuthorityField:
            raise TypeError("authority field entry requires an exact field")

    def to_mapping(self) -> dict[str, object]:
        return {"name": self.name, "field": self.field.to_mapping()}


@dataclass(frozen=True, slots=True)
class TargetAuthorityKey:
    family: TargetAuthorityRowFamily
    member_id: str

    def __post_init__(self) -> None:
        if self.family not in _ROW_FIELDS:
            raise ValueError("target-authority row family is not admitted")
        require_text(
            self.member_id,
            "target-authority member id",
            maximum_bytes=MAX_MEMBER_ID_BYTES,
        )

    @property
    def sort_key(self) -> tuple[bytes, bytes]:
        return (utf16_sort_key(self.family), utf16_sort_key(self.member_id))

    def to_mapping(self) -> dict[str, object]:
        return {"family": self.family, "memberId": self.member_id}


@dataclass(frozen=True, slots=True)
class TargetAuthorityRow:
    key: TargetAuthorityKey
    disposition: AuthorityDisposition
    semantic_owner: str
    source_locator: str
    fields: tuple[AuthorityFieldEntry, ...]

    def __post_init__(self) -> None:
        if type(self.key) is not TargetAuthorityKey:
            raise TypeError("target-authority row requires an exact key")
        if self.disposition not in {"authority", "owner_approved_non_authority"}:
            raise ValueError("target-authority row disposition is not admitted")
        require_text(self.semantic_owner, "semantic owner", maximum_bytes=MAX_OWNER_ID_BYTES)
        require_text(self.source_locator, "source locator", maximum_bytes=MAX_SOURCE_LOCATOR_BYTES)
        if (
            type(self.fields) is not tuple
            or len(self.fields) > MAX_ROW_FIELDS
            or any(type(entry) is not AuthorityFieldEntry for entry in self.fields)
        ):
            raise TypeError("target-authority row fields must be a bounded exact tuple")
        names = tuple(entry.name for entry in self.fields)
        if names != tuple(sorted(set(names), key=utf16_sort_key)):
            raise ValueError("target-authority row fields must be canonical and unique")
        required, conditional = _ROW_FIELDS[self.key.family]
        if set(names) != required | conditional:
            raise ValueError("target-authority row fields do not match the family schema")
        for entry in self.fields:
            if entry.name in required and entry.field.state == "not_applicable":
                raise ValueError("required target-authority field cannot be not applicable")
        encode_mapping(self.to_mapping(), max_bytes=MAX_ROW_BYTES)

    @property
    def has_unknown(self) -> bool:
        return any(entry.field.state == "unknown" for entry in self.fields)

    @property
    def row_digest(self) -> str:
        return domain_digest(
            TARGET_AUTHORITY_ROW_SCHEMA,
            self.to_mapping(),
            max_bytes=MAX_ROW_BYTES,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": TARGET_AUTHORITY_ROW_SCHEMA,
            "key": self.key.to_mapping(),
            "disposition": self.disposition,
            "semanticOwner": self.semantic_owner,
            "sourceLocator": self.source_locator,
            "fields": [entry.to_mapping() for entry in self.fields],
        }


@dataclass(frozen=True, slots=True)
class TargetAuthoritySubject:
    scope: RepositoryScope
    authority_id: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("target-authority subject requires an exact repository scope")
        require_text(self.authority_id, "target-authority subject id", maximum_bytes=256)

    @property
    def subject_digest(self) -> str:
        return domain_digest(TARGET_AUTHORITY_SUBJECT_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": TARGET_AUTHORITY_SUBJECT_SCHEMA,
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "authorityId": self.authority_id,
        }


@dataclass(frozen=True, slots=True)
class EpochComponent:
    state: AuthorityFieldState
    digest: str | None = None
    reason: str | None = None

    @classmethod
    def present(cls, digest: str) -> Self:
        return cls("present", digest=digest)

    @classmethod
    def not_applicable(cls) -> Self:
        return cls("not_applicable")

    @classmethod
    def unknown(cls, reason: str) -> Self:
        return cls("unknown", reason=reason)

    def __post_init__(self) -> None:
        if self.state == "present":
            require_digest(self.digest, "epoch component")
            if self.reason is not None:
                raise ValueError("present epoch component cannot carry a reason")
            return
        if self.state == "not_applicable":
            if self.digest is not None or self.reason is not None:
                raise ValueError("not-applicable epoch component cannot carry data")
            return
        if self.state == "unknown":
            if self.digest is not None:
                raise ValueError("unknown epoch component cannot carry a digest")
            require_text(self.reason, "epoch unknown reason", maximum_bytes=512)
            return
        raise ValueError("epoch component state is not admitted")

    def to_mapping(self) -> dict[str, object]:
        if self.state == "present":
            return {"state": self.state, "digest": self.digest}
        if self.state == "unknown":
            return {"state": self.state, "reason": self.reason}
        return {"state": self.state}


@dataclass(frozen=True, slots=True)
class TargetAuthorityEpoch:
    phase: RelationEpochPhase
    source_manifest: EpochComponent
    provider_governance: EpochComponent
    policy: EpochComponent
    catalog: EpochComponent
    registry: EpochComponent
    owner: EpochComponent

    def __post_init__(self) -> None:
        if self.phase not in {"native_baseline", "adapted_target"}:
            raise ValueError("target-authority epoch phase is not admitted")
        components = self.components
        if any(type(component) is not EpochComponent for component in components):
            raise TypeError("target-authority epoch components must be exact")
        if any(
            component.state == "not_applicable"
            for component in (self.source_manifest, self.provider_governance, self.owner)
        ):
            raise ValueError("source, provider, and owner epochs are always applicable")
        if self.phase == "adapted_target" and any(
            component.state == "not_applicable" for component in components
        ):
            raise ValueError("adapted target epoch requires every component")

    @property
    def components(self) -> tuple[EpochComponent, ...]:
        return (
            self.source_manifest,
            self.provider_governance,
            self.policy,
            self.catalog,
            self.registry,
            self.owner,
        )

    @property
    def has_unknown(self) -> bool:
        return any(component.state == "unknown" for component in self.components)

    @property
    def epoch_digest(self) -> str:
        return domain_digest(TARGET_AUTHORITY_EPOCH_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": TARGET_AUTHORITY_EPOCH_SCHEMA,
            "phase": self.phase,
            "sourceManifest": self.source_manifest.to_mapping(),
            "providerGovernance": self.provider_governance.to_mapping(),
            "policy": self.policy.to_mapping(),
            "catalog": self.catalog.to_mapping(),
            "registry": self.registry.to_mapping(),
            "owner": self.owner.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class ProducerIdentity:
    producer_id: str
    version: str

    def __post_init__(self) -> None:
        require_text(self.producer_id, "producer id", maximum_bytes=MAX_PRODUCER_ID_BYTES)
        require_text(self.version, "producer version", maximum_bytes=MAX_PRODUCER_VERSION_BYTES)

    @property
    def identity_digest(self) -> str:
        return domain_digest(TARGET_AUTHORITY_PRODUCER_SCHEMA, self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": TARGET_AUTHORITY_PRODUCER_SCHEMA,
            "producerId": self.producer_id,
            "version": self.version,
        }


def require_canonical_rows(
    rows: tuple[TargetAuthorityRow, ...],
    *,
    allow_empty: bool,
) -> None:
    if (
        type(rows) is not tuple
        or len(rows) > MAX_RELATION_ROWS
        or any(type(row) is not TargetAuthorityRow for row in rows)
    ):
        raise TypeError("target-authority rows must be a bounded exact tuple")
    if not allow_empty and not rows:
        raise ValueError("target-authority relation must be non-empty")
    keys = tuple(row.key.sort_key for row in rows)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("target-authority rows must have unique canonical keys")


def require_digest(value: object, name: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be an exact SHA-256 digest")


def require_text(value: object, name: str, *, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\0" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{name} must be bounded canonical Unicode scalar text")


def row_field_names(family: TargetAuthorityRowFamily) -> tuple[str, ...]:
    try:
        required, conditional = _ROW_FIELDS[family]
    except KeyError as error:
        raise ValueError("target-authority row family is not admitted") from error
    return tuple(sorted(required | conditional, key=utf16_sort_key))
