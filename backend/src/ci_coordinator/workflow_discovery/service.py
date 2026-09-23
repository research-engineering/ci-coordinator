"""Authorization-first workflow discovery orchestration."""

from __future__ import annotations

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import CanonicalJsonError, utf16_sort_key
from ci_coordinator.workflow_discovery._validation import is_full_sha1
from ci_coordinator.workflow_discovery.adoption import assess_workflow_adoption
from ci_coordinator.workflow_discovery.evidence import Provenance, Unknown, YamlLocation
from ci_coordinator.workflow_discovery.graph import analyze_call_graph
from ci_coordinator.workflow_discovery.outcomes import (
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryInvalidRequest,
    WorkflowDiscoveryOutcome,
    WorkflowDiscoveryUnavailable,
)
from ci_coordinator.workflow_discovery.parser import parse_workflow
from ci_coordinator.workflow_discovery.ports import (
    WorkflowDiscoveryAuthorizer,
    WorkflowSnapshotReader,
)
from ci_coordinator.workflow_discovery.proposal import generate_proposal
from ci_coordinator.workflow_discovery.report import PARSER_VERSION, DiscoveryReport
from ci_coordinator.workflow_discovery.source import (
    RepositoryWorkflowSnapshot,
    WorkflowSourceFailure,
)
from ci_coordinator.workflow_discovery.summary import ParsedWorkflow, WorkflowParseFailure


class WorkflowDiscoveryService:
    def __init__(
        self,
        *,
        authorizer: WorkflowDiscoveryAuthorizer,
        reader: WorkflowSnapshotReader,
    ) -> None:
        self._authorizer = authorizer
        self._reader = reader

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        revision: str | None,
    ) -> WorkflowDiscoveryOutcome:
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return WorkflowDiscoveryForbidden()
        if revision is not None and not is_full_sha1(revision):
            return WorkflowDiscoveryInvalidRequest("invalid_revision")
        snapshot = await self._reader.read(scope=scope, revision=revision)
        if isinstance(snapshot, WorkflowDiscoveryUnavailable):
            return snapshot
        if not isinstance(snapshot, RepositoryWorkflowSnapshot):
            raise TypeError("workflow snapshot reader returned an unsupported outcome")
        return _complete(snapshot, default_branch_head=revision is None)


def _complete(
    snapshot: RepositoryWorkflowSnapshot,
    *,
    default_branch_head: bool,
) -> WorkflowDiscoveryOutcome:
    parsed: list[ParsedWorkflow] = []
    unknowns = [_source_failure_unknown(snapshot, failure) for failure in snapshot.failures]
    for source in snapshot.sources:
        outcome = parse_workflow(
            source,
            scope=snapshot.repository.scope,
            revision=snapshot.revision,
            default_branch=snapshot.repository.default_branch,
        )
        if isinstance(outcome, WorkflowParseFailure):
            unknowns.append(outcome.unknown)
        else:
            parsed.append(outcome)
    parsed_tuple = tuple(sorted(parsed, key=lambda item: utf16_sort_key(item.summary.path)))
    graph = analyze_call_graph(parsed_tuple)
    facts = tuple(
        sorted(
            (*graph.facts, *(fact for workflow in parsed_tuple for fact in workflow.facts)),
            key=lambda item: utf16_sort_key(item.fact_id),
        )
    )
    all_unknowns = tuple(
        sorted(
            (
                *graph.unknowns,
                *unknowns,
                *(unknown for workflow in parsed_tuple for unknown in workflow.unknowns),
            ),
            key=lambda item: utf16_sort_key(item.unknown_id),
        )
    )
    try:
        report = DiscoveryReport.create(
            repository=snapshot.repository,
            revision=snapshot.revision,
            sources=snapshot.source_identities,
            workflows=tuple(workflow.summary for workflow in parsed_tuple),
            call_edges=graph.edges,
            facts=facts,
            unknowns=all_unknowns,
            complete=not snapshot.failures and len(parsed_tuple) == len(snapshot.sources),
            local_graph_closed=graph.local_graph_closed,
        )
    except CanonicalJsonError as error:
        if error.code == "canonical_json_max_bytes_exceeded":
            return WorkflowDiscoveryUnavailable("report_limit_exceeded")
        raise
    return WorkflowDiscoveryCompleted(
        report,
        generate_proposal(report, default_branch_head=default_branch_head),
        snapshot.target_projection,
        assess_workflow_adoption(report, target_projection=snapshot.target_projection),
    )


def _source_failure_unknown(
    snapshot: RepositoryWorkflowSnapshot,
    failure: WorkflowSourceFailure,
) -> Unknown:
    return Unknown.create(
        subject_id="workflow-document",
        category="semantics",
        field="workflow.document",
        reason=f"source_{failure.reason}",
        criticality="safety",
        provenance=Provenance(
            scope=snapshot.repository.scope,
            revision=snapshot.revision,
            workflow_path=failure.path,
            blob_sha=failure.blob_sha,
            parser_version=PARSER_VERSION,
            location=YamlLocation("workflow.document", 1, 1),
        ),
    )
