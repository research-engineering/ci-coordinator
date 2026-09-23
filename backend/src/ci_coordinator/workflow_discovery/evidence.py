"""Provenance-bound facts and typed unknowns."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Self

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.workflow_discovery._validation import (
    MAX_TEXT_BYTES,
    require_identifier,
    require_sha1,
    require_text,
    require_workflow_path,
)

type EvidenceCategory = Literal[
    "identity",
    "invocation",
    "graph",
    "authority",
    "execution",
    "semantics",
]
type Criticality = Literal["informational", "safety"]
type FactScalar = bool | int | float | str | None
type FactValue = FactScalar | tuple["FactValue", ...]

_MAX_FACT_VALUE_DEPTH = 8
_MAX_FACT_VALUE_NODES = 1_024
_EVIDENCE_CATEGORIES = frozenset(
    {"identity", "invocation", "graph", "authority", "execution", "semantics"}
)
_CRITICALITIES = frozenset({"informational", "safety"})


@dataclass(frozen=True, slots=True)
class YamlLocation:
    path: str
    line: int
    column: int

    def __post_init__(self) -> None:
        require_text(self.path, "YAML path", maximum_bytes=1_024, allow_empty=True)
        for name, value in (("line", self.line), ("column", self.column)):
            if type(value) is not int or value < 1:
                raise ValueError(f"YAML {name} must be a positive integer")

    def to_identity_mapping(self) -> dict[str, object]:
        return {"path": self.path, "line": self.line, "column": self.column}


@dataclass(frozen=True, slots=True)
class Provenance:
    scope: RepositoryScope
    revision: str
    workflow_path: str
    blob_sha: str
    parser_version: str
    location: YamlLocation

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("provenance requires an exact RepositoryScope")
        require_sha1(self.revision, "provenance revision")
        require_workflow_path(self.workflow_path)
        require_sha1(self.blob_sha, "provenance blob SHA")
        require_text(self.parser_version, "parser version", maximum_bytes=128)
        if type(self.location) is not YamlLocation:
            raise TypeError("provenance requires an exact YAML location")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "revision": self.revision,
            "workflowPath": self.workflow_path,
            "blobSha": self.blob_sha,
            "parserVersion": self.parser_version,
            "location": self.location.to_identity_mapping(),
        }


@dataclass(frozen=True, slots=True)
class Fact:
    fact_id: str
    subject_id: str
    category: EvidenceCategory
    field: str
    value: FactValue
    criticality: Criticality
    provenance: Provenance

    @classmethod
    def create(
        cls,
        *,
        subject_id: str,
        category: EvidenceCategory,
        field: str,
        value: FactValue,
        criticality: Criticality,
        provenance: Provenance,
    ) -> Self:
        return cls(
            fact_id=_fact_id(subject_id, category, field, value, criticality, provenance),
            subject_id=subject_id,
            category=category,
            field=field,
            value=value,
            criticality=criticality,
            provenance=provenance,
        )

    def __post_init__(self) -> None:
        _require_evidence_common(
            identifier=self.fact_id,
            prefix="fact:",
            subject_id=self.subject_id,
            category=self.category,
            field=self.field,
            criticality=self.criticality,
            provenance=self.provenance,
        )
        validate_fact_value(self.value)
        expected = _fact_id(
            self.subject_id,
            self.category,
            self.field,
            self.value,
            self.criticality,
            self.provenance,
        )
        if self.fact_id != expected:
            raise ValueError("fact id does not bind its canonical evidence projection")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "factId": self.fact_id,
            "subjectId": self.subject_id,
            "category": self.category,
            "field": self.field,
            "value": fact_value_projection(self.value),
            "criticality": self.criticality,
            "provenance": self.provenance.to_identity_mapping(),
        }


@dataclass(frozen=True, slots=True)
class Unknown:
    unknown_id: str
    subject_id: str
    category: EvidenceCategory
    field: str
    reason: str
    criticality: Criticality
    provenance: Provenance
    observed_syntax: str | None = None

    @classmethod
    def create(
        cls,
        *,
        subject_id: str,
        category: EvidenceCategory,
        field: str,
        reason: str,
        criticality: Criticality,
        provenance: Provenance,
        observed_syntax: str | None = None,
    ) -> Self:
        return cls(
            unknown_id=_unknown_id(
                subject_id,
                category,
                field,
                reason,
                criticality,
                provenance,
                observed_syntax,
            ),
            subject_id=subject_id,
            category=category,
            field=field,
            reason=reason,
            criticality=criticality,
            provenance=provenance,
            observed_syntax=observed_syntax,
        )

    def __post_init__(self) -> None:
        _require_evidence_common(
            identifier=self.unknown_id,
            prefix="unknown:",
            subject_id=self.subject_id,
            category=self.category,
            field=self.field,
            criticality=self.criticality,
            provenance=self.provenance,
        )
        require_text(self.reason, "unknown reason", maximum_bytes=256)
        if self.observed_syntax is not None:
            require_text(
                self.observed_syntax,
                "observed syntax",
                maximum_bytes=MAX_TEXT_BYTES,
                allow_empty=True,
            )
        expected = _unknown_id(
            self.subject_id,
            self.category,
            self.field,
            self.reason,
            self.criticality,
            self.provenance,
            self.observed_syntax,
        )
        if self.unknown_id != expected:
            raise ValueError("unknown id does not bind its canonical evidence projection")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "unknownId": self.unknown_id,
            "subjectId": self.subject_id,
            "category": self.category,
            "field": self.field,
            "reason": self.reason,
            "criticality": self.criticality,
            "observedSyntax": self.observed_syntax,
            "provenance": self.provenance.to_identity_mapping(),
        }


def require_canonical_evidence(facts: tuple[Fact, ...], unknowns: tuple[Unknown, ...]) -> None:
    fact_ids = tuple(fact.fact_id for fact in facts)
    unknown_ids = tuple(unknown.unknown_id for unknown in unknowns)
    if fact_ids != tuple(sorted(set(fact_ids), key=utf16_sort_key)):
        raise ValueError("facts must have unique canonical ids")
    if unknown_ids != tuple(sorted(set(unknown_ids), key=utf16_sort_key)):
        raise ValueError("unknowns must have unique canonical ids")


def fact_value_projection(value: FactValue) -> object:
    if type(value) is tuple:
        return [fact_value_projection(item) for item in value]
    return value


def validate_fact_value(value: FactValue) -> None:
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        candidate, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_FACT_VALUE_NODES or depth > _MAX_FACT_VALUE_DEPTH:
            raise ValueError("fact value exceeds its structural bound")
        if candidate is None or type(candidate) is bool:
            continue
        if type(candidate) is int:
            if abs(candidate) > MAX_SAFE_JSON_INTEGER:
                raise ValueError("fact integer exceeds the JSON safe-integer range")
            continue
        if type(candidate) is float:
            if not math.isfinite(candidate) or abs(candidate) > MAX_SAFE_JSON_INTEGER:
                raise ValueError("fact number is not finite and safely bounded")
            continue
        if type(candidate) is str:
            require_text(
                candidate,
                "fact text",
                maximum_bytes=MAX_TEXT_BYTES,
                allow_empty=True,
            )
            continue
        if type(candidate) is tuple:
            stack.extend((item, depth + 1) for item in candidate)
            continue
        raise TypeError("fact value escaped the immutable JSON scalar/tuple algebra")


def _fact_id(
    subject_id: str,
    category: EvidenceCategory,
    field: str,
    value: FactValue,
    criticality: Criticality,
    provenance: Provenance,
) -> str:
    projection = _evidence_projection(
        subject_id,
        category,
        field,
        criticality,
        provenance,
        "value",
        fact_value_projection(value),
    )
    return f"fact:{hash_object(projection)[:32]}"


def _unknown_id(
    subject_id: str,
    category: EvidenceCategory,
    field: str,
    reason: str,
    criticality: Criticality,
    provenance: Provenance,
    observed_syntax: str | None,
) -> str:
    projection = _evidence_projection(
        subject_id,
        category,
        field,
        criticality,
        provenance,
        "unknown",
        {"reason": reason, "observedSyntax": observed_syntax},
    )
    return f"unknown:{hash_object(projection)[:32]}"


def _evidence_projection(
    subject_id: str,
    category: EvidenceCategory,
    field: str,
    criticality: Criticality,
    provenance: Provenance,
    payload_key: str,
    payload_value: object,
) -> dict[str, object]:
    return {
        "subjectId": subject_id,
        "category": category,
        "field": field,
        payload_key: payload_value,
        "criticality": criticality,
        "provenance": provenance.to_identity_mapping(),
    }


def _require_evidence_common(
    *,
    identifier: str,
    prefix: str,
    subject_id: str,
    category: EvidenceCategory,
    field: str,
    criticality: Criticality,
    provenance: Provenance,
) -> None:
    require_identifier(identifier, prefix, "evidence id")
    require_text(subject_id, "evidence subject id", maximum_bytes=128)
    if category not in _EVIDENCE_CATEGORIES:
        raise ValueError("evidence category is not admitted")
    require_text(field, "evidence field", maximum_bytes=256)
    if criticality not in _CRITICALITIES:
        raise ValueError("evidence criticality is not admitted")
    if type(provenance) is not Provenance:
        raise TypeError("evidence requires exact provenance")
