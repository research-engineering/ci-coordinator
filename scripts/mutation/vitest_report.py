"""Bounded machine evidence for Vitest-backed mutation witnesses."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Final, Literal, NotRequired, TypedDict, cast

from scripts.repository_paths import read_repository_regular_file

VITEST_REPORT_RELATIVE_PATH: Final = Path(".mutation-witness/vitest-report.json")
_MAXIMUM_REPORT_BYTES: Final = 8 * 1024 * 1024
_MAXIMUM_TESTS: Final = 100_000
_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "coverageMap",
        "numFailedTests",
        "numFailedTestSuites",
        "numPassedTests",
        "numPassedTestSuites",
        "numPendingTests",
        "numPendingTestSuites",
        "numTodoTests",
        "numTotalTests",
        "numTotalTestSuites",
        "snapshot",
        "startTime",
        "success",
        "testResults",
    }
)
_REQUIRED_TOP_LEVEL_KEYS: Final = _TOP_LEVEL_KEYS - {"coverageMap"}
_TEST_RESULT_KEYS: Final = frozenset(
    {"assertionResults", "endTime", "message", "name", "startTime", "status"}
)
_ASSERTION_REQUIRED_KEYS: Final = frozenset(
    {
        "ancestorTitles",
        "benchmarks",
        "failureMessages",
        "fullName",
        "meta",
        "status",
        "tags",
        "title",
    }
)
_ASSERTION_KEYS: Final = _ASSERTION_REQUIRED_KEYS | {"duration", "location"}
_ASSERTION_STATUSES: Final = frozenset(
    {"disabled", "failed", "passed", "pending", "skipped", "todo"}
)


class VitestEvidence(TypedDict):
    state: Literal["failed_tests", "invalid", "passed_tests"]
    reason: NotRequired[str]
    reportDigest: NotRequired[str]
    totalTests: NotRequired[int]
    failedTests: NotRequired[int]
    executedIdentityDigest: NotRequired[str]
    skippedIdentityDigest: NotRequired[str]


def is_vitest_command(command: list[str]) -> bool:
    return bool(command) and Path(command[0]).name.removesuffix(".exe") == "vitest"


def prepare_vitest_command(command: list[str], worktree: Path) -> list[str]:
    if not is_vitest_command(command):
        return command
    if any(
        argument == "--reporter"
        or argument == "--outputFile"
        or argument.startswith(("--reporter=", "--outputFile="))
        for argument in command[1:]
    ):
        raise RuntimeError("Vitest mutation command cannot override machine-report authority")
    report_path = worktree / VITEST_REPORT_RELATIVE_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.parent.is_symlink():
        raise RuntimeError("Vitest mutation report directory must not be a symlink")
    if report_path.is_dir():
        raise RuntimeError("Vitest mutation report path is not a file")
    report_path.unlink(missing_ok=True)
    return [
        *command,
        "--reporter=json",
        f"--outputFile={report_path}",
    ]


def read_vitest_evidence(worktree: Path) -> VitestEvidence:
    try:
        content = read_repository_regular_file(
            worktree,
            VITEST_REPORT_RELATIVE_PATH,
            "Vitest mutation report",
            maximum_bytes=_MAXIMUM_REPORT_BYTES,
        )
    except (OSError, ValueError):
        return {"state": "invalid", "reason": "report_missing_or_unreadable"}
    digest = hashlib.sha256(content).hexdigest()
    try:
        value: object = json.loads(content, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError):
        return {"state": "invalid", "reason": "report_invalid", "reportDigest": digest}
    evidence = _admit_report(value, worktree)
    evidence["reportDigest"] = digest
    return evidence


def _admit_report(value: object, worktree: Path) -> VitestEvidence:
    if type(value) is not dict:
        return _invalid("report_invalid")
    report = cast("dict[str, object]", value)
    keys = set(report)
    if not _REQUIRED_TOP_LEVEL_KEYS <= keys <= _TOP_LEVEL_KEYS:
        return _invalid("report_invalid")
    counter_names = (
        "numFailedTests",
        "numFailedTestSuites",
        "numPassedTests",
        "numPassedTestSuites",
        "numPendingTests",
        "numPendingTestSuites",
        "numTodoTests",
        "numTotalTests",
        "numTotalTestSuites",
    )
    counters: dict[str, int] = {}
    for name in counter_names:
        counter = report[name]
        if type(counter) is not int or not 0 <= counter <= _MAXIMUM_TESTS:
            return _invalid("report_invalid")
        counters[name] = counter
    if (
        type(report["success"]) is not bool
        or not _is_finite_number(report["startTime"])
        or type(report["testResults"]) is not list
        or len(cast("list[object]", report["testResults"])) > _MAXIMUM_TESTS
        or type(report["snapshot"]) is not dict
        or (
            "coverageMap" in report
            and report["coverageMap"] is not None
            and type(report["coverageMap"]) is not dict
        )
        or counters["numTotalTests"]
        != counters["numPassedTests"]
        + counters["numFailedTests"]
        + counters["numPendingTests"]
        + counters["numTodoTests"]
        or counters["numTotalTestSuites"]
        != counters["numPassedTestSuites"]
        + counters["numFailedTestSuites"]
        + counters["numPendingTestSuites"]
    ):
        return _invalid("report_inconsistent")

    assertion_counts = {status: 0 for status in _ASSERTION_STATUSES}
    failed_assertions_have_messages = True
    failed_result_without_assertion = False
    assertion_total = 0
    files: set[str] = set()
    occurrences: dict[tuple[str, tuple[str, ...], str], int] = {}
    executed: list[tuple[str, tuple[str, ...], str, int]] = []
    skipped: list[tuple[tuple[str, tuple[str, ...], str, int], str]] = []
    for raw_result in cast("list[object]", report["testResults"]):
        if type(raw_result) is not dict or set(raw_result) != _TEST_RESULT_KEYS:
            return _invalid("report_invalid")
        result = cast("dict[str, object]", raw_result)
        if (
            result["status"] not in {"failed", "passed"}
            or type(result["assertionResults"]) is not list
            or type(result["message"]) is not str
            or type(result["name"]) is not str
            or not _is_finite_number(result["startTime"])
            or not _is_finite_number(result["endTime"])
            or cast("int | float", result["endTime"]) < cast("int | float", result["startTime"])
        ):
            return _invalid("report_invalid")
        file = _relative_file(result["name"], worktree)
        if file is None or file in files:
            return _invalid("report_identity_invalid")
        files.add(file)
        result_status = result["status"]
        result_failed_assertions = 0
        for raw_assertion in cast("list[object]", result["assertionResults"]):
            if type(raw_assertion) is not dict:
                return _invalid("report_invalid")
            assertion = cast("dict[str, object]", raw_assertion)
            assertion_keys = set(assertion)
            if not _ASSERTION_REQUIRED_KEYS <= assertion_keys <= _ASSERTION_KEYS:
                return _invalid("report_invalid")
            status = assertion["status"]
            failure_messages = assertion["failureMessages"]
            if (
                status not in _ASSERTION_STATUSES
                or type(failure_messages) is not list
                or any(type(message) is not str for message in failure_messages)
                or not _is_string_list(assertion["ancestorTitles"])
                or type(assertion["fullName"]) is not str
                # Vitest 5 emits this for every test; benchmark evidence is out of scope.
                or assertion["benchmarks"] != []
                or type(assertion["meta"]) is not dict
                or not _is_string_list(assertion["tags"])
                or type(assertion["title"]) is not str
                or not _is_optional_duration(assertion.get("duration"))
                or not _is_optional_location(assertion.get("location"))
            ):
                return _invalid("report_invalid")
            title = assertion["title"]
            ancestors = tuple(cast("list[str]", assertion["ancestorTitles"]))
            if not title or not assertion["fullName"] or any(not part for part in ancestors):
                return _invalid("report_identity_invalid")
            test_name = (file, ancestors, title)
            occurrence = occurrences.get(test_name, 0)
            occurrences[test_name] = occurrence + 1
            identity = (*test_name, occurrence)
            admitted_status = cast(
                "Literal['disabled', 'failed', 'passed', 'pending', 'skipped', 'todo']",
                status,
            )
            assertion_counts[admitted_status] += 1
            assertion_total += 1
            if admitted_status in {"passed", "failed"}:
                executed.append(identity)
            else:
                skipped.append((identity, admitted_status))
            if admitted_status == "failed":
                result_failed_assertions += 1
                if not failure_messages:
                    failed_assertions_have_messages = False
        if result_status == "passed" and result_failed_assertions:
            return _invalid("report_inconsistent")
        if result_status == "failed" and not result_failed_assertions:
            failed_result_without_assertion = True

    pending_assertions = (
        assertion_counts["disabled"] + assertion_counts["pending"] + assertion_counts["skipped"]
    )
    if (
        assertion_total != counters["numTotalTests"]
        or assertion_counts["failed"] != counters["numFailedTests"]
        or assertion_counts["passed"] != counters["numPassedTests"]
        or assertion_counts["todo"] != counters["numTodoTests"]
        or pending_assertions != counters["numPendingTests"]
    ):
        return _invalid("report_inconsistent")
    if failed_result_without_assertion:
        return _invalid("report_did_not_prove_test_outcome")

    facts: VitestEvidence = {
        "state": "invalid",
        "totalTests": counters["numTotalTests"],
        "failedTests": counters["numFailedTests"],
        "executedIdentityDigest": _identity_digest(sorted(executed)),
        "skippedIdentityDigest": _identity_digest(sorted(skipped)),
    }
    if (
        report["success"] is True
        and counters["numTotalTests"] > 0
        and counters["numPassedTests"] > 0
        and counters["numPassedTestSuites"] > 0
        and counters["numFailedTests"] == 0
        and counters["numFailedTestSuites"] == 0
    ):
        facts["state"] = "passed_tests"
        return facts
    if (
        report["success"] is False
        and counters["numTotalTests"] > 0
        and counters["numFailedTests"] > 0
        and counters["numFailedTestSuites"] > 0
        and failed_assertions_have_messages
    ):
        facts["state"] = "failed_tests"
        return facts
    facts["reason"] = "report_did_not_prove_test_outcome"
    return facts


def _invalid(reason: str) -> VitestEvidence:
    return {"state": "invalid", "reason": reason}


def _relative_file(value: str, worktree: Path) -> str | None:
    if not value or any(character in value for character in "\\\0\r\n"):
        return None
    path = Path(value)
    if ".." in path.parts:
        return None
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(worktree.resolve())
        except (OSError, ValueError):
            return None
    elif path.as_posix() != value:
        return None
    relative = path.as_posix()
    if relative == ".":
        return None
    return relative


def _identity_digest(identities: object) -> str:
    encoded = json.dumps(identities, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")


def _is_finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(cast("int | float", value))


def _is_string_list(value: object) -> bool:
    return type(value) is list and all(type(item) is str for item in value)


def _is_optional_duration(value: object) -> bool:
    return value is None or (_is_finite_number(value) and cast("int | float", value) >= 0)


def _is_optional_location(value: object) -> bool:
    if value is None:
        return True
    if type(value) is not dict or set(value) != {"column", "line"}:
        return False
    location = cast("dict[str, object]", value)
    line = location["line"]
    column = location["column"]
    return type(line) is int and line > 0 and type(column) is int and column > 0
