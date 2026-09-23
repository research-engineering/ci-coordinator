import json
from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError

from ci_coordinator.ci_economics.observation_payload import (
    ObservationConfigurationPayload,
    ObservationPositiveId,
    ObservationSelectorPayload,
    ObservationSnapshotPayload,
)
from ci_coordinator.kernel import canonical_json
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .observation_factories import observation


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("value", [True, False, "101", 101.0, None, 0, MAX_SAFE_JSON_INTEGER + 1])
def test_positive_id_is_strict_without_an_enclosing_model(value: object, json_mode: bool) -> None:
    adapter: TypeAdapter[int] = TypeAdapter(ObservationPositiveId)
    with pytest.raises(ValidationError):
        if json_mode:
            adapter.validate_json(json.dumps(value))
        else:
            adapter.validate_python(value)


@pytest.mark.parametrize("value", [1, MAX_SAFE_JSON_INTEGER])
def test_positive_id_admits_exact_numeric_boundaries(value: int) -> None:
    adapter: TypeAdapter[int] = TypeAdapter(ObservationPositiveId)
    assert adapter.validate_python(value) == adapter.validate_json(str(value)) == value


@pytest.mark.parametrize("json_mode", [False, True])
def test_snapshot_wire_modes_preserve_exact_owner_identity(json_mode: bool) -> None:
    snapshot = observation()
    raw = snapshot.canonical_mapping()
    parsed = (
        ObservationSnapshotPayload.model_validate_json(canonical_json(raw))
        if json_mode
        else ObservationSnapshotPayload.model_validate(raw)
    )
    assert parsed.to_snapshot() == snapshot
    assert parsed.model_dump() == raw


@pytest.mark.parametrize(
    "key",
    [
        "schemaVersion",
        "installationId",
        "repositoryId",
        "revision",
        "configuration",
        "configuredAt",
    ],
)
@pytest.mark.parametrize("change", ["missing", "null"])
def test_snapshot_requires_every_non_nullable_operand(key: str, change: str) -> None:
    raw = observation().canonical_mapping()
    if change == "missing":
        del raw[key]
    else:
        raw[key] = None
    with pytest.raises(ValidationError):
        ObservationSnapshotPayload.model_validate(raw)


@pytest.mark.parametrize(
    "key,value",
    [
        ("schemaVersion", "ci-economics-observation/v2"),
        ("installationId", True),
        ("repositoryId", "202"),
        ("revision", 0),
        ("configuredAt", "2026-09-09T12:00:00"),
        ("configuredAt", "2026-09-31T12:00:00Z"),
        ("configuredAt", "2026-09-09 12:00:00Z"),
        ("extra", 1),
    ],
)
def test_snapshot_rejects_invalid_values_not_just_unknown_keys(key: str, value: object) -> None:
    raw = observation().canonical_mapping()
    raw[key] = value
    with pytest.raises(ValidationError):
        ObservationSnapshotPayload.model_validate(raw)


@pytest.mark.parametrize(
    "selector",
    [
        {},
        {"kind": "all"},
        {"workflowIds": None},
        {"kind": "all", "workflowIds": []},
        {"kind": "all", "workflowIds": [1]},
        {"kind": "selected", "workflowIds": None},
        {"kind": "selected", "workflowIds": []},
        {"kind": "selected", "workflowIds": [1, 1]},
        {"kind": "selected", "workflowIds": [True]},
        {"kind": "selected", "workflowIds": [0]},
        {"kind": "selected", "workflowIds": list(range(1, 34))},
        {"kind": "all", "workflowIds": None, "unknown": 1},
    ],
)
def test_selector_relation_and_collection_domain_are_admitted(selector: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ObservationSelectorPayload.model_validate(selector)


@pytest.mark.parametrize("ids", [None, [20, 10], list(range(32, 0, -1))])
@pytest.mark.parametrize("days", [0, 6])
def test_configuration_normalizes_only_the_owner_admitted_workflow_order(
    ids: list[int] | None, days: int
) -> None:
    raw = {
        "enabled": True,
        "selector": {"kind": "all" if ids is None else "selected", "workflowIds": ids},
        "backfillDays": days,
    }
    original = deepcopy(raw)
    configuration = ObservationConfigurationPayload.model_validate(raw).to_configuration()
    assert raw == original
    assert configuration.workflow_ids == (None if ids is None else tuple(sorted(ids)))
    assert (
        ObservationConfigurationPayload.model_validate(
            configuration.canonical_mapping()
        ).to_configuration()
        == configuration
    )


def test_revalidation_rejects_constructed_and_mutated_nested_instances() -> None:
    constructed = ObservationSelectorPayload.model_construct(kind="all", workflow_ids=[1])
    with pytest.raises(ValidationError):
        ObservationSelectorPayload.model_validate(constructed)
    parsed = ObservationSnapshotPayload.model_validate(observation().canonical_mapping())
    assert parsed.configuration.selector.workflow_ids is not None
    parsed.configuration.selector.workflow_ids.append(10)
    with pytest.raises(ValidationError):
        ObservationSnapshotPayload.model_validate(parsed)
