from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from scripts.mutation.mutation_suite_runner import (
    ExecutionClassificationInput,
    classify_mutation_execution,
)
from scripts.mutation.vitest_report import (
    VITEST_REPORT_RELATIVE_PATH,
    prepare_vitest_command,
    read_vitest_evidence,
)


def test_vitest_report_distinguishes_assertion_failure_from_collection_failure(
    tmp_path: Path,
) -> None:
    failed = _report(success=False, assertion_status="failed")
    _write_report(tmp_path, failed)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "failed_tests"
    assert evidence["totalTests"] == 1
    assert evidence["failedTests"] == 1
    assert len(evidence["reportDigest"]) == 64

    collection_failure = _report(success=False, assertion_status=None)
    collection_failure["numFailedTestSuites"] = 1
    collection_failure["numPassedTestSuites"] = 0
    _write_report(tmp_path, collection_failure)

    rejected = read_vitest_evidence(tmp_path)

    assert rejected["state"] == "invalid"
    assert rejected["reason"] == "report_did_not_prove_test_outcome"


def test_vitest_report_requires_consistent_test_counters(tmp_path: Path) -> None:
    report = _report(success=False, assertion_status="failed")
    report["numTotalTests"] = 2
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_inconsistent"


def test_vitest_report_rejects_failed_assertion_in_passed_suite(tmp_path: Path) -> None:
    report = _report(success=False, assertion_status="failed")
    test_result = cast(list[dict[str, object]], report["testResults"])[0]
    test_result["status"] = "passed"
    report["numFailedTestSuites"] = 0
    report["numPassedTestSuites"] = 1
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_inconsistent"


def test_vitest_report_keeps_nested_suite_and_file_result_counts_distinct(
    tmp_path: Path,
) -> None:
    report = _report(success=True, assertion_status="passed")
    report["numPassedTestSuites"] = 3
    report["numTotalTestSuites"] = 3
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "passed_tests"
    assert evidence["totalTests"] == 1


def test_vitest_report_rejects_nonfinite_time(tmp_path: Path) -> None:
    report = _report(success=True, assertion_status="passed")
    report["startTime"] = float("nan")
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_invalid"


@pytest.mark.parametrize("status", ["pending", "skipped", "disabled", "todo", None])
def test_vitest_report_does_not_admit_unexecuted_only_success(
    tmp_path: Path, status: str | None
) -> None:
    report = _report(success=True, assertion_status=status)
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_did_not_prove_test_outcome"


@pytest.mark.parametrize("benchmarks", [None, False, {}, "", [None], [{"name": "bench"}]])
def test_vitest_report_rejects_nonempty_or_invalid_benchmarks(
    tmp_path: Path, benchmarks: object
) -> None:
    report = _report(success=True, assertion_status="passed")
    _assertion(report)["benchmarks"] = benchmarks
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_invalid"


@pytest.mark.parametrize("level", ["report", "result", "assertion"])
def test_vitest_report_keeps_unknown_fields_closed(tmp_path: Path, level: str) -> None:
    report = _report(success=True, assertion_status="passed")
    target = report
    if level == "result":
        target = cast(list[dict[str, object]], report["testResults"])[0]
    elif level == "assertion":
        target = _assertion(report)
    target["unknown"] = []
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_invalid"


def test_vitest_report_requires_pinned_reporter_benchmarks_field(tmp_path: Path) -> None:
    report = _report(success=True, assertion_status="passed")
    del _assertion(report)["benchmarks"]
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_invalid"


def test_vitest_report_rejects_failed_assertion_without_failure_evidence(tmp_path: Path) -> None:
    report = _report(success=False, assertion_status="failed")
    _assertion(report)["failureMessages"] = []
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_did_not_prove_test_outcome"


def test_vitest_report_rejects_duplicate_result_against_native_counters(tmp_path: Path) -> None:
    report = _report(success=True, assertion_status="passed")
    results = cast(list[dict[str, object]], report["testResults"])
    results.append(results[0])
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_identity_invalid"


def test_vitest_report_keeps_test_count_bound(tmp_path: Path) -> None:
    report = _report(success=True, assertion_status="passed")
    report["numTotalTests"] = report["numPassedTests"] = 100_001
    _write_report(tmp_path, report)

    evidence = read_vitest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["reason"] == "report_invalid"


def test_vitest_command_owns_one_machine_report_path(tmp_path: Path) -> None:
    command = prepare_vitest_command(
        ["frontend/node_modules/.bin/vitest", "run", "tests/example.test.ts"],
        tmp_path,
    )

    assert command[-2] == "--reporter=json"
    assert command[-1] == f"--outputFile={tmp_path / VITEST_REPORT_RELATIVE_PATH}"
    with pytest.raises(RuntimeError, match="cannot override"):
        prepare_vitest_command(
            ["vitest", "run", "--reporter=default"],
            tmp_path,
        )


@pytest.mark.parametrize(
    "change", ["rename", "skip-rename", "swap", "skip-status", "duplicate", "file", "baseline"]
)
def test_vitest_mutant_requires_identical_baseline_population_and_skips(
    tmp_path: Path, change: str
) -> None:
    report = _report(success=True, assertion_status="passed")
    skip = deepcopy(_assertion(report))
    skip.update(title="omitted", fullName="suite omitted", status="pending")
    result = cast(list[dict[str, object]], report["testResults"])[0]
    assertions = cast(list[dict[str, object]], result["assertionResults"])
    assertions.append(skip)
    report.update(numTotalTests=2, numPendingTests=1)
    _write_report(tmp_path, report)
    baseline: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": 0,
        "vitestEvidence": read_vitest_evidence(tmp_path),
    }
    assert baseline["vitestEvidence"]["state"] == "passed_tests"
    if change == "rename":
        _assertion(report).update(title="different", fullName="suite different")
    elif change == "skip-rename":
        skip.update(title="different", fullName="suite different")
    elif change == "swap":
        _assertion(report)["status"] = "pending"
        skip["status"] = "passed"
    elif change == "skip-status":
        skip["status"] = "skipped"
    elif change == "duplicate":
        assertions.append(deepcopy(_assertion(report)))
        report.update(numTotalTests=3, numPassedTests=2)
    elif change == "file":
        result["name"] = "tests/other.test.ts"
    else:
        baseline["executable"] = False
    _write_report(tmp_path, report)
    execution: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": 0,
        "vitestEvidence": read_vitest_evidence(tmp_path),
    }
    assert (
        classify_mutation_execution({"command": ["vitest"]}, execution, baseline=baseline)["status"]
        == "invalid"
    )


@pytest.mark.parametrize("exit_code", [-15, 0, 1, 2, 3, 4, 5])
def test_vitest_matching_population_requires_exact_failure_exit(
    tmp_path: Path, exit_code: int
) -> None:
    _write_report(tmp_path, _report(success=True, assertion_status="passed"))
    baseline: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": 0,
        "vitestEvidence": read_vitest_evidence(tmp_path),
    }
    _write_report(tmp_path, _report(success=False, assertion_status="failed"))
    execution: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": exit_code,
        "vitestEvidence": read_vitest_evidence(tmp_path),
    }
    classification = classify_mutation_execution(
        {"command": ["vitest"]}, execution, baseline=baseline
    )
    assert classification["status"] == ("killed" if exit_code == 1 else "invalid")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("unchanged", "survived"),
        ("failure", "killed"),
        ("drop", "invalid"),
        ("add", "invalid"),
        ("replace", "invalid"),
        ("skip-swap", "invalid"),
    ],
)
def test_vitest_repeated_titles_preserve_occurrences_and_skip_positions(
    tmp_path: Path, change: str, expected: str
) -> None:
    report = _report(success=True, assertion_status="passed")
    result = cast(list[dict[str, object]], report["testResults"])[0]
    assertions = cast(list[dict[str, object]], result["assertionResults"])
    skipped = deepcopy(assertions[0])
    skipped["status"] = "pending"
    assertions.append(skipped)
    report.update(numTotalTests=2, numPendingTests=1)
    _write_report(tmp_path, report)
    baseline: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": 0,
        "vitestEvidence": read_vitest_evidence(tmp_path),
    }
    assert baseline["vitestEvidence"]["state"] == "passed_tests"

    if change == "failure":
        assertions[0].update(status="failed", failureMessages=["assertion failed"])
        result["status"] = "failed"
        report.update(
            success=False,
            numPassedTests=0,
            numFailedTests=1,
            numPassedTestSuites=0,
            numFailedTestSuites=1,
        )
    elif change == "drop":
        assertions.pop()
        report.update(numTotalTests=1, numPendingTests=0)
    elif change == "add":
        assertions.append(deepcopy(skipped))
        report.update(numTotalTests=3, numPendingTests=2)
    elif change == "replace":
        skipped.update(title="other", fullName="suite other")
    elif change == "skip-swap":
        assertions[0]["status"] = "pending"
        skipped["status"] = "passed"
    _write_report(tmp_path, report)
    execution: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": 1 if change == "failure" else 0,
        "vitestEvidence": read_vitest_evidence(tmp_path),
    }
    assert execution["vitestEvidence"]["state"] == (
        "failed_tests" if change == "failure" else "passed_tests"
    )
    assert (
        classify_mutation_execution({"command": ["vitest"]}, execution, baseline=baseline)["status"]
        == expected
    )


@pytest.mark.parametrize(
    "file", ["", "../outside.test.ts", "/outside.test.ts", "tests\\other.test.ts"]
)
def test_vitest_identity_rejects_foreign_or_empty_file(tmp_path: Path, file: str) -> None:
    report = _report(success=True, assertion_status="passed")
    cast(list[dict[str, object]], report["testResults"])[0]["name"] = file
    _write_report(tmp_path, report)
    assert read_vitest_evidence(tmp_path)["state"] == "invalid"


def test_vitest_identity_preserves_title_boundaries_and_order(tmp_path: Path) -> None:
    report = _report(success=True, assertion_status="passed")
    first = _assertion(report)
    first.update(ancestorTitles=["a b"], title="c", fullName="a b c")
    other = deepcopy(first)
    other.update(ancestorTitles=["a"], title="b c")
    assertions = cast(
        list[dict[str, object]],
        cast(list[dict[str, object]], report["testResults"])[0]["assertionResults"],
    )
    assertions.append(other)
    report.update(numTotalTests=2, numPassedTests=2)
    _write_report(tmp_path, report)
    initial = read_vitest_evidence(tmp_path)
    assert initial["state"] == "passed_tests"
    assertions.reverse()
    _write_report(tmp_path, report)
    assert (
        read_vitest_evidence(tmp_path)["executedIdentityDigest"]
        == initial["executedIdentityDigest"]
    )


def test_vitest_command_preserves_target_behind_report_directory_symlink(tmp_path: Path) -> None:
    target = tmp_path / "other"
    target.mkdir()
    protected = target / VITEST_REPORT_RELATIVE_PATH.name
    protected.write_text("preserve")
    (tmp_path / VITEST_REPORT_RELATIVE_PATH.parent).symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        prepare_vitest_command(["vitest"], tmp_path)
    assert protected.read_text() == "preserve"


def _write_report(root: Path, report: dict[str, object]) -> None:
    path = root / VITEST_REPORT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")


def _assertion(report: dict[str, object]) -> dict[str, object]:
    result = cast(list[dict[str, object]], report["testResults"])[0]
    return cast(list[dict[str, object]], result["assertionResults"])[0]


def _report(*, success: bool, assertion_status: str | None) -> dict[str, object]:
    assertions: list[dict[str, object]] = []
    if assertion_status is not None:
        assertions.append(
            {
                "ancestorTitles": ["suite"],
                "benchmarks": [],
                "failureMessages": ["assertion failed"] if assertion_status == "failed" else [],
                "fullName": "suite test",
                "meta": {},
                "status": assertion_status,
                "tags": [],
                "title": "test",
            }
        )
    failed = int(assertion_status == "failed")
    passed = int(assertion_status == "passed")
    pending = int(assertion_status in {"pending", "skipped", "disabled"})
    todo = int(assertion_status == "todo")
    total = failed + passed + pending + todo
    return {
        "numFailedTests": failed,
        "numFailedTestSuites": failed,
        "numPassedTests": passed,
        "numPassedTestSuites": 1 if success else 0,
        "numPendingTests": pending,
        "numPendingTestSuites": 0,
        "numTodoTests": todo,
        "numTotalTests": total,
        "numTotalTestSuites": 1,
        "snapshot": {},
        "startTime": 1,
        "success": success,
        "testResults": [
            {
                "assertionResults": assertions,
                "endTime": 2,
                "message": "",
                "name": "tests/example.test.ts",
                "startTime": 1,
                "status": "failed" if not success else "passed",
            }
        ],
    }
