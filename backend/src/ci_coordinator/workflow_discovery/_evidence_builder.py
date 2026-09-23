"""Single-write construction for the closed discovery predicate ledger."""

from __future__ import annotations

from dataclasses import dataclass, field

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery._yaml_nodes import YamlMapping, YamlNode, node_location
from ci_coordinator.workflow_discovery.evidence import (
    Criticality,
    EvidenceCategory,
    Fact,
    FactValue,
    Provenance,
    Unknown,
    YamlLocation,
)


@dataclass(slots=True)
class EvidenceBuilder:
    base: Provenance
    _facts: list[Fact] = field(default_factory=list)
    _unknowns: list[Unknown] = field(default_factory=list)
    _predicates: set[tuple[str, str]] = field(default_factory=set)

    def fact(
        self,
        *,
        subject_id: str,
        category: EvidenceCategory,
        field_name: str,
        value: FactValue,
        criticality: Criticality,
        node: YamlNode | None,
        parent: YamlMapping,
    ) -> None:
        self._claim(subject_id, field_name)
        self._facts.append(
            Fact.create(
                subject_id=subject_id,
                category=category,
                field=field_name,
                value=value,
                criticality=criticality,
                provenance=self._provenance(field_name, node, parent),
            )
        )

    def unknown(
        self,
        *,
        subject_id: str,
        category: EvidenceCategory,
        field_name: str,
        reason: str,
        criticality: Criticality,
        node: YamlNode | None,
        parent: YamlMapping,
        observed_syntax: str | None = None,
    ) -> None:
        self._claim(subject_id, field_name)
        self._unknowns.append(
            Unknown.create(
                subject_id=subject_id,
                category=category,
                field=field_name,
                reason=reason,
                criticality=criticality,
                provenance=self._provenance(field_name, node, parent),
                observed_syntax=observed_syntax,
            )
        )

    def finish(self) -> tuple[tuple[Fact, ...], tuple[Unknown, ...]]:
        return (
            tuple(sorted(self._facts, key=lambda item: utf16_sort_key(item.fact_id))),
            tuple(sorted(self._unknowns, key=lambda item: utf16_sort_key(item.unknown_id))),
        )

    def _claim(self, subject_id: str, field_name: str) -> None:
        predicate = (subject_id, field_name)
        if predicate in self._predicates:
            raise ValueError("discovery predicate has more than one terminal outcome")
        self._predicates.add(predicate)

    def _provenance(
        self,
        field_name: str,
        node: YamlNode | None,
        parent: YamlMapping,
    ) -> Provenance:
        line, column = node_location(node, parent)
        return Provenance(
            scope=self.base.scope,
            revision=self.base.revision,
            workflow_path=self.base.workflow_path,
            blob_sha=self.base.blob_sha,
            parser_version=self.base.parser_version,
            location=YamlLocation(field_name, line, column),
        )
