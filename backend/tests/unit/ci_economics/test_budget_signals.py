from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from typing import Literal, cast

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.budget import ReportBudget
from ci_coordinator.ci_economics.budget_commands import (
    BudgetPolicyCommitted,
    BudgetPolicyConflict,
    ConfigureBudgetPolicy,
)
from ci_coordinator.ci_economics.budget_payload import BudgetPolicyPayload
from ci_coordinator.ci_economics.budget_policy import BudgetPolicyConfiguration
from ci_coordinator.ci_economics.budget_signal import BudgetSignal, BudgetSignalPage
from ci_coordinator.ci_economics.reports import CounterUnavailableReason, ReportMeasurement
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .budget_factories import budget_command
from .report_factories import stored_report


@pytest.mark.parametrize(
    "maximum,expected", [(999, "breached"), (1_000, "within_budget"), (1_001, "within_budget")]
)
@pytest.mark.parametrize("exit_code", [0, 1, -15])
def test_signal_binds_threshold_boundary_and_preserves_command_failure(
    maximum: int, expected: str, exit_code: int
) -> None:
    record = stored_report()
    record = replace(record, report=replace(record.report, command_exit_code=exit_code))
    policy = budget_command().next_policy
    policy = replace(
        policy,
        configuration=replace(policy.configuration, budget=ReportBudget("cpu_user", maximum)),
    )
    signal = BudgetSignal.evaluate(policy, record)
    assert signal.outcome == expected
    assert signal.measurement.value_us == 1_000
    assert signal.command_exit_code == exit_code
    assert signal.retain_until == record.retain_until
    assert (
        signal.report_id == record.report.report_id
        and signal.report_digest == record.report.report_digest
    )
    assert signal.canonical_mapping()["policyDigest"] == policy.policy_digest
    assert signal.canonical_mapping()["policy"] == policy.canonical_mapping()


@pytest.mark.parametrize(
    "reason", ["unsupported_platform", "counter_error", "out_of_range", "incomplete_scope"]
)
def test_missing_counter_is_evidence_not_zero(reason: CounterUnavailableReason) -> None:
    record = stored_report()
    record = replace(
        record,
        report=replace(
            record.report,
            measurements=tuple(
                ReportMeasurement("cpu_user", None, reason) if item.counter == "cpu_user" else item
                for item in record.report.measurements
            ),
        ),
    )
    signal = BudgetSignal.evaluate(budget_command().next_policy, record)
    assert signal.outcome == "insufficient_evidence"
    assert signal.measurement.value_us is None and signal.measurement.unavailable_reason == reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_revision", -1),
        ("expected_revision", True),
        ("expected_revision", MAX_SAFE_JSON_INTEGER),
        ("actor", ""),
        ("actor", "a" * 513),
        ("actor", "\x00"),
        ("actor", "\ud800"),
        ("operation_id", "bad/key"),
        ("policy_key", ""),
        ("scope", {}),
        ("configuration", {}),
    ],
)
def test_command_rejects_invalid_authority_operands(field: str, value: object) -> None:
    command = budget_command()
    operands = {name: getattr(command, name) for name in command.__dataclass_fields__}
    operands[field] = value
    with pytest.raises((ValueError, TypeError)):
        ConfigureBudgetPolicy(
            scope=cast(RepositoryScope, operands["scope"]),
            actor=cast(str, operands["actor"]),
            operation_id=cast(str, operands["operation_id"]),
            policy_key=cast(str, operands["policy_key"]),
            expected_revision=cast(int, operands["expected_revision"]),
            configuration=cast(BudgetPolicyConfiguration, operands["configuration"]),
        )


def test_operation_digest_binds_actor_revision_identity_and_complete_configuration() -> None:
    command = budget_command()
    variants = (
        command,
        replace(command, actor="other"),
        replace(command, operation_id="other"),
        replace(command, expected_revision=1),
        replace(command, policy_key="other"),
        replace(command, configuration=replace(command.configuration, enabled=False)),
    )
    assert len({value.command_digest for value in variants}) == len(variants)
    assert command.audit_key != replace(command, operation_id="other").audit_key
    assert (
        replace(command, expected_revision=MAX_SAFE_JSON_INTEGER - 1).next_policy.revision
        == MAX_SAFE_JSON_INTEGER
    )
    assert BudgetPolicyCommitted(command.next_policy, True).replayed
    with pytest.raises(TypeError):
        BudgetPolicyCommitted(command.next_policy, cast(bool, 1))
    with pytest.raises(ValueError):
        BudgetPolicyConflict(cast(Literal["revision_conflict"], "unknown"))


@pytest.mark.parametrize("path", [(), ("configuration",), ("configuration", "selector")])
@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_policy_payload_is_closed_at_every_boundary(path: tuple[str, ...], mutation: str) -> None:
    payload = budget_command().next_policy.canonical_mapping()
    target = payload
    for key in path:
        target = cast(dict[str, object], target[key])
    for key in tuple(target) if mutation == "missing" else ("unexpected",):
        candidate = deepcopy(payload)
        nested = candidate
        for step in path:
            nested = cast(dict[str, object], nested[step])
        if mutation == "missing":
            del nested[key]
        else:
            nested[key] = None
        with pytest.raises(ValidationError):
            BudgetPolicyPayload.model_validate(candidate)


@pytest.mark.parametrize("revision", [True, 1.0, "1", 0, MAX_SAFE_JSON_INTEGER + 1])
def test_payload_rejects_revision_coercion(revision: object) -> None:
    payload = budget_command().next_policy.canonical_mapping()
    payload["revision"] = revision
    with pytest.raises(ValidationError):
        BudgetPolicyPayload.model_validate(payload)


def test_payload_roundtrip_and_forged_instance_are_revalidated() -> None:
    policy = budget_command().next_policy
    payload = BudgetPolicyPayload.model_validate(policy.canonical_mapping())
    assert payload.to_policy() == policy
    assert BudgetPolicyPayload.model_validate(payload).to_policy() == policy
    with pytest.raises(ValidationError):
        BudgetPolicyPayload.model_validate(payload.model_copy(update={"unexpected": "value"}))
    with pytest.raises(ValidationError):
        BudgetPolicyPayload.model_validate(payload.model_copy(update={"revision": True}))


def test_signal_revision_is_historical_and_cursor_cannot_skip_last_row() -> None:
    command = budget_command()
    record = stored_report()
    first = BudgetSignal.evaluate(command.next_policy, record)
    second = BudgetSignal.evaluate(replace(command.next_policy, revision=2), record)
    assert first.signal_id != second.signal_id
    assert first.policy.revision == 1
    items = tuple(sorted((first, second), key=lambda item: item.signal_id))
    assert BudgetSignalPage(items, items[-1].signal_id).items == items
    for invalid, cursor in (
        (items[::-1], None),
        ((first, first), None),
        ((), first.signal_id),
        (items, items[0].signal_id),
    ):
        with pytest.raises(ValueError):
            BudgetSignalPage(invalid, cursor)
    with pytest.raises(ValueError):
        BudgetSignal.evaluate(
            replace(
                command.next_policy, configuration=replace(command.configuration, enabled=False)
            ),
            record,
        )
    with pytest.raises(ValueError):
        replace(first, retain_until=first.received_at)
    with pytest.raises(ValueError):
        replace(first, received_at=first.received_at.replace(tzinfo=None))
    assert (
        replace(first, received_at=first.received_at + timedelta(microseconds=1)).signal_id
        == first.signal_id
    )
