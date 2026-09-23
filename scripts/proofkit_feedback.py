from __future__ import annotations

import re
import stat
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.proofkit_common import JsonObject, as_object

REQUIRED_CATEGORIES = frozenset(
    {
        "adoption_friction",
        "consumer_integration_defect",
        "missing_primitive",
        "schema_discovery_gap",
        "unclear_prompt",
    }
)
ADMITTED_STATUSES = frozenset({"open", "partially_resolved", "resolved", "retained"})

_RECORD_ID = re.compile(r"APF-CI-[0-9]{3}\Z")
_OBSERVATION_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


@dataclass(frozen=True, slots=True)
class FeedbackSummary:
    record_count: int


def validate_proofkit_feedback_ledger(
    ledger: Mapping[str, object],
    expected_dependency: str,
    *,
    repo_root: Path,
    tracked_regular_paths: Set[str],
) -> FeedbackSummary:
    _exact_keys(
        ledger,
        ("evaluatedDependency", "ledgerId", "records", "schemaVersion"),
        "Proofkit feedback ledger",
    )
    if type(ledger.get("schemaVersion")) is not int or ledger["schemaVersion"] != 1:
        raise ValueError("Proofkit feedback ledger schema version is unsupported")
    if ledger.get("ledgerId") != "ci-coordinator.agentic-proofkit-adoption-feedback":
        raise ValueError("Proofkit feedback ledger identity is invalid")
    if ledger.get("evaluatedDependency") != expected_dependency:
        raise ValueError("Proofkit feedback ledger dependency does not match the pinned package")
    raw_records = ledger.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("Proofkit feedback ledger records must be a non-empty array")

    records = [
        _validate_record(
            as_object(record, "Proofkit feedback record"),
            repo_root=repo_root,
            tracked_regular_paths=tracked_regular_paths,
        )
        for record in raw_records
    ]
    ids = [_string_field(record, "id") for record in records]
    if len(set(ids)) != len(ids):
        raise ValueError("Proofkit feedback record ids must be unique")
    if ids != sorted(ids):
        raise ValueError("Proofkit feedback records must be canonically ordered by id")
    observed_categories = {_string_field(record, "category") for record in records}
    missing_categories = sorted(REQUIRED_CATEGORIES - observed_categories)
    if missing_categories:
        raise ValueError(
            "Proofkit feedback ledger is missing required categories: "
            + ", ".join(missing_categories)
        )
    return FeedbackSummary(record_count=len(records))


def _validate_record(
    record: JsonObject,
    *,
    repo_root: Path,
    tracked_regular_paths: Set[str],
) -> JsonObject:
    _exact_keys(
        record,
        (
            "category",
            "closureOracle",
            "evidenceRefs",
            "id",
            "observedOn",
            "status",
            "summary",
        ),
        "Proofkit feedback record",
    )
    record_id = record.get("id")
    if not isinstance(record_id, str) or _RECORD_ID.fullmatch(record_id) is None:
        raise ValueError("Proofkit feedback record id is invalid")
    category = record.get("category")
    if category not in REQUIRED_CATEGORIES:
        visible = category if isinstance(category, str) else "<missing>"
        raise ValueError(f"Proofkit feedback category is unsupported: {visible}")
    status = record.get("status")
    if status not in ADMITTED_STATUSES:
        visible = status if isinstance(status, str) else "<missing>"
        raise ValueError(f"Proofkit feedback status is unsupported: {visible}")
    observed_on = record.get("observedOn")
    if not isinstance(observed_on, str) or _OBSERVATION_DATE.fullmatch(observed_on) is None:
        raise ValueError("Proofkit feedback observation date must use YYYY-MM-DD")
    _nonempty_string(record.get("summary"), "Proofkit feedback summary")
    _nonempty_string(record.get("closureOracle"), "Proofkit feedback closure oracle")
    evidence_refs = _sorted_unique_strings(
        record.get("evidenceRefs"), "Proofkit feedback evidence refs"
    )
    if not evidence_refs:
        raise ValueError("Proofkit feedback evidence refs must be non-empty")
    for evidence_ref in evidence_refs:
        _validate_current_evidence_ref(
            evidence_ref,
            repo_root=repo_root,
            tracked_regular_paths=tracked_regular_paths,
        )
    return record


def _validate_current_evidence_ref(
    evidence_ref: str,
    *,
    repo_root: Path,
    tracked_regular_paths: Set[str],
) -> None:
    relative = PurePosixPath(evidence_ref)
    if (
        relative.is_absolute()
        or str(relative) != evidence_ref
        or "\\" in evidence_ref
        or not relative.parts
        or any(part in {".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"Proofkit feedback evidence ref is not canonical: {evidence_ref}")
    if evidence_ref not in tracked_regular_paths:
        raise ValueError(
            f"Proofkit feedback evidence ref is not tracked as a regular file: {evidence_ref}"
        )
    path = repo_root.joinpath(*relative.parts)
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ValueError(
            f"Proofkit feedback evidence ref is unavailable: {evidence_ref}"
        ) from error
    if not stat.S_ISREG(mode):
        raise ValueError(f"Proofkit feedback evidence ref is not a regular file: {evidence_ref}")


def _exact_keys(value: Mapping[str, object], expected: Sequence[str], context: str) -> None:
    if sorted(value) != sorted(expected):
        raise ValueError(f"{context} fields are invalid")


def _nonempty_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _sorted_unique_strings(value: object, context: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{context} must be an array of strings")
    strings = list(value)
    if any(not item.strip() for item in strings):
        raise ValueError(f"{context} must not contain empty strings")
    if strings != sorted(set(strings)):
        raise ValueError(f"{context} must be unique and canonically ordered")
    return strings


def _string_field(value: Mapping[str, object], field: str) -> str:
    result = value[field]
    if not isinstance(result, str):
        raise TypeError(f"validated field {field} changed type")
    return result
