from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from ci_coordinator.audit_replay import (
    AuditEventError,
    AuditEventInput,
    build_audit_event,
)
from ci_coordinator.audit_replay.event import JsonValue

CORPUS_PATH = Path("fixtures/conformance/v1/audit-json-resource-corpus.v1.json")


def audit_resource_domain_cases(repo_root: Path) -> dict[str, object]:
    corpus = cast(
        dict[str, Any],
        json.loads((repo_root / CORPUS_PATH).read_text(encoding="utf8")),
    )
    cases = cast(list[dict[str, Any]], corpus["cases"])
    return {
        "schemaVersion": corpus["schemaVersion"],
        "profileId": corpus["profileId"],
        "profileDigest": corpus["profileDigest"],
        "limits": corpus["limits"],
        "caseCount": len(cases),
        "cases": {
            case["id"]: _capture_resource_case(
                cast(str, case["id"]),
                cast(dict[str, Any], case["recipe"]),
            )
            for case in cases
        },
    }


def _capture_resource_case(case_id: str, recipe: dict[str, Any]) -> dict[str, object]:
    try:
        record = build_audit_event(
            AuditEventInput(
                idempotency_key=f"audit-resource-{case_id}",
                subject_type="dynamic-ci-plan",
                subject_id="scalar-domain",
                event_type="scalar-domain.checked",
                created_at="2026-07-09T00:00:00.000Z",
                actor="ci-coordinator",
                payload=_build_resource_value(recipe),
            ),
            None,
        )
        return {"accepted": True, "payloadHash": record.payload_hash}
    except AuditEventError as error:
        if error.resource_failure is not None:
            return {
                "accepted": False,
                "resourceFailure": error.resource_failure.to_mapping(),
            }
        return {"accepted": False, "unexpectedError": type(error).__name__}


def _build_resource_value(recipe: dict[str, Any]) -> JsonValue:
    kind = cast(str, recipe["kind"])
    if kind == "null":
        return None
    if kind == "empty-array":
        return []
    if kind == "empty-object":
        return {}
    if kind == "nested-array":
        return _nested_resource_value(cast(int, recipe["depth"]), mixed=False)
    if kind == "mixed-depth":
        return _nested_resource_value(cast(int, recipe["depth"]), mixed=True)
    if kind == "wide-array":
        return [None] * cast(int, recipe["itemCount"])
    if kind == "wide-array-under-key":
        key = cast(str, recipe["key"])
        return {key: [None] * cast(int, recipe["itemCount"])}
    if kind == "wide-array-invalid-tail":
        item_count = cast(int, recipe["itemCount"])
        value: list[JsonValue] = [None] * (item_count - 1)
        value.append(float("nan"))
        return value
    if kind == "deep-tail-after-wide-prefix":
        return [None] * cast(int, recipe["prefixCount"]) + [
            _nested_resource_value(cast(int, recipe["depth"]), mixed=False)
        ]
    if kind == "wide-object":
        member_count = cast(int, recipe["memberCount"])
        entries = [(f"k{index:05d}", None) for index in range(member_count)]
        if recipe["order"] == "descending":
            entries.reverse()
        return dict(entries)
    if kind == "repeated-alias":
        shared: list[JsonValue] = [None] * cast(int, recipe["itemCount"])
        return [shared, shared]
    raise ValueError(f"unsupported audit resource recipe {kind}")


def _nested_resource_value(depth: int, *, mixed: bool) -> JsonValue:
    value: JsonValue = None
    for index in range(depth):
        value = {"value": value} if mixed and index % 2 == 1 else [value]
    return value
