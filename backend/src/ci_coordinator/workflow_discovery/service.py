"""Authorization-first workflow discovery orchestration."""

from __future__ import annotations

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import CanonicalJsonError, Clock, NoQueueAdmission, utf16_sort_key
from ci_coordinator.workflow_discovery._validation import is_full_sha1
from ci_coordinator.workflow_discovery.adoption import assess_workflow_adoption
from ci_coordinator.workflow_discovery.component_passport import (
    ComponentPassport,
    ComponentReference,
)
from ci_coordinator.workflow_discovery.component_resolution import (
    resolve_component_references,
    unresolved_component_references,
)
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
    ComponentRevisionResolver,
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
        component_resolver: ComponentRevisionResolver | None = None,
        clock: Clock | None = None,
    ) -> None:
        if (component_resolver is None) != (clock is None):
            raise ValueError("component resolution requires exactly one resolver and clock")
        self._authorizer = authorizer
        self._reader = reader
        self._component_resolver = component_resolver
        self._clock = clock
        self._resolution_admission = NoQueueAdmission(1)

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
        report = _report(snapshot)
        if isinstance(report, WorkflowDiscoveryUnavailable):
            return report
        references = await self._component_references(actor=actor, report=report)
        return WorkflowDiscoveryCompleted(
            report,
            generate_proposal(report, default_branch_head=revision is None),
            snapshot.target_projection,
            assess_workflow_adoption(report, target_projection=snapshot.target_projection),
            ComponentPassport.create(report, references),
        )

    async def _component_references(
        self,
        *,
        actor: str,
        report: DiscoveryReport,
    ) -> tuple[ComponentReference, ...]:
        # Provider reads start only after the caller snapshot is complete; no
        # database transaction or snapshot lease is held while they run.
        if self._component_resolver is None or self._clock is None:
            return unresolved_component_references(report, "resolution_not_requested")
        lease = self._resolution_admission.try_acquire()
        if lease is None:
            return unresolved_component_references(report, "resolution_overloaded")
        try:
            return await resolve_component_references(
                report,
                actor=actor,
                authorizer=self._authorizer,
                resolver=self._component_resolver,
                clock=self._clock,
            )
        finally:
            lease.release()


def _report(snapshot: RepositoryWorkflowSnapshot) -> DiscoveryReport | WorkflowDiscoveryUnavailable:
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
        return DiscoveryReport.create(
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
