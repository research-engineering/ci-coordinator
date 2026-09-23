"""Workflow discovery use-case and source-read outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.workflow_discovery._validation import require_text
from ci_coordinator.workflow_discovery.adoption import AdoptionAssessment, assess_workflow_adoption
from ci_coordinator.workflow_discovery.adoption_target import AdoptionTargetProjection
from ci_coordinator.workflow_discovery.proposal_model import ProposalManifest
from ci_coordinator.workflow_discovery.report import DiscoveryReport
from ci_coordinator.workflow_discovery.source import RepositoryWorkflowSnapshot


@dataclass(frozen=True, slots=True)
class WorkflowDiscoveryForbidden:
    pass


@dataclass(frozen=True, slots=True)
class WorkflowDiscoveryInvalidRequest:
    reason: str

    def __post_init__(self) -> None:
        require_text(self.reason, "discovery invalid-request reason", maximum_bytes=128)


@dataclass(frozen=True, slots=True)
class WorkflowDiscoveryUnavailable:
    reason: str

    def __post_init__(self) -> None:
        require_text(self.reason, "discovery unavailable reason", maximum_bytes=128)


@dataclass(frozen=True, slots=True)
class WorkflowDiscoveryCompleted:
    report: DiscoveryReport
    proposal: ProposalManifest
    target_projection: AdoptionTargetProjection
    adoption_assessments: tuple[AdoptionAssessment, ...]

    def __post_init__(self) -> None:
        if type(self.report) is not DiscoveryReport or type(self.proposal) is not ProposalManifest:
            raise TypeError("completed discovery requires exact report and proposal values")
        if type(self.target_projection) is not AdoptionTargetProjection:
            raise TypeError("completed discovery requires an exact target projection")
        if type(self.adoption_assessments) is not tuple or any(
            type(item) is not AdoptionAssessment for item in self.adoption_assessments
        ):
            raise TypeError("completed discovery requires exact adoption assessments")
        if self.proposal.inventory_digest != self.report.inventory_digest:
            raise ValueError("proposal must bind the completed discovery report")
        expected_unknown_ids = tuple(unknown.unknown_id for unknown in self.report.unknowns)
        if self.proposal.unknown_ids != expected_unknown_ids:
            raise ValueError("proposal must retain every completed-report unknown")
        expected_assessments = assess_workflow_adoption(
            self.report,
            target_projection=self.target_projection,
        )
        if self.adoption_assessments != expected_assessments:
            raise ValueError("adoption assessments must totally bind the discovery report")


type WorkflowDiscoveryOutcome = (
    WorkflowDiscoveryCompleted
    | WorkflowDiscoveryForbidden
    | WorkflowDiscoveryInvalidRequest
    | WorkflowDiscoveryUnavailable
)
type SnapshotReadOutcome = RepositoryWorkflowSnapshot | WorkflowDiscoveryUnavailable
