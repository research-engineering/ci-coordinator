from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CORPUS_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures/conformance/v1/audit-json-resource-corpus.v1.json"
)
_PROFILE_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/specs/ci-coordinator-core/audit-json-resource-profile.v1.json"
)
_CORPUS_SCHEMA = "ci-audit-json-resource-corpus/v1"
_PROFILE_SCHEMA = "ci-audit-json-resource-profile/v1"
_PROFILE_ID = "ci-audit-event-json-resources/v1"
_PROFILE_DIGEST = "211e5a32e49adcf48c3101dd116265e76cba6ddc7b3e0a314693801cc09d40a7"
_CORPUS_DIGEST = "1f48bb5c4d6e84665d1dd68f4014fcb8b326f48037c57833f5498b74599cab0d"


def validate_audit_resource_oracles(
    expected: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    profile = _load_json(_PROFILE_PATH)
    canonical_profile = _canonical_json_bytes(profile)
    if hashlib.sha256(canonical_profile).hexdigest() != _PROFILE_DIGEST:
        raise ValueError("audit resource profile has an unadmitted fingerprint")
    if profile.get("schemaVersion") != _PROFILE_SCHEMA:
        raise ValueError("audit resource profile has an unsupported schema")
    if profile.get("profileId") != _PROFILE_ID:
        raise ValueError("audit resource profile has an unsupported id")
    limits = profile.get("limits")
    if not isinstance(limits, dict):
        raise TypeError("audit resource profile has no limits")

    corpus = _load_json(_CORPUS_PATH)
    canonical_corpus = _canonical_json_bytes(corpus)
    if hashlib.sha256(canonical_corpus).hexdigest() != _CORPUS_DIGEST:
        raise ValueError("audit resource corpus has an unadmitted fingerprint")
    if corpus.get("schemaVersion") != _CORPUS_SCHEMA:
        raise ValueError("audit resource corpus has an unsupported schema")
    if corpus.get("profileId") != _PROFILE_ID:
        raise ValueError("audit resource corpus has an unsupported profile id")
    if corpus.get("profileDigest") != _PROFILE_DIGEST:
        raise ValueError("audit resource corpus has an unsupported profile digest")
    if corpus.get("limits") != limits:
        raise ValueError("audit resource corpus has unadmitted limits")

    raw_cases = corpus.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("audit resource corpus cases must be a non-empty array")
    expected_cases: dict[str, dict[str, Any]] = {}
    for case in raw_cases:
        if not isinstance(case, dict):
            raise TypeError("audit resource corpus case must be an object")
        case_id = _require_str(case, "id")
        if case_id in expected_cases:
            raise ValueError(f"audit resource corpus contains duplicate case {case_id}")
        case_expectation = case.get("expected")
        if not isinstance(case_expectation, dict):
            raise TypeError(f"audit resource corpus case {case_id} has no expectation")
        expected_cases[case_id] = case_expectation

    for label, bundle in (("expected", expected), ("candidate", candidate)):
        projection = _get_path(bundle, "cases.auditReplay.resourceAdmissionV1")
        _validate_projection(label, projection, expected_cases, limits)


def _validate_projection(
    label: str,
    projection: Any,
    expected_cases: dict[str, dict[str, Any]],
    limits: dict[str, Any],
) -> None:
    if not isinstance(projection, dict):
        raise TypeError(f"{label} audit resource projection must be an object")
    if projection.get("schemaVersion") != _CORPUS_SCHEMA:
        raise ValueError(f"{label} audit resource projection has an invalid schema")
    if projection.get("profileId") != _PROFILE_ID:
        raise ValueError(f"{label} audit resource projection has an invalid profile id")
    if projection.get("profileDigest") != _PROFILE_DIGEST:
        raise ValueError(f"{label} audit resource projection has an invalid profile digest")
    if projection.get("limits") != limits:
        raise ValueError(f"{label} audit resource projection has invalid limits")
    if projection.get("caseCount") != len(expected_cases):
        raise ValueError(f"{label} audit resource projection has invalid case count")
    actual_cases = projection.get("cases")
    if not isinstance(actual_cases, dict) or set(actual_cases) != set(expected_cases):
        raise ValueError(f"{label} audit resource projection has invalid case ids")
    for case_id, expectation in expected_cases.items():
        _validate_case(label, case_id, expectation, actual_cases[case_id])


def _validate_case(
    label: str,
    case_id: str,
    expectation: dict[str, Any],
    actual: Any,
) -> None:
    if not isinstance(actual, dict):
        raise TypeError(f"{label} audit resource case {case_id} must be an object")
    accepted = expectation.get("accepted")
    if type(accepted) is not bool or actual.get("accepted") is not accepted:
        raise ValueError(f"{label} audit resource case {case_id} has wrong admission")
    if accepted:
        if set(actual) != {"accepted", "payloadHash"}:
            raise ValueError(f"{label} audit resource case {case_id} has invalid shape")
        payload_hash = actual.get("payloadHash")
        if payload_hash != expectation.get("payloadHash"):
            raise ValueError(f"{label} audit resource case {case_id} has wrong payloadHash")
        return

    if set(actual) != {"accepted", "resourceFailure"}:
        raise ValueError(f"{label} audit resource case {case_id} has invalid shape")
    expected_failure = expectation.get("resourceFailure")
    actual_failure = actual.get("resourceFailure")
    if not isinstance(expected_failure, dict) or not isinstance(actual_failure, dict):
        raise TypeError(f"{label} audit resource case {case_id} has no resource failure")
    if set(actual_failure) != {"code", "instancePointer", "limit", "observed"}:
        raise ValueError(f"{label} audit resource case {case_id} has invalid failure shape")
    for field in ("code", "instancePointer", "limit", "observed"):
        if actual_failure.get(field) != expected_failure.get(field):
            raise ValueError(f"{label} audit resource case {case_id} has wrong {field}")


def _get_path(value: Any, path: str) -> Any:
    current = value
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise ValueError(f"missing required path {path}")
        current = current[segment]
    return current


def _require_str(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf8")
