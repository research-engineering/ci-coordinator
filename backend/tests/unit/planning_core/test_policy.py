from __future__ import annotations

from dataclasses import replace

import pytest

from ci_coordinator.planning_core import PlanningPolicy, planner_admission_reasons
from ci_coordinator.validation_contract import ValidationCatalog

from .conftest import MakeInput, MakePolicy


def test_policy_uses_closed_catalog_as_its_validation_authority(
    make_policy: MakePolicy,
) -> None:
    policy = make_policy()

    assert policy.catalog_hash == policy.catalog.catalog_hash
    assert tuple(item.obligation_id for item in policy.catalog.obligations) == (
        "backend-tests",
        "docs-lint",
        "required-baseline",
    )


def test_policy_rejects_an_unsafe_responsibility_pattern(
    make_policy: MakePolicy,
) -> None:
    policy = make_policy()
    first = replace(policy.catalog.obligations[0], responsibility_paths=("../unsafe/**",))
    catalog = ValidationCatalog(
        obligations=(first, *policy.catalog.obligations[1:]),
        witnesses=policy.catalog.witnesses,
        execution_profiles=policy.catalog.execution_profiles,
    )

    with pytest.raises(ValueError, match="responsibility path"):
        PlanningPolicy(
            config_epoch_id=policy.config_epoch_id,
            compiled_policy_hash=policy.compiled_policy_hash,
            policy_hash=policy.policy_hash,
            catalog=catalog,
            fallback_timeout_seconds=policy.fallback_timeout_seconds,
        )


def test_admission_fails_closed_only_on_planning_authority_inputs(
    make_input: MakeInput,
    make_policy: MakePolicy,
) -> None:
    input = make_input()

    assert planner_admission_reasons(input, make_policy()) == ()
    assert planner_admission_reasons(input, make_policy(policy_hash="f" * 64)) == (
        "planning_policy_hash_mismatch",
    )
