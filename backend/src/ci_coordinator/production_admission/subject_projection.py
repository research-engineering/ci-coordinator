"""Project runtime-owned values into the exact production authority subject."""

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.production_admission.model import (
    ProductionCandidateSubject,
    ProductionPlanSubject,
)
from ci_coordinator.verification_core import VerifiedPlan


def project_candidate_subject(
    scope: RepositoryScope,
    verified_plan: VerifiedPlan,
    identity: TrustedActionsRun,
) -> ProductionCandidateSubject:
    if type(scope) is not RepositoryScope:
        raise TypeError("production subject projection requires an exact scope")
    if type(verified_plan) is not VerifiedPlan:
        raise TypeError("production subject projection requires an exact verified plan")
    if type(identity) is not TrustedActionsRun:
        raise TypeError("production subject projection requires an exact trusted identity")
    source = verified_plan.source_plan
    return ProductionCandidateSubject(
        scope=scope,
        config_epoch_id=source.config_epoch_id,
        compiled_policy_hash=source.compiled_policy_hash,
        policy_hash=source.policy_hash,
        catalog_hash=source.catalog_hash,
        execution_plan_id=verified_plan.execution_plan_id,
        workflow_ref=identity.workflow_ref,
        job_workflow_ref=identity.job_workflow_ref,
        workflow_sha=identity.workflow_sha,
        job_workflow_sha=identity.job_workflow_sha,
    )


def project_plan_subject(
    scope: RepositoryScope,
    verified_plan: VerifiedPlan,
    identity: TrustedActionsRun,
    target_registry_hash: str,
    reconciliation_subject_id: str,
) -> ProductionPlanSubject:
    return ProductionPlanSubject(
        candidate=project_candidate_subject(scope, verified_plan, identity),
        target_registry_hash=target_registry_hash,
        reconciliation_subject_id=reconciliation_subject_id,
    )
