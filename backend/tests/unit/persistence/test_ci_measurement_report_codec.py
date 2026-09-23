from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.factories import NOW
from ci_economics.report_factories import measurement_report

from ci_coordinator.ci_economics.reports import MeasurementReportOrigin, StoredMeasurementReport
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.ci_measurement_report_codec import (
    decode_measurement_report,
    encode_measurement_report,
)


def _record() -> StoredMeasurementReport:
    report = measurement_report()
    return StoredMeasurementReport(
        report,
        ProviderRunCollectionSource(report.attempt, NOW, "2026-03-10", "f" * 64),
        MeasurementReportOrigin("a" * 64, "b" * 64),
        NOW + timedelta(seconds=1),
        NOW + timedelta(days=90),
    )


def test_report_row_round_trip_preserves_payload_origin_and_lifetime() -> None:
    record = _record()
    row = encode_measurement_report(record)
    assert row["payload_canonical"] == canonical_json(record.report.canonical_mapping())
    assert decode_measurement_report(row, record.source) == record
    payload = row["payload_canonical"]
    assert isinstance(payload, bytes)
    assert (
        decode_measurement_report({**row, "payload_canonical": memoryview(payload)}, record.source)
        == record
    )


@pytest.mark.parametrize(
    "field",
    (
        "report_id",
        "subject_id",
        "source_kind",
        "report_digest",
        "producer_claim_hash",
        "provider_binding_digest",
        "payload_canonical",
        "received_at",
        "retain_until",
    ),
)
def test_report_codec_rejects_missing_row_operand(field: str) -> None:
    record = _record()
    row = encode_measurement_report(record)
    del row[field]
    with pytest.raises(ValueError):
        decode_measurement_report(row, record.source)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("report_id", "c" * 64),
        ("subject_id", "c" * 64),
        ("source_kind", "reconciliation"),
        ("report_digest", "c" * 64),
        ("producer_claim_hash", "A" * 64),
        ("provider_binding_digest", "b" * 63),
        ("received_at", NOW - timedelta(seconds=1)),
        ("received_at", NOW.replace(tzinfo=None)),
        ("retain_until", NOW),
        ("payload_canonical", b"{}"),
        ("payload_canonical", b' {"schemaVersion":"ci-economics-job-report/v1"}'),
        ("payload_canonical", b'{"method":"waited_children/v1","method":"waited_children/v1"}'),
        ("payload_canonical", b"[" * 2000 + b"0" + b"]" * 2000),
    ],
)
def test_report_codec_rejects_wrong_identity_lifetime_or_raw_payload(
    field: str, value: object
) -> None:
    record = _record()
    row = encode_measurement_report(record)
    with pytest.raises(ValueError):
        decode_measurement_report({**row, field: value}, record.source)


def test_report_codec_rejects_a_valid_payload_with_changed_semantic_digest() -> None:
    record = _record()
    changed = replace(record.report, command_exit_code=1)
    row = encode_measurement_report(record)
    row["payload_canonical"] = canonical_json(changed.canonical_mapping())
    with pytest.raises(ValueError, match="canonical identity"):
        decode_measurement_report(row, record.source)


def test_report_codec_rejects_noncanonical_counter_order_without_changing_wire_admission() -> None:
    record = _record()
    payload = record.report.canonical_mapping()
    counters = payload["measurements"]
    assert isinstance(counters, list)
    counters.reverse()
    row = encode_measurement_report(record)
    row["payload_canonical"] = canonical_json(payload)
    with pytest.raises(ValueError, match="canonical identity"):
        decode_measurement_report(row, record.source)
