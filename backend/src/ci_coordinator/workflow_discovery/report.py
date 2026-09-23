"""Canonical exact-snapshot workflow discovery report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Self

from ci_coordinator.kernel import bounded_canonical_json, sha256_hex, utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    MAX_REPORT_BYTES,
    MAX_REPORT_FACTS,
    MAX_REPORT_JOBS,
    MAX_REPORT_UNKNOWNS,
    require_canonical_text_tuple,
    require_exact_tuple,
    require_sha1,
    require_sha256,
)
from ci_coordinator.workflow_discovery.evidence import (
    Fact,
    Unknown,
    require_canonical_evidence,
)
from ci_coordinator.workflow_discovery.graph_model import CallEdge
from ci_coordinator.workflow_discovery.source import RepositoryIdentity, WorkflowSourceIdentity
from ci_coordinator.workflow_discovery.summary import WorkflowSummary, require_predicate_closure

PARSER_VERSION: Final = "github-actions-static/v2"
DISCOVERY_NON_CLAIMS: Final = tuple(
    sorted(
        (
            "check identity is not proven",
            "environment protection is not proven",
            "external actions and reusable workflows are not executed",
            "fork exposure is not proven",
            "oidc availability is not proven",
            "remote reusable workflows are not fetched",
            "runner availability is not proven",
            "runtime workflow behavior is not proven",
            "secret existence and contents are not proven",
        ),
        key=utf16_sort_key,
    )
)


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    repository: RepositoryIdentity
    revision: str
    parser_version: str
    sources: tuple[WorkflowSourceIdentity, ...]
    workflows: tuple[WorkflowSummary, ...]
    call_edges: tuple[CallEdge, ...]
    facts: tuple[Fact, ...]
    unknowns: tuple[Unknown, ...]
    complete: bool
    local_graph_closed: bool
    non_claims: tuple[str, ...]
    inventory_digest: str

    @classmethod
    def create(
        cls,
        *,
        repository: RepositoryIdentity,
        revision: str,
        sources: tuple[WorkflowSourceIdentity, ...],
        workflows: tuple[WorkflowSummary, ...],
        call_edges: tuple[CallEdge, ...],
        facts: tuple[Fact, ...],
        unknowns: tuple[Unknown, ...],
        complete: bool,
        local_graph_closed: bool,
    ) -> Self:
        ordered_sources = tuple(sorted(sources, key=lambda item: utf16_sort_key(item.path)))
        ordered_workflows = tuple(sorted(workflows, key=lambda item: utf16_sort_key(item.path)))
        ordered_edges = tuple(sorted(call_edges, key=lambda item: utf16_sort_key(item.edge_id)))
        ordered_facts = tuple(sorted(facts, key=lambda item: utf16_sort_key(item.fact_id)))
        ordered_unknowns = tuple(sorted(unknowns, key=lambda item: utf16_sort_key(item.unknown_id)))
        encoded = bounded_canonical_json(
            _report_projection(
                repository=repository,
                revision=revision,
                parser_version=PARSER_VERSION,
                sources=ordered_sources,
                workflows=ordered_workflows,
                call_edges=ordered_edges,
                facts=ordered_facts,
                unknowns=ordered_unknowns,
                complete=complete,
                local_graph_closed=local_graph_closed,
                non_claims=DISCOVERY_NON_CLAIMS,
            ),
            max_bytes=MAX_REPORT_BYTES,
        )
        return cls(
            repository=repository,
            revision=revision,
            parser_version=PARSER_VERSION,
            sources=ordered_sources,
            workflows=ordered_workflows,
            call_edges=ordered_edges,
            facts=ordered_facts,
            unknowns=ordered_unknowns,
            complete=complete,
            local_graph_closed=local_graph_closed,
            non_claims=DISCOVERY_NON_CLAIMS,
            inventory_digest=sha256_hex(encoded),
        )

    def __post_init__(self) -> None:
        if type(self.repository) is not RepositoryIdentity:
            raise TypeError("discovery report requires an exact repository identity")
        require_sha1(self.revision, "report revision")
        if self.parser_version != PARSER_VERSION:
            raise ValueError("discovery report parser version is not admitted")
        require_exact_tuple(self.sources, WorkflowSourceIdentity, "report sources")
        require_exact_tuple(self.workflows, WorkflowSummary, "report workflows")
        require_exact_tuple(self.call_edges, CallEdge, "report call edges")
        require_exact_tuple(self.facts, Fact, "report facts")
        require_exact_tuple(self.unknowns, Unknown, "report unknowns")
        source_paths = tuple(source.path for source in self.sources)
        workflow_paths = tuple(workflow.path for workflow in self.workflows)
        if source_paths != tuple(sorted(set(source_paths), key=utf16_sort_key)):
            raise ValueError("report sources must have unique canonical paths")
        if workflow_paths != tuple(sorted(set(workflow_paths), key=utf16_sort_key)):
            raise ValueError("report workflows must have unique canonical paths")
        if sum(len(workflow.jobs) for workflow in self.workflows) > MAX_REPORT_JOBS:
            raise ValueError("discovery report exceeds its aggregate job bound")
        if len(self.facts) > MAX_REPORT_FACTS or len(self.unknowns) > MAX_REPORT_UNKNOWNS:
            raise ValueError("discovery report exceeds its evidence count bound")
        require_canonical_evidence(self.facts, self.unknowns)
        edge_ids = tuple(edge.edge_id for edge in self.call_edges)
        if edge_ids != tuple(sorted(set(edge_ids), key=utf16_sort_key)):
            raise ValueError("report call edges must have unique canonical ids")
        if type(self.complete) is not bool or type(self.local_graph_closed) is not bool:
            raise TypeError("report state flags must be exact booleans")
        require_canonical_text_tuple(self.non_claims, "report non-claims", allow_empty=False)
        if self.non_claims != DISCOVERY_NON_CLAIMS:
            raise ValueError("discovery report non-claims must use the closed catalog")
        require_sha256(self.inventory_digest, "inventory digest")
        _require_source_termination(self, source_paths, workflow_paths)
        _require_report_provenance(self)
        _require_call_edge_closure(self)
        for workflow in self.workflows:
            require_predicate_closure(workflow, self.facts, self.unknowns)
        expected = sha256_hex(
            bounded_canonical_json(_report_projection_from_report(self), max_bytes=MAX_REPORT_BYTES)
        )
        if self.inventory_digest != expected:
            raise ValueError("inventory digest does not bind the canonical report")


def _require_source_termination(
    report: DiscoveryReport,
    source_paths: tuple[str, ...],
    workflow_paths: tuple[str, ...],
) -> None:
    failed_paths = tuple(
        sorted(
            {
                unknown.provenance.workflow_path
                for unknown in report.unknowns
                if unknown.field == "workflow.document"
            },
            key=utf16_sort_key,
        )
    )
    if set(workflow_paths) & set(failed_paths) or set(source_paths) != {
        *workflow_paths,
        *failed_paths,
    }:
        raise ValueError("every source must terminate as one parsed workflow or parse failure")
    if report.complete != (not failed_paths):
        raise ValueError("report completeness must equal absence of source parse failures")


def _require_report_provenance(report: DiscoveryReport) -> None:
    source_index = {source.path: source for source in report.sources}
    provenances = (
        *(edge.provenance for edge in report.call_edges),
        *(fact.provenance for fact in report.facts),
        *(unknown.provenance for unknown in report.unknowns),
    )
    for provenance in provenances:
        source = source_index.get(provenance.workflow_path)
        if (
            provenance.scope != report.repository.scope
            or provenance.revision != report.revision
            or provenance.parser_version != report.parser_version
            or source is None
            or source.blob_sha != provenance.blob_sha
        ):
            raise ValueError("report evidence crosses its exact snapshot identity")


def _require_call_edge_closure(report: DiscoveryReport) -> None:
    expected = {
        (workflow.path, job.job_id): job
        for workflow in report.workflows
        for job in workflow.jobs
        if job.uses is not None
    }
    actual = {(edge.caller_workflow_path, edge.caller_job_id): edge for edge in report.call_edges}
    if len(actual) != len(report.call_edges) or actual.keys() != expected.keys():
        raise ValueError("every job uses predicate must terminate as exactly one call edge")
    for coordinate, job in expected.items():
        edge = actual[coordinate]
        if edge.uses != job.uses or job.uses_dynamic != (edge.kind == "dynamic"):
            raise ValueError("call edge must preserve its job uses syntax and dynamism")
    expected_closed = all(
        edge.kind == "remote" or (edge.kind == "local" and edge.status == "resolved")
        for edge in report.call_edges
    )
    if report.local_graph_closed != expected_closed:
        raise ValueError("report local graph closure must be derived from its call edges")


def _report_projection_from_report(report: DiscoveryReport) -> dict[str, object]:
    return _report_projection(
        repository=report.repository,
        revision=report.revision,
        parser_version=report.parser_version,
        sources=report.sources,
        workflows=report.workflows,
        call_edges=report.call_edges,
        facts=report.facts,
        unknowns=report.unknowns,
        complete=report.complete,
        local_graph_closed=report.local_graph_closed,
        non_claims=report.non_claims,
    )


def _report_projection(
    *,
    repository: RepositoryIdentity,
    revision: str,
    parser_version: str,
    sources: tuple[WorkflowSourceIdentity, ...],
    workflows: tuple[WorkflowSummary, ...],
    call_edges: tuple[CallEdge, ...],
    facts: tuple[Fact, ...],
    unknowns: tuple[Unknown, ...],
    complete: bool,
    local_graph_closed: bool,
    non_claims: tuple[str, ...],
) -> dict[str, object]:
    return {
        "repository": repository.to_identity_mapping(),
        "revision": revision,
        "parserVersion": parser_version,
        "sources": [source.to_identity_mapping() for source in sources],
        "workflows": [workflow.to_identity_mapping() for workflow in workflows],
        "callEdges": [edge.to_identity_mapping() for edge in call_edges],
        "facts": [fact.to_identity_mapping() for fact in facts],
        "unknowns": [unknown.to_identity_mapping() for unknown in unknowns],
        "complete": complete,
        "localGraphClosed": local_graph_closed,
        "nonClaims": list(non_claims),
    }
