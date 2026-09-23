"""Deterministic same-snapshot reusable-workflow graph analysis."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery._validation import is_full_sha1, require_workflow_path
from ci_coordinator.workflow_discovery.evidence import Fact, Provenance, Unknown
from ci_coordinator.workflow_discovery.graph_model import (
    CallEdge,
    CallKind,
    CallStatus,
    GraphAnalysis,
)
from ci_coordinator.workflow_discovery.summary import ParsedWorkflow

_MAX_LOCAL_CALL_DEPTH = 10


@dataclass(frozen=True, slots=True)
class _EdgeDraft:
    caller_path: str
    caller_job_id: str
    uses: str
    kind: CallKind
    status: CallStatus
    target_path: str | None
    remote_ref: str | None
    remote_ref_immutable: bool | None
    provenance: Provenance


def analyze_call_graph(workflows: tuple[ParsedWorkflow, ...]) -> GraphAnalysis:
    paths = frozenset(workflow.summary.path for workflow in workflows)
    evidence = _uses_provenance(workflows)
    drafts = [
        _edge_draft(
            workflow.summary.path,
            job.job_id,
            job.uses,
            job.uses_dynamic,
            paths,
            evidence[job.subject_id],
        )
        for workflow in workflows
        for job in workflow.summary.jobs
        if job.uses is not None
    ]
    local_pairs = {
        (draft.caller_path, draft.target_path)
        for draft in drafts
        if draft.kind == "local" and draft.status == "resolved" and draft.target_path is not None
    }
    cycle_pairs = {
        pair
        for pair in local_pairs
        if _reachable(pair[1], pair[0], local_pairs, visited=frozenset())
    }
    depth_exceeded_pairs = _depth_exceeded_pairs(local_pairs - cycle_pairs)
    classified = [
        replace(draft, status="cycle")
        if (draft.caller_path, draft.target_path) in cycle_pairs
        else replace(draft, status="depth_exceeded")
        if (draft.caller_path, draft.target_path) in depth_exceeded_pairs
        else draft
        for draft in drafts
    ]
    edges = tuple(
        sorted(
            (
                CallEdge.create(
                    caller_workflow_path=draft.caller_path,
                    caller_job_id=draft.caller_job_id,
                    uses=draft.uses,
                    kind=draft.kind,
                    status=draft.status,
                    target_path=draft.target_path,
                    remote_ref=draft.remote_ref,
                    remote_ref_immutable=draft.remote_ref_immutable,
                    provenance=draft.provenance,
                )
                for draft in classified
            ),
            key=lambda item: utf16_sort_key(item.edge_id),
        )
    )
    facts, unknowns = _graph_evidence(edges)
    local_graph_closed = all(
        edge.kind == "remote" or (edge.kind == "local" and edge.status == "resolved")
        for edge in edges
    )
    return GraphAnalysis(edges, facts, unknowns, local_graph_closed)


def _uses_provenance(workflows: tuple[ParsedWorkflow, ...]) -> dict[str, Provenance]:
    result: dict[str, Provenance] = {}
    for workflow in workflows:
        for fact in workflow.facts:
            if fact.field == "job.uses":
                result[fact.subject_id] = fact.provenance
        for unknown in workflow.unknowns:
            if unknown.field == "job.uses":
                result[unknown.subject_id] = unknown.provenance
    return result


def _edge_draft(
    caller_path: str,
    job_id: str,
    uses: str,
    uses_dynamic: bool,
    paths: frozenset[str],
    provenance: Provenance,
) -> _EdgeDraft:
    if uses_dynamic:
        return _EdgeDraft(
            caller_path, job_id, uses, "dynamic", "dynamic", None, None, None, provenance
        )
    if uses.startswith(("./", "$/")):
        target = uses[2:]
        if "@" in target:
            return _EdgeDraft(
                caller_path, job_id, uses, "unknown", "invalid", None, None, None, provenance
            )
        try:
            require_workflow_path(target)
        except ValueError:
            return _EdgeDraft(
                caller_path, job_id, uses, "unknown", "invalid", None, None, None, provenance
            )
        return _EdgeDraft(
            caller_path,
            job_id,
            uses,
            "local",
            "resolved" if target in paths else "missing",
            target,
            None,
            None,
            provenance,
        )
    coordinate, separator, remote_ref = uses.rpartition("@")
    parts = coordinate.split("/")
    if (
        separator
        and remote_ref
        and len(parts) == 5
        and all(parts[index] for index in (0, 1, 4))
        and parts[2:4] == [".github", "workflows"]
        and parts[4].endswith((".yml", ".yaml"))
    ):
        return _EdgeDraft(
            caller_path,
            job_id,
            uses,
            "remote",
            "remote",
            None,
            remote_ref,
            is_full_sha1(remote_ref),
            provenance,
        )
    return _EdgeDraft(caller_path, job_id, uses, "unknown", "invalid", None, None, None, provenance)


def _graph_evidence(edges: tuple[CallEdge, ...]) -> tuple[tuple[Fact, ...], tuple[Unknown, ...]]:
    facts: list[Fact] = []
    unknowns: list[Unknown] = []
    for edge in edges:
        subject_id = f"call:{edge.edge_id.removeprefix('edge:')}"
        if edge.kind == "local" and edge.status == "resolved":
            facts.append(
                Fact.create(
                    subject_id=subject_id,
                    category="graph",
                    field="call.target",
                    value=edge.target_path,
                    criticality="safety",
                    provenance=edge.provenance,
                )
            )
            continue
        reason = {
            "cycle": "local_call_cycle",
            "depth_exceeded": "local_call_depth_exceeded",
            "dynamic": "dynamic_call_target",
            "invalid": "invalid_call_coordinate",
            "missing": "local_call_target_missing",
            "remote": "remote_call_not_fetched",
        }[edge.status]
        unknowns.append(
            Unknown.create(
                subject_id=subject_id,
                category="graph",
                field="call.target",
                reason=reason,
                criticality="safety",
                provenance=edge.provenance,
                observed_syntax=edge.uses,
            )
        )
    return (
        tuple(sorted(facts, key=lambda item: utf16_sort_key(item.fact_id))),
        tuple(sorted(unknowns, key=lambda item: utf16_sort_key(item.unknown_id))),
    )


def _reachable(
    start: str,
    target: str,
    edges: set[tuple[str, str]],
    *,
    visited: frozenset[str],
) -> bool:
    if start == target:
        return True
    if start in visited:
        return False
    next_visited = visited | {start}
    return any(
        _reachable(destination, target, edges, visited=next_visited)
        for source, destination in edges
        if source == start
    )


def _depth_exceeded_pairs(edges: set[tuple[str, str]]) -> set[tuple[str, str]]:
    predecessors: dict[str, set[str]] = {}
    successors: dict[str, set[str]] = {}
    for source, destination in edges:
        predecessors.setdefault(destination, set()).add(source)
        predecessors.setdefault(source, set())
        successors.setdefault(source, set()).add(destination)
        successors.setdefault(destination, set())

    prefix_memo: dict[str, int] = {}
    suffix_memo: dict[str, int] = {}

    def prefix(node: str) -> int:
        if node not in prefix_memo:
            prefix_memo[node] = 1 + max(
                (prefix(parent) for parent in predecessors[node]),
                default=0,
            )
        return prefix_memo[node]

    def suffix(node: str) -> int:
        if node not in suffix_memo:
            suffix_memo[node] = 1 + max(
                (suffix(child) for child in successors[node]),
                default=0,
            )
        return suffix_memo[node]

    return {
        (source, destination)
        for source, destination in edges
        if prefix(source) + suffix(destination) > _MAX_LOCAL_CALL_DEPTH
    }
