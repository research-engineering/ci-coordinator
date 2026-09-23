from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceRepository,
    GovernanceState,
)
from ci_coordinator.kernel import canonical_json


def governance_state(
    scope: RepositoryScope, *, rule_type: str = "required_status_checks"
) -> GovernanceState:
    value = {
        "parameters": {},
        "ruleset_id": 41,
        "ruleset_source": "example/repository",
        "ruleset_source_type": "Repository",
        "type": rule_type,
    }
    rule = EffectiveGovernanceRule(
        rule_type=rule_type,
        ruleset_source_type="Repository",
        ruleset_source="example/repository",
        ruleset_id=41,
        canonical_json=canonical_json(value),
    )
    return GovernanceState(
        GovernanceRepository(
            scope,
            owner_id=101,
            owner="example",
            name="repository",
            full_name="example/repository",
            default_branch="master",
        ),
        "2026-03-10",
        (rule,),
    )
