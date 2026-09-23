from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.report_payload import (
    JobMeasurementPayload,
    ReportAttemptPayload,
    ReportCounterPayload,
    ReportWorkloadPayload,
)
from ci_coordinator.ci_economics.reports import ReportMeasurement
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .report_factories import measurement_report

type _Path = tuple[str | int, ...]
type _Payload = (
    JobMeasurementPayload | ReportAttemptPayload | ReportWorkloadPayload | ReportCounterPayload
)


def _typed_payload(index: int) -> _Payload:
    payload = JobMeasurementPayload.model_validate(measurement_report().canonical_mapping())
    return (payload, payload.attempt, payload.workload, payload.measurements[0])[index]


@pytest.mark.parametrize("index", range(4))
def test_typed_payload_revalidation_preserves_exact_wire_fields(index: int) -> None:
    payload = _typed_payload(index)
    revalidated = type(payload).model_validate(payload)
    assert revalidated is not payload
    assert revalidated.model_dump(mode="json") == payload.model_dump(mode="json")


@pytest.mark.parametrize("index", range(4))
def test_typed_payload_revalidation_rejects_undeclared_fields(index: int) -> None:
    payload = _typed_payload(index).model_copy(update={"unexpected": None})
    with pytest.raises(ValidationError, match="undeclared fields"):
        type(payload).model_validate(payload)


@pytest.mark.parametrize(
    ("index", "field", "invalid"),
    [
        (0, "provider_job_id", True),
        (1, "installation_id", 0),
        (2, "runner_class_digest", "not-a-digest"),
        (3, "value", -1),
    ],
)
def test_typed_payload_revalidation_rejects_invalid_or_missing_fields(
    index: int, field: str, invalid: object
) -> None:
    payload = _typed_payload(index).model_copy(update={field: invalid})
    with pytest.raises(ValidationError):
        type(payload).model_validate(payload)
    del payload.__dict__[field]
    with pytest.raises(ValidationError):
        type(payload).model_validate(payload)


@pytest.mark.parametrize("index", range(4))
def test_untrusted_field_names_do_not_gain_typed_instance_admission(index: int) -> None:
    payload = _typed_payload(index)
    with pytest.raises(ValidationError):
        type(payload).model_validate(payload.model_dump(by_alias=False))


@pytest.mark.parametrize("mutation", ("invalid_counter", "extra_counter", "extra_nested_field"))
def test_report_revalidation_checks_mutable_nested_values(mutation: str) -> None:
    payload = JobMeasurementPayload.model_validate(measurement_report().canonical_mapping())
    if mutation == "extra_counter":
        payload.measurements.append(payload.measurements[0])
    else:
        update = {"value": -1} if mutation == "invalid_counter" else {"unexpected": None}
        payload.measurements[0] = payload.measurements[0].model_copy(update=update)
    with pytest.raises(ValidationError):
        JobMeasurementPayload.model_validate(payload)


def _parent(payload: dict[str, object], path: _Path) -> tuple[dict[str, object], str]:
    current: object = payload
    for component in path[:-1]:
        if isinstance(component, int):
            assert isinstance(current, list)
            current = current[component]
        else:
            assert isinstance(current, dict)
            current = current[component]
    assert isinstance(current, dict) and isinstance(path[-1], str)
    return current, path[-1]


_ROOT_FIELDS = (
    "schemaVersion",
    "method",
    "attempt",
    "providerJobId",
    "checkRunId",
    "sampleKey",
    "producerDigest",
    "workload",
    "reportedAt",
    "commandExitCode",
    "measurements",
)
_REQUIRED: tuple[_Path, ...] = (
    *((name,) for name in _ROOT_FIELDS),
    *(
        ("attempt", name)
        for name in ("installationId", "repositoryId", "workflowRunId", "runAttempt", "headSha")
    ),
    *(
        ("workload", name)
        for name in ("protectedInputsDigest", "runnerClassDigest", "cacheClassDigest")
    ),
    *(
        ("measurements", 0, name)
        for name in ("counter", "unit", "scope", "value", "unavailableReason")
    ),
)


@pytest.mark.parametrize("path", _REQUIRED)
def test_report_payload_requires_every_declared_field(path: _Path) -> None:
    payload = measurement_report().canonical_mapping()
    parent, key = _parent(payload, path)
    del parent[key]
    with pytest.raises(ValidationError):
        JobMeasurementPayload.model_validate(payload)


@pytest.mark.parametrize("path", [(), ("attempt",), ("workload",), ("measurements", 0)])
def test_report_payload_rejects_extra_keys_at_each_object_boundary(path: _Path) -> None:
    payload = measurement_report().canonical_mapping()
    parent, key = _parent(payload, (*path, "unknown"))
    parent[key] = None
    with pytest.raises(ValidationError):
        JobMeasurementPayload.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schemaVersion",), "ci-economics-job-report/v2"),
        (("method",), "cgroup/v1"),
        (("providerJobId",), True),
        (("checkRunId",), "405"),
        (("sampleKey",), "has whitespace"),
        (("sampleKey",), "a" * 129),
        (("producerDigest",), "A" * 64),
        (("reportedAt",), "2026-02-30T10:00:00.000000Z"),
        (("reportedAt",), "2026-09-04T10:00:00Z"),
        (("reportedAt",), "2026-09-04T10:00:00.000000+00:00"),
        (("commandExitCode",), True),
        (("commandExitCode",), MAX_SAFE_JSON_INTEGER + 1),
        (("commandExitCode",), -MAX_SAFE_JSON_INTEGER - 1),
        (("attempt", "installationId"), 0),
        (("attempt", "repositoryId"), MAX_SAFE_JSON_INTEGER + 1),
        (("attempt", "workflowRunId"), None),
        (("attempt", "runAttempt"), 1.0),
        (("attempt", "headSha"), "b" * 39),
        (("workload", "protectedInputsDigest"), None),
        (("workload", "runnerClassDigest"), "c" * 63),
        (("workload", "cacheClassDigest"), "d" * 65),
        (("measurements", 0, "counter"), "cpu_total"),
        (("measurements", 0, "unit"), "millisecond"),
        (("measurements", 0, "scope"), "reporter_interval"),
        (("measurements", 0, "value"), True),
        (("measurements", 0, "value"), -1),
        (("measurements", 0, "value"), MAX_SAFE_JSON_INTEGER + 1),
        (("measurements", 0, "value"), None),
        (("measurements", 0, "unavailableReason"), "counter_error"),
        (("measurements",), []),
    ],
)
def test_report_payload_rejects_domain_and_representation_mutants(
    path: _Path, value: object
) -> None:
    payload = measurement_report().canonical_mapping()
    parent, key = _parent(payload, path)
    parent[key] = value
    with pytest.raises(ValidationError):
        JobMeasurementPayload.model_validate(payload)


@pytest.mark.parametrize("mode", ("zero", "maximum", "unavailable"))
def test_report_payload_round_trips_valid_counter_extremes(mode: str) -> None:
    report = measurement_report()
    measurements = tuple(
        ReportMeasurement(item.counter, None, "counter_error")
        if mode == "unavailable"
        else ReportMeasurement(item.counter, 0 if mode == "zero" else MAX_SAFE_JSON_INTEGER)
        for item in report.measurements
    )
    report = replace(report, measurements=measurements)
    admitted = JobMeasurementPayload.model_validate(report.canonical_mapping()).to_report()
    assert admitted == report
    assert admitted.report_digest == report.report_digest
    assert admitted.report_id == report.report_id


def test_report_counter_order_is_normalized_but_duplicate_counter_is_rejected() -> None:
    report = measurement_report()
    payload = report.canonical_mapping()
    counters = cast(list[object], payload["measurements"])
    counters.reverse()
    assert JobMeasurementPayload.model_validate(payload).to_report() == report
    counters[0] = counters[1]
    with pytest.raises(ValidationError):
        JobMeasurementPayload.model_validate(payload)


def test_report_json_schema_remains_a_closed_finite_payload() -> None:
    schema = JobMeasurementPayload.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(_ROOT_FIELDS)
    assert schema["properties"]["measurements"]["maxItems"] == 3
    assert schema["properties"]["schemaVersion"]["const"] == "ci-economics-job-report/v1"
