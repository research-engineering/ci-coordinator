from datetime import datetime

from ci_coordinator.app import DynamicPlanCommand
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.plan_issuance import PlanRequest


def dynamic_plan_command(at: datetime) -> DynamicPlanCommand:
    request = PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "push",
        "refs/heads/main",
        "a" * 40,
        "b" * 40,
        7001,
        1,
        execution_sha="c" * 40,
    )
    identity = TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/heads/main",
        7001,
        1,
        "push",
        "workflow@ref",
        None,
        None,
        None,
        None,
        at,
        execution_sha=request.execution_sha,
    )
    return DynamicPlanCommand(request, identity)
