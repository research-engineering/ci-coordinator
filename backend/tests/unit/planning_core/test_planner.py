from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from ci_coordinator.planning_core import DeterministicPlan, PlanningPolicy, PlanningRejected, plan
from ci_coordinator.repo_context import DiffFileChangeInput

from .conftest import MakeInput, MakePolicy


def test_docs_change_omits_unaffected_obligation_and_binds_complete_proof(
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    policy = make_policy()
    candidate = require_candidate(
        plan(
            make_input(DiffFileChangeInput(path="docs/guide.md", status="modified")),
            policy,
        )
    )

    assert candidate.fallback.triggered is False
    assert [item.obligation_id for item in candidate.selected_obligations] == [
        "docs-lint",
        "required-baseline",
    ]
    assert [item.obligation_id for item in candidate.omitted_obligations] == ["backend-tests"]
    assert [item.witness_id for item in candidate.selected_witnesses] == [
        "baseline-witness",
        "docs-witness",
        "shared-quality",
    ]
    proof = candidate.omitted_obligations[0].proof
    assert proof.policy_hash == policy.policy_hash
    assert proof.catalog_hash == policy.catalog_hash
    assert proof.predicates.no_impact_intersection is True
    assert proof.predicates.no_global_risk_file is True
    assert proof.invalidates_when == (
        "changed path is unknown",
        "dependency graph is stale",
        "diff is truncated",
        "policy changes",
        "validation catalog changes",
    )


def test_shared_witness_depth_is_maximum_of_requiring_obligation_depths(
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    candidate = require_candidate(
        plan(
            make_input(DiffFileChangeInput(path="ci/pipeline.yml", status="modified")),
            make_policy(),
        )
    )

    assert candidate.fallback.triggered is False
    assert [item.depth for item in candidate.selected_obligations] == [
        "targeted",
        "smoke",
        "standard",
    ]
    shared = next(
        item for item in candidate.selected_witnesses if item.witness_id == "shared-quality"
    )
    assert shared.depth == "targeted"
    assert shared.required_by_obligation_ids == ("backend-tests", "docs-lint")


def test_unknown_path_fallback_selects_every_obligation_at_its_full_depth(
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    candidate = require_candidate(
        plan(
            make_input(DiffFileChangeInput(path="src/unknown.py", status="modified")),
            make_policy(),
        )
    )

    assert candidate.fallback.triggered is True
    assert candidate.omitted_obligations == ()
    assert [item.depth for item in candidate.selected_obligations] == [
        "full",
        "standard",
        "full",
    ]
    assert all(item.depth in {"standard", "full"} for item in candidate.selected_witnesses)


@pytest.mark.parametrize(
    ("mismatch", "reason"),
    [
        (
            lambda policy: replace(policy, config_epoch_id="f" * 64),
            "planning_config_epoch_mismatch",
        ),
        (
            lambda policy: replace(policy, compiled_policy_hash="f" * 64),
            "planning_compiled_policy_hash_mismatch",
        ),
        (
            lambda policy: replace(policy, policy_hash="f" * 64),
            "planning_policy_hash_mismatch",
        ),
    ],
)
def test_every_planning_identity_coordinate_rejects_mismatch(
    mismatch: Callable[[PlanningPolicy], PlanningPolicy],
    reason: str,
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    result = plan(
        make_input(DiffFileChangeInput(path="docs/guide.md", status="modified")),
        mismatch(make_policy()),
    )

    assert result == PlanningRejected((reason,))


def test_non_catalog_input_invalidation_runs_full_validation(
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    candidate = require_candidate(
        plan(
            make_input(DiffFileChangeInput(path="docs/guide.md", status="unknown")),
            make_policy(),
        )
    )

    assert candidate.fallback.triggered is True
    assert candidate.fallback.reason == "unknown_file_status"
    assert [item.depth for item in candidate.selected_obligations] == [
        "full",
        "standard",
        "full",
    ]


def test_plan_identity_is_deterministic(
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()

    assert plan(input, policy) == plan(input, policy)


def test_constructor_rejects_missing_required_witness(
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    candidate = require_candidate(
        plan(
            make_input(DiffFileChangeInput(path="docs/guide.md", status="modified")),
            make_policy(),
        )
    )
    forged_witnesses = tuple(
        item for item in candidate.selected_witnesses if item.witness_id != "docs-witness"
    )

    with pytest.raises(ValueError, match="exactly close obligation requirements"):
        replace(candidate, selected_witnesses=forged_witnesses)


def test_constructor_rejects_missing_witness_reverse_edge(
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    candidate = require_candidate(
        plan(
            make_input(DiffFileChangeInput(path="ci/pipeline.yml", status="modified")),
            make_policy(),
        )
    )
    forged_witnesses = tuple(
        replace(item, required_by_obligation_ids=("docs-lint",))
        if item.witness_id == "shared-quality"
        else item
        for item in candidate.selected_witnesses
    )

    with pytest.raises(ValueError, match="reverse edges"):
        replace(candidate, selected_witnesses=forged_witnesses)


def require_candidate(value: DeterministicPlan | PlanningRejected) -> DeterministicPlan:
    if isinstance(value, PlanningRejected):
        pytest.fail("unexpected planning rejection: " + ", ".join(value.reasons))
    return value
