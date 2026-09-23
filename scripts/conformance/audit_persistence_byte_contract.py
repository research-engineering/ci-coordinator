from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Final, cast

from scripts.bounded_process import spawn

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_PROFILE_PATH: Final = (
    _REPO_ROOT / "docs/specs/ci-coordinator-core/audit-persistence-byte-profile.v1.json"
)
_CORPUS_PATH: Final = _REPO_ROOT / "fixtures/conformance/v1/audit-persistence-byte-corpus.v1.json"
_PROFILE_SCHEMA: Final = "ci-audit-persistence-byte-profile/v1"
_CORPUS_SCHEMA: Final = "ci-audit-persistence-byte-corpus/v1"
_PROFILE_ID: Final = "ci-audit-event-persistence-bytes/v1"
_PROFILE_DIGEST: Final = "b99394dded648e4f0c009a71ca237ad6599b8c6abfa250cd186a9c3b794176fa"
_CORPUS_DIGEST: Final = "73eaf281286ea16c10b187a4d9548efd58540729c2f693ac43d9605bc5e3a4fa"
_FAILURE_FIELDS: Final = {"code", "instancePointer", "limit", "observed"}


def validate_audit_persistence_byte_oracles(
    expected: dict[str, Any],
    candidate: dict[str, Any],
    *,
    profile_path: Path = _PROFILE_PATH,
    corpus_path: Path = _CORPUS_PATH,
) -> None:
    profile = _load_pinned_json(profile_path, _PROFILE_DIGEST, "profile")
    limits = _validate_profile(profile)
    corpus = _load_pinned_json(corpus_path, _CORPUS_DIGEST, "corpus")
    expectations = _validate_corpus(corpus, limits)
    _validate_projection("expected", expected, expectations, limits)
    _validate_projection("candidate", candidate, expectations, limits)
    if expected != candidate:
        raise ValueError("audit persistence byte oracles are not byte-profile equivalent")


def _validate_profile(profile: dict[str, Any]) -> dict[str, int]:
    if profile.get("schemaVersion") != _PROFILE_SCHEMA or profile.get("profileId") != _PROFILE_ID:
        raise ValueError("audit persistence byte profile identity is unsupported")
    limits = profile.get("limits")
    if not isinstance(limits, dict) or limits != {
        "maxVariableTextUtf8Bytes": 4096,
        "maxPayloadCanonicalBytes": 1048576,
    }:
        raise ValueError("audit persistence byte profile limits are unsupported")
    projections = profile.get("fieldProjections")
    if not isinstance(projections, list):
        raise TypeError("audit persistence byte profile projections must be an array")
    observed = {
        (
            projection.get("instancePointer"),
            projection.get("measureRef"),
            projection.get("limitRef"),
            projection.get("nonEmpty"),
        )
        for projection in projections
        if isinstance(projection, dict)
    }
    expected = {
        ("/idempotencyKey", "variableText", "maxVariableTextUtf8Bytes", True),
        ("/subjectId", "variableText", "maxVariableTextUtf8Bytes", True),
        ("/eventType", "variableText", "maxVariableTextUtf8Bytes", True),
        ("/actor", "variableText", "maxVariableTextUtf8Bytes", True),
        ("/payload", "payload", "maxPayloadCanonicalBytes", True),
    }
    if observed != expected or len(projections) != len(expected):
        raise ValueError("audit persistence byte profile projections are unsupported")
    return cast(dict[str, int], limits)


def _validate_corpus(
    corpus: dict[str, Any],
    limits: dict[str, int],
) -> dict[str, dict[str, Any]]:
    if (
        corpus.get("schemaVersion") != _CORPUS_SCHEMA
        or corpus.get("profileId") != _PROFILE_ID
        or corpus.get("profileDigest") != _PROFILE_DIGEST
        or corpus.get("limits") != limits
    ):
        raise ValueError("audit persistence byte corpus authority is unsupported")
    raw_cases = corpus.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("audit persistence byte corpus cases must be a non-empty array")
    expectations: dict[str, dict[str, Any]] = {}
    for raw_case in raw_cases:
        case = _require_mapping(raw_case, "corpus case")
        case_id = _require_str(case, "id")
        if case_id in expectations:
            raise ValueError(f"audit persistence byte corpus duplicates {case_id}")
        expectation = _require_mapping(case.get("expected"), f"case {case_id} expectation")
        _validate_expected_case(case_id, expectation)
        expectations[case_id] = expectation
    return expectations


def _validate_expected_case(case_id: str, expectation: dict[str, Any]) -> None:
    accepted = expectation.get("accepted")
    if type(accepted) is not bool:
        raise ValueError(f"case {case_id} admission must be boolean")
    if accepted:
        allowed = (
            {"accepted", "utf8Bytes"},
            {"accepted", "canonicalByteLength", "canonicalSha256"},
            {"accepted", "payloadHash"},
        )
        if set(expectation) not in allowed:
            raise ValueError(f"case {case_id} accepted expectation has invalid shape")
        return
    if set(expectation) != {"accepted", "resourceFailure"}:
        raise ValueError(f"case {case_id} rejection expectation has invalid shape")
    failure = _require_mapping(expectation.get("resourceFailure"), f"case {case_id} failure")
    if set(failure) != _FAILURE_FIELDS:
        raise ValueError(f"case {case_id} failure has invalid shape")


def _validate_projection(
    label: str,
    projection: dict[str, Any],
    expectations: dict[str, dict[str, Any]],
    limits: dict[str, int],
) -> None:
    required_fields = {
        "schemaVersion",
        "profileId",
        "profileDigest",
        "corpusDigest",
        "limits",
        "caseCount",
        "cases",
    }
    if set(projection) != required_fields:
        raise ValueError(f"{label} audit persistence byte projection has invalid shape")
    if (
        projection.get("schemaVersion") != _CORPUS_SCHEMA
        or projection.get("profileId") != _PROFILE_ID
        or projection.get("profileDigest") != _PROFILE_DIGEST
        or projection.get("corpusDigest") != _CORPUS_DIGEST
        or projection.get("limits") != limits
        or projection.get("caseCount") != len(expectations)
    ):
        raise ValueError(f"{label} audit persistence byte projection has stale authority facts")
    cases = projection.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(expectations):
        raise ValueError(f"{label} audit persistence byte projection has invalid case ids")
    for case_id, expectation in expectations.items():
        if cases.get(case_id) != expectation:
            raise ValueError(f"{label} audit persistence byte case {case_id} violates expectation")


def _load_pinned_json(path: Path, digest: str, label: str) -> dict[str, Any]:
    value = _load_json(path)
    if hashlib.sha256(_canonical_json_bytes(value)).hexdigest() != digest:
        raise ValueError(f"audit persistence byte {label} has an unadmitted fingerprint")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf8"))
    return _require_mapping(value, str(path))


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _require_str(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("expected", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--profile", type=Path, default=_PROFILE_PATH)
    parser.add_argument("--corpus", type=Path, default=_CORPUS_PATH)
    return parser.parse_args()


def _bounded_contention_witness() -> int:
    executable = "backend/.venv/bin/python"
    timeout_seconds = 20
    command = (
        "-m",
        "pytest",
        "backend/tests/integration/persistence/test_byte_limits.py",
        "-q",
        "-k",
        "byte_limit_upgrade_lock_wait_is_bounded",
    )
    result = spawn(
        executable,
        command,
        cwd=_REPO_ROOT,
        max_buffer=16 * 1024 * 1024,
        timeout_seconds=timeout_seconds,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.error is not None:
        print(
            f"bounded contention witness failed: {result.error}",
            file=sys.stderr,
        )
        timeout_error = f"{executable}: timed out after {timeout_seconds:g} seconds"
        return 124 if result.error == timeout_error else 1
    return 1 if result.status is None else result.status


if __name__ == "__main__":
    if sys.argv[1:] == ["--bounded-contention-witness"]:
        raise SystemExit(_bounded_contention_witness())
    arguments = _parse_args()
    validate_audit_persistence_byte_oracles(
        _load_json(arguments.expected),
        _load_json(arguments.candidate),
        profile_path=arguments.profile,
        corpus_path=arguments.corpus,
    )
