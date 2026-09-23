"""Read-only exact-commit workflow discovery and conservative proposals."""

from ci_coordinator.workflow_discovery.adoption import (
    ADOPTION_NON_CLAIMS,
    AdoptionAssessment,
    AdoptionState,
    RecommendedAdapter,
    assess_workflow_adoption,
)
from ci_coordinator.workflow_discovery.adoption_target import (
    AdoptionTargetJob,
    AdoptionTargetProjection,
    AdoptionTargetProjectionStatus,
    AdoptionTargetWorkflow,
)
from ci_coordinator.workflow_discovery.evidence import Fact, Provenance, Unknown, YamlLocation
from ci_coordinator.workflow_discovery.graph_model import CallEdge
from ci_coordinator.workflow_discovery.outcomes import (
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryInvalidRequest,
    WorkflowDiscoveryOutcome,
    WorkflowDiscoveryUnavailable,
)
from ci_coordinator.workflow_discovery.ports import WorkflowDiscoveryUseCase
from ci_coordinator.workflow_discovery.proposal_model import ProposalManifest
from ci_coordinator.workflow_discovery.report import DiscoveryReport
from ci_coordinator.workflow_discovery.service import WorkflowDiscoveryService
from ci_coordinator.workflow_discovery.source import (
    RepositoryIdentity,
    RepositoryWorkflowSnapshot,
    WorkflowSource,
    WorkflowSourceFailure,
    WorkflowSourceIdentity,
)
from ci_coordinator.workflow_discovery.summary import JobSummary, WorkflowSummary

__all__ = [
    "ADOPTION_NON_CLAIMS",
    "AdoptionAssessment",
    "AdoptionState",
    "AdoptionTargetJob",
    "AdoptionTargetProjection",
    "AdoptionTargetProjectionStatus",
    "AdoptionTargetWorkflow",
    "CallEdge",
    "DiscoveryReport",
    "Fact",
    "JobSummary",
    "ProposalManifest",
    "Provenance",
    "RecommendedAdapter",
    "RepositoryIdentity",
    "RepositoryWorkflowSnapshot",
    "Unknown",
    "WorkflowDiscoveryCompleted",
    "WorkflowDiscoveryForbidden",
    "WorkflowDiscoveryInvalidRequest",
    "WorkflowDiscoveryOutcome",
    "WorkflowDiscoveryService",
    "WorkflowDiscoveryUnavailable",
    "WorkflowDiscoveryUseCase",
    "WorkflowSource",
    "WorkflowSourceFailure",
    "WorkflowSourceIdentity",
    "WorkflowSummary",
    "YamlLocation",
    "assess_workflow_adoption",
]
