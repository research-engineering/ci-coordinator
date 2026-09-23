from datetime import UTC, datetime

import pytest
from governance_state_support import governance_state
from planning_command_support import dynamic_plan_command

from ci_coordinator.config_control import RepositoryScope


@pytest.mark.parametrize(
    "at",
    (datetime(2026, 7, 15, tzinfo=UTC), datetime(2026, 9, 13, 12, 34, tzinfo=UTC)),
)
def test_planning_inputs_are_fresh_and_bind_the_caller_time(at: datetime) -> None:
    left, right = dynamic_plan_command(at), dynamic_plan_command(at)
    assert left.identity.verified_at == at
    assert left == right
    assert left is not right
    assert left.request is not right.request
    assert left.identity is not right.identity


@pytest.mark.parametrize("scope", (RepositoryScope(7, 11), RepositoryScope(13, 17)))
def test_governance_inputs_are_fresh_and_keep_rule_type_independent(scope: RepositoryScope) -> None:
    left, right = governance_state(scope), governance_state(scope)
    assert left.repository.scope == scope
    assert left == right
    assert left is not right
    assert left.repository is not right.repository
    assert left != governance_state(scope, rule_type="pull_request")
