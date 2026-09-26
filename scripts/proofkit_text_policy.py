from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from time import monotonic

from scripts.proofkit_cli import invoke_proofkit, resolve_proofkit_executable
from scripts.proofkit_common import JsonObject, js_json_dumps, parse_json_object
from scripts.proofkit_inputs import (
    TEXT_POLICY_MAX_INPUT_BYTES,
    TEXT_POLICY_REPORT_ID,
    TextPolicyBatch,
    TextPolicyEntry,
    capture_text_policy_inventory,
    plan_text_policy_batches,
    text_policy_document,
)


def text_policy_report(
    repo_root: Path,
    *,
    proofkit_executable: str | Path | None = None,
    maximum_input_bytes: int = TEXT_POLICY_MAX_INPUT_BYTES,
) -> JsonObject:
    deadline = monotonic() + 120.0

    def remaining() -> float:
        seconds = deadline - monotonic()
        if seconds <= 0:
            raise RuntimeError("text-policy shared deadline exhausted")
        return seconds

    executable = resolve_proofkit_executable(proofkit_executable)
    remaining()
    inventory = capture_text_policy_inventory(repo_root, remaining)
    files = tuple(entry for entry in inventory if entry.exclusion is None)
    batches = plan_text_policy_batches(files, remaining, maximum_bytes=maximum_input_bytes)
    _admit_partition(files, batches)
    manifest = [entry.manifest() for entry in inventory]
    inventory_digest = hashlib.sha256(js_json_dumps(manifest).encode("utf-8")).hexdigest()
    reports: list[JsonObject] = []
    binary_count = 0
    for batch in batches:
        remaining()
        selected = files[batch.start : batch.stop]
        document = text_policy_document([entry.row() for entry in selected], batch.report_id)
        payload = js_json_dumps(document)
        encoded = payload.encode("utf-8")
        if len(encoded) != batch.input_bytes or len(encoded) > maximum_input_bytes:
            raise ValueError(f"text-policy envelope size mismatch: {batch.report_id}")
        input_digest = hashlib.sha256(encoded).hexdigest()
        del encoded
        expected = _expected_passed_report(document)
        timeout_seconds = remaining()
        try:
            result = invoke_proofkit(
                executable,
                "text-policy",
                ("--input", "-"),
                cwd=repo_root,
                input_text=payload,
                timeout_seconds=timeout_seconds,
            )
            remaining()
        except RuntimeError as error:
            raise RuntimeError(
                f"Proofkit text-policy failed ({batch.report_id}): {error}"
            ) from error
        del payload, document
        if result.returncode != 0:
            diagnostic = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
            raise RuntimeError(f"Proofkit text-policy failed ({batch.report_id}): {diagnostic}")
        report = parse_json_object(result.stdout, f"text-policy {batch.report_id}")
        # Serialized equality distinguishes booleans and floats from exact integers.
        if json.dumps(report, sort_keys=True, allow_nan=False) != json.dumps(
            expected, sort_keys=True, allow_nan=False
        ):
            raise ValueError(
                f"text-policy report differs from exact pinned output: {batch.report_id}"
            )
        binary_count += expected["summary"]["binarySkippedFileCount"]
        reports.append(
            {
                "command": "text-policy",
                "state": "passed",
                "reportId": batch.report_id,
                "inputSha256": input_digest,
                "inputBytes": batch.input_bytes,
                "start": batch.start,
                "stop": batch.stop,
                "summary": report["summary"],
            }
        )
    if len(reports) != len(batches) or sum(
        report["summary"]["inputFileCount"] for report in reports
    ) != len(files):
        raise ValueError("text-policy reports do not cover the complete inventory")
    if capture_text_policy_inventory(repo_root, remaining) != inventory:
        raise ValueError("text-policy source inventory changed during admission")
    result_report: JsonObject = {
        "schemaVersion": 1,
        "reportId": "ci-coordinator.proofkit-text-policy",
        "reportKind": "ci-coordinator.proofkit-text-policy",
        "state": "passed",
        "summary": {
            "admissionCount": len(reports),
            "commands": ["text-policy"] * len(reports),
            "inventorySha256": inventory_digest,
            "inventoryFileCount": len(inventory),
            "inputFileCount": len(files),
            "checkedTextFileCount": len(files) - binary_count,
            "binarySkippedFileCount": binary_count,
            "missingSkippedFileCount": 0,
            "failureCount": 0,
            "excludedFileCount": len(inventory) - len(files),
            "excludedFiles": [entry for entry in manifest if entry["exclusion"] is not None],
        },
        "reports": reports,
        "nonClaims": [
            "This wrapper admits caller-owned Proofkit inputs and does not execute "
            "native witnesses.",
            "A passing report does not prove provider execution, merge safety, "
            "or deployment readiness.",
        ],
    }
    remaining()
    return result_report


def _admit_partition(files: Sequence[TextPolicyEntry], batches: Sequence[TextPolicyBatch]) -> None:
    if not batches:
        raise ValueError("text-policy partition must include a native invocation")
    paths = [entry.path for entry in files]
    if paths != sorted(set(paths)):
        raise ValueError("text-policy partition needs globally sorted unique paths")
    cursor = 0
    for index, batch in enumerate(batches, 1):
        expected_id = (
            TEXT_POLICY_REPORT_ID if len(batches) == 1 else f"{TEXT_POLICY_REPORT_ID}.batch-{index}"
        )
        if (
            batch.start != cursor
            or batch.stop < cursor
            or batch.stop > len(files)
            or (batch.stop == cursor and (files or len(batches) != 1))
            or batch.report_id != expected_id
        ):
            raise ValueError("text-policy partition has a hole, overlap or wrong identity")
        cursor = batch.stop
    if cursor != len(files):
        raise ValueError("text-policy partition omits inventory rows")


def _expected_passed_report(document: JsonObject) -> JsonObject:
    policy = document["policy"]
    files = document["files"]
    binary_count = sum(_go_extension(row["path"]) in policy["binarySuffixes"] for row in files)
    return {
        "schemaVersion": 1,
        "reportKind": "proofkit.text-policy",
        "reportId": document["reportId"],
        "state": "passed",
        "summary": {
            "admittedPolicy": policy,
            "binarySkippedFileCount": binary_count,
            "checkedTextFileCount": len(files) - binary_count,
            "failureCount": 0,
            "inputFileCount": len(files),
            "missingSkippedFileCount": 0,
        },
        "diagnostics": [{"key": "failures", "value": []}],
        "ruleResults": [
            {
                "ruleId": "proofkit.text-policy.admitted-policy",
                "status": "passed",
                "message": "Caller-provided text files satisfy the admitted text policy.",
                "diagnostics": [{"key": "failureCount", "value": 0}],
            }
        ],
        "nonClaims": sorted(
            [
                "Text policy checks caller-provided file inventory only.",
                "Text policy does not discover git state, read repository files, "
                "own repository-specific documentation topology, decide proof freshness, "
                "approve merge, release, or rollout.",
                *document["nonClaims"],
            ]
        ),
    }


def _go_extension(path: str) -> str:
    # Go filepath.Ext includes the last dot even for '.png'; this only counts rows.
    name = path.rsplit("/", 1)[-1]
    index = name.rfind(".")
    # For the declared ASCII suffixes, Go's simple I-dot mapping differs from Python's.
    return name[index:].replace("\u0130", "i").lower() if index >= 0 else ""
