from __future__ import annotations

from dataclasses import replace
from importlib import import_module

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_comparison import (
    GovernanceStateComparison,
    compare_governance_states,
)
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceRepository,
    GovernanceState,
)
from ci_coordinator.kernel import canonical_json

SCOPE = RepositoryScope(7, 11)


def test_identical_canonical_state_matches_without_delta() -> None:
    state = _state()

    result = compare_governance_states(state, state)

    assert result.relation == "matches"
    assert result.baseline_state_digest == result.current_state_digest
    assert result.changed_coordinates == ()
    assert (result.added_rule_count, result.removed_rule_count) == (0, 0)


@pytest.mark.parametrize(
    ("case", "coordinates", "rule_delta"),
    [
        ("api-version", ("api_version",), (0, 0)),
        ("owner-id", ("repository.owner_id",), (0, 0)),
        (
            "owner",
            ("repository.owner", "repository.full_name", "rules"),
            (1, 1),
        ),
        (
            "name",
            ("repository.name", "repository.full_name", "rules"),
            (1, 1),
        ),
        ("default-branch", ("repository.default_branch",), (0, 0)),
        ("rule", ("rules",), (1, 1)),
    ],
)
def test_changed_coordinates_are_closed_and_canonical(
    case: str,
    coordinates: tuple[str, ...],
    rule_delta: tuple[int, int],
) -> None:
    result = compare_governance_states(_state(), _mutated_state(case))

    assert result.relation == "differs"
    assert result.changed_coordinates == coordinates
    assert (result.added_rule_count, result.removed_rule_count) == rule_delta


def test_exact_bytes_not_digest_equality_decide_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = import_module("ci_coordinator.governance_comparison.comparison")
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(module, "sha256_hex", lambda _: "a" * 64)

    result = compare_governance_states(_state(), _state(default_branch="main"))

    assert result.relation == "differs"
    assert result.baseline_state_digest == result.current_state_digest == "a" * 64


def test_additive_provider_fields_remain_exact_rule_differences() -> None:
    baseline = _state(additive_mode="observe")
    current = _state(additive_mode="enforce")

    result = compare_governance_states(baseline, current)

    assert result.relation == "differs"
    assert result.changed_coordinates == ("rules",)
    assert (result.added_rule_count, result.removed_rule_count) == (1, 1)


def test_cross_scope_is_invalid_instead_of_governance_difference() -> None:
    current = _state()
    repository = replace(current.repository, scope=RepositoryScope(7, 12))

    with pytest.raises(ValueError, match="cross repository scope"):
        compare_governance_states(_state(), replace(current, repository=repository))


def test_differs_relation_requires_a_nonempty_exact_delta() -> None:
    with pytest.raises(ValueError, match="relation contradicts"):
        GovernanceStateComparison(
            relation="differs",
            baseline_state_digest="a" * 64,
            current_state_digest="b" * 64,
            changed_coordinates=(),
            added_rule_count=0,
            removed_rule_count=0,
        )


def _mutated_state(case: str) -> GovernanceState:
    if case == "api-version":
        return _state(api_version="2026-04-01")
    if case == "owner-id":
        return _state(owner_id=102)
    if case == "owner":
        return _state(owner="new-owner")
    if case == "name":
        return _state(name="new-name")
    if case == "default-branch":
        return _state(default_branch="main")
    if case == "rule":
        return _state(rule_type="pull_request")
    raise AssertionError(case)


def _state(
    *,
    additive_mode: str | None = None,
    api_version: str = "2026-03-10",
    default_branch: str = "master",
    name: str = "repository",
    owner: str = "example",
    owner_id: int = 101,
    rule_type: str = "required_status_checks",
) -> GovernanceState:
    full_name = f"{owner}/{name}"
    value = {
        "parameters": {"required_status_checks": ["CI"]},
        "ruleset_id": 41,
        "ruleset_source": full_name,
        "ruleset_source_type": "Repository",
        "type": rule_type,
    }
    if additive_mode is not None:
        value["future"] = {"mode": additive_mode}
    rule = EffectiveGovernanceRule(
        rule_type=rule_type,
        ruleset_source_type="Repository",
        ruleset_source=full_name,
        ruleset_id=41,
        canonical_json=canonical_json(value),
    )
    return GovernanceState(
        repository=GovernanceRepository(
            scope=SCOPE,
            owner_id=owner_id,
            owner=owner,
            name=name,
            full_name=full_name,
            default_branch=default_branch,
        ),
        api_version=api_version,
        rules=(rule,),
    )
