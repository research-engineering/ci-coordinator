from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from ci_coordinator.ci_economics.budget import ReportBudget
from ci_coordinator.ci_economics.budget_policy import (
    BudgetPolicyConfiguration,
    BudgetPolicySnapshot,
    BudgetSelector,
)
from ci_coordinator.ci_economics.reports import REPORT_METHOD, JobMeasurementReport
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .report_factories import measurement_report


def _policy() -> BudgetPolicySnapshot:
    report = measurement_report()
    return BudgetPolicySnapshot(
        report.attempt.scope,
        "backend-cpu",
        1,
        BudgetPolicyConfiguration(
            True,
            BudgetSelector(report.sample_key, report.producer_digest, "c" * 64),
            ReportBudget("cpu_user", 1_000),
        ),
    )


@pytest.mark.parametrize("runner", [None, "c" * 64])
@pytest.mark.parametrize("exit_code", [0, 1, -15])
def test_exact_and_explicit_wildcard_selectors_keep_failed_commands(
    runner: str | None, exit_code: int
) -> None:
    policy = _policy()
    selector = replace(policy.configuration.selector, runner_class_digest=runner)
    policy = replace(policy, configuration=replace(policy.configuration, selector=selector))
    assert policy.matches(replace(measurement_report(), command_exit_code=exit_code))
    assert selector.canonical_mapping()["method"] == REPORT_METHOD
    assert selector.canonical_mapping()["runnerClassDigest"] == runner


@pytest.mark.parametrize(
    "operand", ["installation", "repository", "enabled", "sample", "producer", "runner"]
)
def test_each_independent_matching_operand_can_reject(operand: str) -> None:
    policy = _policy()
    report = measurement_report()
    if operand == "installation":
        policy = replace(
            policy,
            scope=RepositoryScope(policy.scope.installation_id + 1, policy.scope.repository_id),
        )
    elif operand == "repository":
        policy = replace(
            policy,
            scope=RepositoryScope(policy.scope.installation_id, policy.scope.repository_id + 1),
        )
    elif operand == "enabled":
        policy = replace(policy, configuration=replace(policy.configuration, enabled=False))
    else:
        field = {
            "sample": "sample_key",
            "producer": "producer_digest",
            "runner": "runner_class_digest",
        }[operand]
        value = "another-sample" if operand == "sample" else "e" * 64
        selector = replace(policy.configuration.selector, **{field: value})
        policy = replace(policy, configuration=replace(policy.configuration, selector=selector))
    assert not policy.matches(report)


def test_wildcard_does_not_require_equal_cache_or_workload_declarations() -> None:
    report = measurement_report()
    selector = BudgetSelector(report.sample_key, report.producer_digest, None)
    changed = replace(
        report,
        workload=replace(
            report.workload,
            protected_inputs_digest="e" * 64,
            runner_class_digest="f" * 64,
            cache_class_digest="a" * 64,
        ),
    )
    assert selector.matches(changed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sample_key", ""),
        ("sample_key", "a" * 129),
        ("sample_key", "contains space"),
        ("sample_key", "non-ascii-\u00e9"),
        ("sample_key", None),
        ("producer_digest", "A" * 64),
        ("producer_digest", "a" * 63),
        ("producer_digest", None),
        ("runner_class_digest", ""),
        ("runner_class_digest", "A" * 64),
        ("runner_class_digest", True),
    ],
)
def test_invalid_selector_operand_does_not_become_a_wildcard(field: str, value: object) -> None:
    selector = _policy().configuration.selector
    with pytest.raises(ValueError):
        BudgetSelector(
            cast(str, value) if field == "sample_key" else selector.sample_key,
            cast(str, value) if field == "producer_digest" else selector.producer_digest,
            cast(str | None, value)
            if field == "runner_class_digest"
            else selector.runner_class_digest,
        )


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1", MAX_SAFE_JSON_INTEGER + 1])
def test_policy_revision_rejects_nonpositive_or_inexact_values(value: object) -> None:
    with pytest.raises(ValueError):
        replace(_policy(), revision=cast(int, value))


@pytest.mark.parametrize("revision", [1, MAX_SAFE_JSON_INTEGER])
def test_policy_revision_accepts_its_exact_domain_boundaries(revision: int) -> None:
    assert replace(_policy(), revision=revision).revision == revision


@pytest.mark.parametrize("field,value", [("enabled", 1), ("selector", {}), ("budget", {})])
def test_configuration_requires_exact_domain_values(field: str, value: object) -> None:
    configuration = _policy().configuration
    with pytest.raises(TypeError):
        BudgetPolicyConfiguration(
            cast(bool, value) if field == "enabled" else configuration.enabled,
            cast(BudgetSelector, value) if field == "selector" else configuration.selector,
            cast(ReportBudget, value) if field == "budget" else configuration.budget,
        )


@pytest.mark.parametrize("field,value", [("scope", {}), ("configuration", {})])
def test_snapshot_requires_exact_domain_values(field: str, value: object) -> None:
    policy = _policy()
    with pytest.raises(TypeError):
        BudgetPolicySnapshot(
            cast(RepositoryScope, value) if field == "scope" else policy.scope,
            policy.policy_key,
            policy.revision,
            cast(BudgetPolicyConfiguration, value)
            if field == "configuration"
            else policy.configuration,
        )


@pytest.mark.parametrize("key", ["", "a" * 129, "contains space", "non-ascii-\u00e9"])
def test_invalid_policy_key_is_rejected(key: str) -> None:
    with pytest.raises(ValueError):
        replace(_policy(), policy_key=key)


def test_maximum_identifier_and_zero_threshold_are_valid() -> None:
    policy = _policy()
    selector = replace(policy.configuration.selector, sample_key="a" * 128)
    configuration = replace(
        policy.configuration, selector=selector, budget=ReportBudget("cpu_user", 0)
    )
    assert (
        replace(
            policy, policy_key="a" * 128, configuration=configuration
        ).configuration.budget.maximum_us
        == 0
    )


def test_digest_binds_every_semantic_policy_operand() -> None:
    policy = _policy()
    configuration = policy.configuration
    alternatives = [
        replace(
            policy,
            scope=RepositoryScope(policy.scope.installation_id + 1, policy.scope.repository_id),
        ),
        replace(
            policy,
            scope=RepositoryScope(policy.scope.installation_id, policy.scope.repository_id + 1),
        ),
        replace(policy, policy_key="another-policy"),
        replace(policy, revision=2),
        replace(policy, configuration=replace(configuration, enabled=False)),
        replace(
            policy, configuration=replace(configuration, budget=ReportBudget("elapsed", 1_000))
        ),
        replace(policy, configuration=replace(configuration, budget=ReportBudget("cpu_user", 999))),
    ]
    alternatives.extend(
        replace(policy, configuration=replace(configuration, selector=selector))
        for selector in (
            replace(configuration.selector, sample_key="other"),
            replace(configuration.selector, producer_digest="b" * 64),
            replace(configuration.selector, runner_class_digest=None),
        )
    )
    assert len({policy.policy_digest, *(item.policy_digest for item in alternatives)}) == 11
    assert replace(policy).policy_digest == policy.policy_digest


@pytest.mark.parametrize("value", [None, {}, "report"])
def test_matching_rejects_non_domain_reports(value: object) -> None:
    policy = _policy()
    report = cast(JobMeasurementReport, value)
    with pytest.raises(TypeError):
        policy.matches(report)
    with pytest.raises(TypeError):
        policy.configuration.selector.matches(report)
