from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scripts.mutation import pytest_report
from scripts.mutation.detached_worktree_lifecycle import DetachedWorktreeLifecycle
from scripts.mutation.mutation_manifest import Mutant, MutationManifest
from scripts.mutation.mutation_suite_runner import (
    ExecutionClassificationInput,
    _baseline_is_valid,
    _execute_witness,
    classify_mutation_execution,
)
from scripts.mutation.pytest_report import (
    PYTEST_REPORT_RELATIVE_PATH,
    prepare_pytest_command,
    read_pytest_evidence,
)

_SUCCESS_CASE = (
    '<testcase classname="test_probe" name="test_guard" file="test_probe.py" time="0.001" />'
)
_SKIP = (
    '<testcase classname="test_probe" name="test_skip" file="test_probe.py"><skipped /></testcase>'
)


def _report(cases: str, *, tests: int = 1, failures: int = 0, skipped: int = 0) -> str:
    return (
        '<testsuites name="pytest tests"><testsuite name="pytest" '
        f'tests="{tests}" errors="0" failures="{failures}" skipped="{skipped}">'
        f"{cases}</testsuite></testsuites>"
    )


def _write_report(root: Path, content: str | bytes) -> Path:
    path = root / PYTEST_REPORT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


@pytest.mark.parametrize("skipped", [False, True])
def test_report_admits_passing_tests_with_stable_skips(tmp_path: Path, skipped: bool) -> None:
    _write_report(
        tmp_path,
        _report(
            _SUCCESS_CASE + (_SKIP if skipped else ""), tests=1 + skipped, skipped=int(skipped)
        ),
    )

    evidence = read_pytest_evidence(tmp_path)

    assert evidence["state"] == "passed_tests"
    assert evidence["totalTests"] == 1 + skipped
    assert evidence["failedTests"] == 0
    assert len(evidence["executedIdentityDigest"]) == 64
    assert len(evidence["skippedIdentityDigest"]) == 64


def test_invalid_report_diagnostic_excludes_error_payload(tmp_path: Path) -> None:
    _write_report(
        tmp_path,
        _report(_SUCCESS_CASE.replace(" />", "><error>private payload</error></testcase>")).replace(
            'errors="0"', 'errors="1"'
        ),
    )

    evidence = read_pytest_evidence(tmp_path)

    assert evidence["state"] == "invalid"
    assert evidence["admissionDetail"] == "report contains setup, teardown, or collection errors"
    assert "private payload" not in str(evidence)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "<testsuites>",
        _report(_SUCCESS_CASE, tests=2),
        _report(_SUCCESS_CASE, failures=1),
        _report(_SUCCESS_CASE, skipped=1),
        _report(_SUCCESS_CASE * 2, tests=2),
        _report(_SKIP, skipped=1),
        _report(_SUCCESS_CASE.replace(' file="test_probe.py"', "")),
        _report(_SUCCESS_CASE.replace(" />", "><error>setup</error></testcase>")),
        _report(_SUCCESS_CASE.replace(" />", "><failure /></testcase>"), failures=1),
        _report(_SUCCESS_CASE.replace(" />", "><failure>call</failure><skipped /></testcase>")),
        _report(_SUCCESS_CASE).replace('errors="0"', 'errors="1"'),
        _report(_SUCCESS_CASE).replace('tests="1"', 'tests="100001"'),
        _report(_SUCCESS_CASE).replace('tests="1"', 'tests="NaN"'),
        '<!DOCTYPE testsuites [<!ENTITY e "expanded">]>' + _report(_SUCCESS_CASE),
        (
            '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE testsuites SYSTEM "file:///not-read">'
            + _report(_SUCCESS_CASE)
        ).encode("utf-16"),
        "<node>" * 9 + "</node>" * 9,
        "<testsuites>" + "<n />" * 400_010 + "</testsuites>",
    ],
    ids=[
        "empty",
        "malformed",
        "total",
        "failure-count",
        "skip-count",
        "duplicate",
        "only-skips",
        "identity",
        "setup",
        "no-failure-detail",
        "contradictory",
        "error-count",
        "count-bound",
        "count-syntax",
        "dtd",
        "utf16-dtd",
        "depth",
        "elements",
    ],
)
def test_report_rejects_invalid_machine_evidence(tmp_path: Path, content: str | bytes) -> None:
    _write_report(tmp_path, content)

    assert read_pytest_evidence(tmp_path)["state"] == "invalid"


@pytest.mark.parametrize("kind", ["missing", "oversize", "symlink", "directory"])
def test_report_file_boundary_is_fail_closed(tmp_path: Path, kind: str) -> None:
    if kind != "missing":
        path = _write_report(tmp_path, _report(_SUCCESS_CASE))
        if kind == "oversize":
            path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
        elif kind == "symlink":
            target = tmp_path / "other.xml"
            path.rename(target)
            path.symlink_to(target)
        else:
            path.unlink()
            path.mkdir()

    assert read_pytest_evidence(tmp_path)["state"] == "invalid"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--junitxml=other.xml"],
        ["--junit-xml", "other.xml"],
        ["--junit-prefix=other"],
        ["-o", "junit_family=xunit2"],
        ["-ojunit_family=xunit2"],
        ["--override-ini=junit_family=xunit2"],
        ["-o", "addopts=-x"],
        ["-x"],
        ["-qx"],
        ["-lx"],
        ["-slqx"],
        ["--exitfirst"],
        ["--maxfail=1"],
    ],
)
def test_command_cannot_override_report_authority(tmp_path: Path, arguments: list[str]) -> None:
    with pytest.raises(RuntimeError, match="report or selection authority"):
        prepare_pytest_command([sys.executable, "-m", "pytest", *arguments], tmp_path)


def test_command_removes_stale_report_without_reinterpreting_test_names(tmp_path: Path) -> None:
    path = _write_report(tmp_path, _report(_SUCCESS_CASE))
    command = [sys.executable, "-m", "pytest", "test_junit_family.py", "-q"]

    prepared = prepare_pytest_command(command, tmp_path)

    assert prepared == [*command, f"--junitxml={path}", "-o", "junit_family=xunit1"]
    assert not path.exists()
    assert read_pytest_evidence(tmp_path)["state"] == "invalid"
    plain = [sys.executable, "witness.py"]
    assert prepare_pytest_command(plain, tmp_path) is plain


def test_command_does_not_follow_a_report_directory_symlink(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    protected = other / PYTEST_REPORT_RELATIVE_PATH.name
    protected.write_text("preserve")
    (tmp_path / PYTEST_REPORT_RELATIVE_PATH.parent).symlink_to(other, target_is_directory=True)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        prepare_pytest_command(["pytest"], tmp_path)

    assert protected.read_text() == "preserve"


@pytest.mark.parametrize("bound", ["_MAXIMUM_ELEMENTS", "_MAXIMUM_DEPTH", "_MAXIMUM_REPORT_BYTES"])
def test_parser_bounds_have_valid_boundary_falsifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bound: str,
) -> None:
    content = _report(_SUCCESS_CASE)
    exact = len(content.encode()) if bound == "_MAXIMUM_REPORT_BYTES" else 3
    _write_report(tmp_path, content)
    monkeypatch.setattr(pytest_report, bound, exact)
    assert read_pytest_evidence(tmp_path)["state"] == "passed_tests"
    monkeypatch.setattr(pytest_report, bound, exact - 1)
    assert read_pytest_evidence(tmp_path)["state"] == "invalid"


@pytest.mark.parametrize("changed", ["executed", "skipped", "exit", "baseline-exit"])
def test_classifier_rejects_independent_identity_or_exit_drift(
    tmp_path: Path, changed: str
) -> None:
    content = _report(_SUCCESS_CASE + _SKIP, tests=2, skipped=1)
    _write_report(tmp_path, content)
    baseline: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": 0,
        "pytestEvidence": read_pytest_evidence(tmp_path),
    }
    if changed == "executed":
        content = content.replace("test_guard", "test_other")
    elif changed == "skipped":
        content = content.replace("test_skip", "test_other")
    elif changed == "baseline-exit":
        baseline["exitCode"] = 1
    _write_report(tmp_path, content)
    execution: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": 1 if changed == "exit" else 0,
        "pytestEvidence": read_pytest_evidence(tmp_path),
    }

    result = classify_mutation_execution({"command": ["pytest"]}, execution, baseline=baseline)

    assert result["status"] == "invalid"


@pytest.mark.parametrize("exit_code", [-15, 0, 1, 2, 3, 4, 5])
def test_failed_report_requires_exact_test_failure_exit(tmp_path: Path, exit_code: int) -> None:
    _write_report(tmp_path, _report(_SUCCESS_CASE))
    baseline: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": 0,
        "pytestEvidence": read_pytest_evidence(tmp_path),
    }
    failed = _SUCCESS_CASE.replace(" />", "><failure>call assertion</failure></testcase>")
    _write_report(tmp_path, _report(failed, failures=1))
    execution: ExecutionClassificationInput = {
        "executable": True,
        "exitCode": exit_code,
        "pytestEvidence": read_pytest_evidence(tmp_path),
    }

    result = classify_mutation_execution({"command": ["pytest"]}, execution, baseline=baseline)

    assert result["status"] == ("killed" if exit_code == 1 else "invalid")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("pass", "survived"),
        ("call", "killed"),
        ("interrupt", "invalid"),
        ("setup", "invalid"),
        ("teardown", "invalid"),
        ("collect", "invalid"),
        ("skip", "invalid"),
        ("rename", "invalid"),
    ],
)
def test_native_pytest_phase_and_identity_are_required_for_mutation_kill(
    tmp_path: Path,
    mode: str,
    expected: str,
) -> None:
    source = """import pytest
MODE = "pass"
if MODE == "collect":
    raise RuntimeError("collection")
@pytest.fixture
def resource():
    if MODE == "setup":
        raise RuntimeError("setup")
    yield
    if MODE == "teardown":
        raise RuntimeError("teardown")
def test_guard(resource):
    if MODE == "skip":
        pytest.skip("skip")
    assert MODE not in {"call", "interrupt"}
"""
    lifecycle = DetachedWorktreeLifecycle(repo_root=tmp_path, temp_prefix="pytest-evidence-")
    manifest: MutationManifest = {
        "expectedKilled": 1,
        "expectedMutantIds": ["phase"],
        "mutants": [],
        "outerTimeoutMs": 40_000,
        "timeoutMs": 15_000,
    }
    mutant: Mutant = {
        "id": "phase",
        "file": "test_probe.py",
        "operator": "phase-substitution",
        "original": 'MODE = "pass"',
        "replacement": f'MODE = "{mode}"',
        "witnessId": "phase",
        "requirementIds": ["REQ-PROBE-001"],
        "command": [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "test_probe.py",
        ],
    }
    try:
        lifecycle.worktree.mkdir()
        path = lifecycle.worktree / "test_probe.py"
        path.write_text(source)
        if mode == "interrupt":
            (lifecycle.worktree / "conftest.py").write_text(
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    if exitstatus == 1:\n"
                "        session.exitstatus = 2\n"
            )
        baseline = _execute_witness(lifecycle, manifest, mutant)
        assert _baseline_is_valid(mutant, baseline)
        changed = source.replace(mutant["original"], mutant["replacement"])
        if mode == "rename":
            changed = changed.replace("test_guard", "test_other")
        path.write_text(changed)

        execution = _execute_witness(lifecycle, manifest, mutant)
        classification = classify_mutation_execution(mutant, execution, baseline=baseline)

        assert classification["status"] == expected, (baseline, execution, classification)
        assert "pytestEvidence" in execution
        if mode == "interrupt":
            assert execution["exitCode"] == 2
            assert execution["pytestEvidence"]["state"] == "failed_tests"
        assert classify_mutation_execution(mutant, execution)["status"] == "invalid"
    finally:
        assert lifecycle.cleanup().state == "passed"
