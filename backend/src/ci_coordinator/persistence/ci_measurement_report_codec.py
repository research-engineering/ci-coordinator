from __future__ import annotations

from datetime import datetime

from ci_coordinator.ci_economics.report_payload import JobMeasurementPayload
from ci_coordinator.ci_economics.reports import (
    MAX_REPORT_BYTES,
    MeasurementReportOrigin,
    StoredMeasurementReport,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.kernel import canonical_json, load_strict_json
from ci_coordinator.kernel.canonical_json import JsonResourceLimits
from ci_coordinator.persistence.canonical_row import (
    encode_canonical_object,
    require_bytes,
    require_digest,
)


def encode_measurement_report(record: StoredMeasurementReport) -> dict[str, object]:
    if type(record) is not StoredMeasurementReport:
        raise TypeError("report row requires an exact retained record")
    return {
        "report_id": record.report.report_id,
        "subject_id": record.source.source_id,
        "source_kind": "provider_run",
        "report_digest": record.report.report_digest,
        "producer_claim_hash": record.origin.producer_claim_hash,
        "provider_binding_digest": record.origin.provider_binding_digest,
        "payload_canonical": encode_canonical_object(
            record.report.canonical_mapping(),
            maximum_bytes=MAX_REPORT_BYTES,
            context="CI measurement report",
        ),
        "received_at": record.received_at,
        "retain_until": record.retain_until,
    }


def decode_measurement_report(
    row: dict[str, object], source: ProviderRunCollectionSource
) -> StoredMeasurementReport:
    if type(source) is not ProviderRunCollectionSource:
        raise TypeError("report row decoding requires an exact provider source")
    raw = require_bytes(row.get("payload_canonical"), "CI measurement report")
    payload = load_strict_json(
        raw,
        max_bytes=MAX_REPORT_BYTES,
        resource_limits=JsonResourceLimits(max_depth=4, max_nodes=128),
    )
    report = JobMeasurementPayload.model_validate(payload).to_report()
    if (
        row.get("source_kind") != "provider_run"
        or row.get("subject_id") != source.source_id
        or row.get("report_id") != report.report_id
        or row.get("report_digest") != report.report_digest
        or canonical_json(report.canonical_mapping()) != raw
    ):
        raise ValueError("stored report differs from its canonical identity or payload")
    received_at, retain_until = row.get("received_at"), row.get("retain_until")
    if type(received_at) is not datetime or type(retain_until) is not datetime:
        raise ValueError("stored report receipt or retention instant is invalid")
    return StoredMeasurementReport(
        report,
        source,
        MeasurementReportOrigin(
            require_digest(row.get("producer_claim_hash"), "report producer claim"),
            require_digest(row.get("provider_binding_digest"), "report provider binding"),
        ),
        received_at,
        retain_until,
    )
