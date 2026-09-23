from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.archive_retention import (
    DetailPolicyReference,
    DetailRetentionMode,
    DetailRetentionPolicy,
)
from ci_coordinator.ci_economics.history_configuration import (
    HistoryConfiguration,
    HistoryDefaults,
    HistoryQuota,
    HistoryUsage,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_economics.archive_factories import ARCHIVE_TIME, history_dataset


def _quota(value: int = 10) -> HistoryQuota:
    return HistoryQuota(attempts=value, jobs=value, gaps=value, canonicalBytes=value)


def _usage(value: int) -> HistoryUsage:
    return HistoryUsage(attempts=value, jobs=value, gaps=value, canonicalBytes=value)


@pytest.mark.parametrize("field", ["attempts", "jobs", "gaps", "canonicalBytes"])
def test_each_capacity_dimension_independently_blocks_reservation(field: str) -> None:
    raw = _usage(0).model_dump()
    raw[field] = 2
    addition = HistoryUsage.model_validate(raw)
    assert _usage(9).reserve(addition, _quota()) is None
    raw[field] = 1
    addition = HistoryUsage.model_validate(raw)
    expected = _usage(9).model_dump()
    expected[field] = 10
    assert _usage(9).reserve(addition, _quota()) == HistoryUsage.model_validate(expected)


def test_exact_replay_reserves_nothing_even_after_a_quota_reduction() -> None:
    prior = _usage(10)
    assert not prior.fits(_quota(1))
    assert prior.reserve(HistoryUsage.empty(), _quota(1)) == prior


def test_reservation_never_overflows_the_shared_integer_domain() -> None:
    prior = _usage(MAX_SAFE_JSON_INTEGER)
    assert prior.reserve(_usage(1), _quota(MAX_SAFE_JSON_INTEGER)) is None
    assert prior.reserve(_usage(0), _quota(MAX_SAFE_JSON_INTEGER)) == prior


@pytest.mark.parametrize("field", ["attempts", "jobs", "gaps", "canonicalBytes"])
def test_release_cannot_hide_underflow_in_any_dimension(field: str) -> None:
    raw = _usage(0).model_dump()
    raw[field] = 2
    with pytest.raises(ValidationError):
        _usage(1).release(HistoryUsage.model_validate(raw))


def test_successful_release_preserves_all_other_resource_operands() -> None:
    prior = HistoryUsage(attempts=5, jobs=50, gaps=2, canonicalBytes=1000)
    detail_cleanup = HistoryUsage(attempts=0, jobs=0, gaps=0, canonicalBytes=800)
    assert prior.release(detail_cleanup) == HistoryUsage(
        attempts=5, jobs=50, gaps=2, canonicalBytes=200
    )
    assert prior.release(HistoryUsage.empty()) == prior


@pytest.mark.parametrize("value", [True, -1, 1.0, "1", MAX_SAFE_JSON_INTEGER + 1])
def test_resource_operands_are_not_coerced(value: object) -> None:
    raw = _usage(1).model_dump()
    raw["jobs"] = value
    with pytest.raises(ValidationError):
        HistoryUsage.model_validate(raw)
    with pytest.raises(ValidationError):
        HistoryQuota.model_validate(raw)


def _configuration(workflows: object = None) -> dict[str, object]:
    return {
        "enabled": True,
        "workflowIds": workflows,
        "detailRetention": {
            "mode": "days",
            "days": 365,
            "anchor": "first_successful_detail_import",
        },
        "quota": _quota().model_dump(),
    }


def test_wire_workflow_array_is_frozen_and_canonicalized_without_mutating_the_caller() -> None:
    ids = [5, 3]
    configuration = HistoryConfiguration.model_validate(_configuration(ids))
    assert ids == [5, 3] and configuration.workflow_ids == (3, 5)
    ids.append(7)
    assert configuration.selects(3) and not configuration.selects(7)
    assert (
        HistoryConfiguration.model_validate_json(configuration.model_dump_json()) == configuration
    )
    assert configuration.detail_retention is not None
    assert configuration.detail_retention.to_policy().days == 365


@pytest.mark.parametrize("workflows", [[], [1, 1], [True], [0], ["1"], list(range(1, 34)), "all"])
def test_invalid_or_ambiguous_workflow_selection_is_rejected(workflows: object) -> None:
    with pytest.raises(ValidationError):
        HistoryConfiguration.model_validate_json(json.dumps(_configuration(workflows)))


@pytest.mark.parametrize("workflow", [True, 0, -1, "1", MAX_SAFE_JSON_INTEGER + 1])
def test_all_workflows_does_not_admit_a_malformed_provider_identity(workflow: object) -> None:
    configuration = HistoryConfiguration.model_validate(_configuration())
    with pytest.raises(ValueError):
        configuration.selects(cast(int, workflow))


def test_zero_quota_is_not_an_implicit_unlimited_or_disabled_mode() -> None:
    with pytest.raises(ValidationError):
        _quota(0)


def test_untrusted_configuration_flags_and_extra_fields_are_rejected() -> None:
    for patch in (
        {"enabled": "true"},
        {"unknown": None},
        {"detailRetention": {"mode": "forever", "days": 365}},
    ):
        with pytest.raises(ValidationError):
            HistoryConfiguration.model_validate({**_configuration(), **patch})


@pytest.mark.parametrize("mode", ["disabled", "days", "forever"])
@pytest.mark.parametrize("inherit", [False, True])
def test_effective_policy_preserves_exact_source_and_independent_revision(
    mode: DetailRetentionMode, inherit: bool
) -> None:
    raw = _configuration()
    raw["detailRetention"] = None if inherit else raw["detailRetention"]
    dataset = replace(
        history_dataset(),
        configuration_revision=4,
        configuration=HistoryConfiguration.model_validate(raw),
    )
    policy = DetailRetentionPolicy(mode, 20 if mode == "days" else None)
    defaults = HistoryDefaults(100, policy, ARCHIVE_TIME)
    effective, reference = dataset.resolve_detail_policy(defaults)
    if inherit:
        assert effective == policy
        assert reference == DetailPolicyReference("service_default", 100)
    else:
        assert effective == DetailRetentionPolicy.default()
        assert reference == DetailPolicyReference("repository_override", 4)
    successor = replace(defaults, revision=101, detail_retention=DetailRetentionPolicy("disabled"))
    after = dataset.resolve_detail_policy(successor)
    assert (after == (effective, reference)) is (not inherit)


def test_inheritance_requires_an_explicit_null_not_an_absent_or_empty_policy() -> None:
    raw = _configuration()
    raw.pop("detailRetention")
    with pytest.raises(ValidationError):
        HistoryConfiguration.model_validate(raw)
    with pytest.raises(ValidationError):
        HistoryConfiguration.model_validate({**raw, "detailRetention": {}})
