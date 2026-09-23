from __future__ import annotations

# The repository's test assertion exception is scoped to backend/tests.
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, cast

import pytest
from scripts.bounded_process import CommandResult
from scripts.mutation.detached_worktree_lifecycle import DetachedWorktreeLifecycle
from scripts.mutation.mutation_manifest import (
    Mutant,
    MutationCommand,
    MutationManifest,
)
from scripts.mutation.mutation_suite_runner import (
    SHARED_AUTHORITY_RELATIVE_PATHS,
    ExecutionClassificationInput,
    _execute_witness,
    classify_mutation_execution,
)
from scripts.mutation.mutation_suite_specs import (
    EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS,
)

SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"{name} is required for mutation runner tests")
    return executable


GIT: Final = _required_executable("git")


class _WitnessLifecycle:
    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree

    def run(
        self,
        _command: str,
        _arguments: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        max_buffer: int,
        timeout_ms: int,
    ) -> CommandResult:
        assert cwd == self.worktree
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert max_buffer > 0
        assert timeout_ms == 1_000
        return CommandResult(0, "stdout", "stderr")

    def assert_running(self) -> None:
        return None


def test_mutation_duration_uses_monotonic_samples(
    tmp_path: Path,
) -> None:
    samples = iter((5_000_000_000, 5_250_999_999))
    lifecycle = cast(DetachedWorktreeLifecycle, _WitnessLifecycle(tmp_path))
    manifest: MutationManifest = {
        "expectedKilled": 1,
        "expectedMutantIds": ["probe"],
        "mutants": [],
        "outerTimeoutMs": 2_000,
        "timeoutMs": 1_000,
    }

    result = _execute_witness(
        lifecycle,
        manifest,
        _mutant("probe", "target.py", "before", "after", "survived"),
        monotonic_ns=lambda: next(samples),
    )

    assert result["durationMs"] == 250
    assert result["outputDigest"] == hashlib.sha256(b"stdout\nstderr").hexdigest()


def test_mutation_duration_rejects_regressed_monotonic_sample(tmp_path: Path) -> None:
    samples = iter((5_000_000_000, 4_999_999_999))
    lifecycle = cast(DetachedWorktreeLifecycle, _WitnessLifecycle(tmp_path))
    manifest: MutationManifest = {
        "expectedKilled": 1,
        "expectedMutantIds": ["probe"],
        "mutants": [],
        "outerTimeoutMs": 2_000,
        "timeoutMs": 1_000,
    }

    with pytest.raises(RuntimeError, match=r"^monotonic clock regressed$"):
        _execute_witness(
            lifecycle,
            manifest,
            _mutant("probe", "target.py", "before", "after", "survived"),
            monotonic_ns=lambda: next(samples),
        )


def _imported_modules(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return (node.module,)
    return ()


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            **(environment or {}),
        },
    )


def _mutant(
    mutant_id: str,
    file: str,
    original: str,
    replacement: str,
    mode: str,
) -> Mutant:
    return {
        "id": mutant_id,
        "file": file,
        "operator": f"probe-{mutant_id}",
        "original": original,
        "replacement": replacement,
        "witnessId": f"probe-{mutant_id}",
        "requirementIds": ["REQ-PROBE-001"],
        "command": [sys.executable, "witness.py", mode, file],
    }


def _initialize_repository(
    root: Path,
    *,
    include_static_invalid: bool = True,
) -> MutationManifest:
    mutation_dir = root / "scripts" / "mutation"
    for relative_path in SHARED_AUTHORITY_RELATIVE_PATHS:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_ROOT / relative_path, target)
    for relative_path in EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_ROOT / relative_path, target)

    probe_authority_paths = (
        "scripts/mutation/probe.py",
        *EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS,
        "manifest.json",
    )
    (mutation_dir / "probe.py").write_text(
        f"""from scripts.mutation.mutation_suite_runner import (
    MutationSuiteConfig,
    run_mutation_suite_main,
)

config = MutationSuiteConfig(
    dependencies=("dependency-cache",),
    manifest_relative_path="manifest.json",
    report_id="mutation-runner-self-test",
    temp_prefix="mutation-runner-probe-",
    authority_relative_paths={probe_authority_paths!r},
)
raise SystemExit(run_mutation_suite_main(config))
""",
        encoding="utf-8",
    )
    (root / "witness.py").write_text(
        """import os
import sys
from pathlib import Path

mode, path = sys.argv[1:]
if not Path("dependency-cache").is_symlink():
    raise SystemExit(4)
if "MUTATION_AMBIENT_SENTINEL" in os.environ:
    raise SystemExit(6)
if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
    raise SystemExit(7)
source = Path(path).read_text(encoding="utf-8")
with Path("dependency-cache/executions.log").open("a", encoding="utf-8") as stream:
    stream.write(f"{mode}\\n")
if mode == "killed":
    raise SystemExit(1 if "MUTATED_KILLED" in source else 0)
if mode == "restored":
    if "SAFE_KILLED" not in source:
        raise SystemExit(3)
    raise SystemExit(1 if "MUTATED_RESTORED" in source else 0)
if mode == "survived":
    raise SystemExit(0)
if mode == "invalid-baseline":
    raise SystemExit(1)
raise SystemExit(5)
""",
        encoding="utf-8",
    )
    mutants: list[Mutant] = [
        _mutant("killed", "shared.txt", "SAFE_KILLED", "MUTATED_KILLED", "killed"),
        _mutant(
            "restored",
            "shared.txt",
            "SAFE_RESTORED",
            "MUTATED_RESTORED",
            "restored",
        ),
        _mutant("survived", "survived.txt", "SAFE_SURVIVED", "MUTATED", "survived"),
        _mutant(
            "invalid_baseline",
            "invalid-baseline.txt",
            "SAFE_INVALID",
            "MUTATED",
            "invalid-baseline",
        ),
    ]
    if include_static_invalid:
        mutants.extend(
            (
                _mutant(
                    "invalid_target",
                    "invalid-target.txt",
                    "MISSING",
                    "MUTATED",
                    "survived",
                ),
                _mutant(
                    "invalid_multiple",
                    "invalid-multiple.txt",
                    "DUPLICATE",
                    "MUTATED",
                    "survived",
                ),
            )
        )
    (root / "shared.txt").write_text("SAFE_KILLED\nSAFE_RESTORED\n", encoding="utf-8")
    (root / "survived.txt").write_text("SAFE_SURVIVED\n", encoding="utf-8")
    (root / "invalid-baseline.txt").write_text("SAFE_INVALID\n", encoding="utf-8")
    (root / "invalid-target.txt").write_text("SAFE_INVALID_TARGET\n", encoding="utf-8")
    (root / "invalid-multiple.txt").write_text("DUPLICATE\nDUPLICATE\n", encoding="utf-8")
    dependency = root / "dependency-cache"
    dependency.mkdir()
    (dependency / "marker.txt").write_text("dependency\n", encoding="utf-8")
    manifest: MutationManifest = {
        "expectedKilled": len(mutants),
        "expectedMutantIds": [mutant["id"] for mutant in mutants],
        "mutants": mutants,
        "outerTimeoutMs": 80_000,
        "timeoutMs": 1_000,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    _run([GIT, "init", "--quiet", str(root)])
    _run([GIT, "-C", str(root), "config", "user.name", "Mutation Runner Probe"])
    _run(
        [
            GIT,
            "-C",
            str(root),
            "config",
            "user.email",
            "mutation-runner@example.invalid",
        ]
    )
    _run([GIT, "-C", str(root), "add", "."])
    _run([GIT, "-C", str(root), "commit", "--quiet", "-m", "probe fixture"])
    return manifest


def _execute_suite(
    root: Path,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    execution = _run(
        [sys.executable, "-m", "scripts.mutation.probe"],
        cwd=root,
        check=False,
        environment={"MUTATION_AMBIENT_SENTINEL": "must-not-cross"},
    )
    parsed = cast(object, json.loads(execution.stdout))
    assert isinstance(parsed, dict), execution.stderr
    return execution, cast(dict[str, object], parsed)


def _results_by_id(report: dict[str, object]) -> dict[str, dict[str, object]]:
    raw_results = report["results"]
    assert isinstance(raw_results, list)
    results: dict[str, dict[str, object]] = {}
    for raw_result in raw_results:
        assert isinstance(raw_result, dict)
        result = cast(dict[str, object], raw_result)
        mutant_id = result["id"]
        assert isinstance(mutant_id, str)
        results[mutant_id] = result
    return results


def test_mixed_suite_preserves_classification_restoration_and_report(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    manifest = _initialize_repository(root, include_static_invalid=False)
    execution, report = _execute_suite(root)
    results = _results_by_id(report)

    assert execution.returncode == 1
    assert execution.stderr == ""
    assert report["state"] == "failed"
    assert report["schemaVersion"] == 1
    assert report["reportId"] == "mutation-runner-self-test"
    assert report["reportKind"] == "mutation-runner-self-test"
    assert report["manifestPath"] == "manifest.json"
    assert report["cleanup"] == {"state": "passed", "worktreeRemoval": "removed"}
    assert "runError" not in report
    assert results["killed"]["status"] == "killed", json.dumps(results["killed"], sort_keys=True)
    assert results["restored"]["status"] == "killed"
    assert results["survived"]["status"] == "survived"
    assert results["invalid_baseline"]["status"] == "invalid"

    restored_baseline = results["restored"]["baseline"]
    assert isinstance(restored_baseline, dict)
    assert restored_baseline["exitCode"] == 0
    assert restored_baseline["executable"] is True

    assert report["summary"] == {
        "expectedKilled": len(manifest["mutants"]),
        "invalidCount": 1,
        "killedCount": 2,
        "survivedCount": 1,
        "totalCount": len(manifest["mutants"]),
    }
    manifest_bytes = (root / "manifest.json").read_bytes()
    assert report["manifestDigest"] == hashlib.sha256(manifest_bytes).hexdigest()
    revision = _run([GIT, "-C", str(root), "rev-parse", "HEAD"]).stdout.strip()
    assert report["sourceRevision"] == revision
    assert report["nonClaims"] == [
        "This finite mutation suite proves only that the named witnesses reject the named patches.",
        (
            "This report does not prove mutation completeness, provider execution, "
            "or absence of other defects."
        ),
    ]


def test_applicability_preflight_rejects_suite_before_first_witness(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    manifest = _initialize_repository(root)

    execution, report = _execute_suite(root)

    assert execution.returncode == 1
    assert report["state"] == "failed"
    assert report["results"] == []
    assert report["summary"] == {
        "expectedKilled": len(manifest["mutants"]),
        "invalidCount": 0,
        "killedCount": 0,
        "survivedCount": 0,
        "totalCount": 0,
    }
    error = report["runError"]
    assert isinstance(error, str)
    assert "mutation manifest applicability preflight failed" in error
    assert '"id":"invalid_target","occurrenceCount":0' in error
    assert '"id":"invalid_multiple","occurrenceCount":2' in error
    assert not (root / "dependency-cache" / "executions.log").exists()


def test_duplicate_manifest_identity_is_rejected_before_execution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _initialize_repository(root)
    manifest_value = cast(
        dict[str, object],
        cast(object, json.loads((root / "manifest.json").read_text(encoding="utf-8"))),
    )
    mutants = cast(list[dict[str, object]], manifest_value["mutants"])
    expected_ids = cast(list[str], manifest_value["expectedMutantIds"])
    mutants[1]["id"] = mutants[0]["id"]
    expected_ids[1] = expected_ids[0]
    (root / "manifest.json").write_text(json.dumps(manifest_value, indent=2), encoding="utf-8")
    _run([GIT, "-C", str(root), "add", "manifest.json"])
    _run([GIT, "-C", str(root), "commit", "--quiet", "-m", "duplicate mutant id"])

    execution, report = _execute_suite(root)

    assert execution.returncode == 1
    assert report["state"] == "failed"
    assert report["results"] == []
    run_error = report["runError"]
    assert isinstance(run_error, str)
    assert "unique and canonical" in run_error


@pytest.mark.parametrize(
    "relative_path",
    [
        *SHARED_AUTHORITY_RELATIVE_PATHS,
        "scripts/mutation/probe.py",
        *EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS,
        "manifest.json",
    ],
)
def test_runner_authority_must_match_head(
    tmp_path: Path,
    relative_path: str,
) -> None:
    root = tmp_path / "repo"
    _initialize_repository(root)
    authority_path = root / relative_path
    authority_path.write_text(f"{authority_path.read_text(encoding='utf-8')}\n", encoding="utf-8")

    execution, report = _execute_suite(root)

    assert execution.returncode == 1
    assert report["manifestDigest"] is None
    assert report["cleanup"] == {"state": "passed", "worktreeRemoval": "not-needed"}
    assert report["summary"] == {
        "expectedKilled": 0,
        "invalidCount": 0,
        "killedCount": 0,
        "survivedCount": 0,
        "totalCount": 0,
    }
    assert report["runError"] == f"mutation authority must match HEAD: {relative_path}"


def test_mutation_authority_is_closed_over_first_party_imports() -> None:
    authority = {
        *SHARED_AUTHORITY_RELATIVE_PATHS,
        *EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS,
        "scripts/mutation/mutation_suite_specs.py",
    }
    assert {"scripts/__init__.py", "scripts/mutation/__init__.py"} <= authority
    missing: set[str] = set()
    for relative_path in authority:
        if not relative_path.endswith(".py"):
            continue
        source_path = SOURCE_ROOT / relative_path
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=relative_path)
        modules = {
            module
            for node in ast.walk(tree)
            for module in _imported_modules(node)
            if module == "scripts" or module.startswith("scripts.")
        }
        for module in modules:
            candidate = module.replace(".", "/")
            module_path = f"{candidate}.py"
            package_path = f"{candidate}/__init__.py"
            if (SOURCE_ROOT / module_path).is_file() and module_path not in authority:
                missing.add(module_path)
            if (SOURCE_ROOT / package_path).is_file() and package_path not in authority:
                missing.add(package_path)
    assert missing == set()


@pytest.mark.parametrize(
    ("command", "execution", "expected_status", "expected_reason"),
    [
        (
            ["python", "-m", "pytest"],
            {"executable": True, "exitCode": 2},
            "invalid",
            "pytest machine evidence did not prove the same test selection and outcome",
        ),
        (
            ["pytest.exe"],
            {"executable": True, "exitCode": 1},
            "invalid",
            "pytest machine evidence did not prove the same test selection and outcome",
        ),
        (
            ["pytest"],
            {"executable": True, "exitCode": 0},
            "invalid",
            "pytest machine evidence did not prove the same test selection and outcome",
        ),
        (
            ["frontend/node_modules/.bin/vitest"],
            {"executable": True, "exitCode": 2},
            "invalid",
            "vitest machine evidence did not prove a consistent test outcome (exit 2)",
        ),
        (
            ["frontend/node_modules/.bin/vitest"],
            {"executable": True, "exitCode": 1},
            "invalid",
            "vitest machine evidence did not prove a consistent test outcome (exit 1)",
        ),
        (
            ["frontend/node_modules/.bin/vitest"],
            {
                "executable": True,
                "exitCode": 1,
                "vitestEvidence": {
                    "state": "failed_tests",
                    "totalTests": 1,
                    "failedTests": 1,
                },
            },
            "invalid",
            "vitest machine evidence did not prove a consistent test outcome (exit 1)",
        ),
        (
            ["frontend/node_modules/.bin/vitest"],
            {
                "executable": True,
                "exitCode": 0,
                "vitestEvidence": {
                    "state": "passed_tests",
                    "totalTests": 1,
                    "failedTests": 0,
                },
            },
            "invalid",
            "vitest machine evidence did not prove a consistent test outcome (exit 0)",
        ),
        (
            ["frontend/node_modules/.bin/vitest"],
            {
                "executable": True,
                "exitCode": 0,
                "vitestEvidence": {
                    "state": "failed_tests",
                    "totalTests": 1,
                    "failedTests": 1,
                },
            },
            "invalid",
            "vitest machine evidence did not prove a consistent test outcome (exit 0)",
        ),
        (
            ["pytest"],
            {"executable": False, "exitCode": None},
            "invalid",
            "mutated witness did not execute",
        ),
    ],
)
def test_classification_preserves_pytest_exit_semantics(
    command: list[str],
    execution: ExecutionClassificationInput,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    mutant: MutationCommand = {"command": command}
    classification = classify_mutation_execution(mutant, execution)
    assert classification["status"] == expected_status
    assert classification.get("reason") == expected_reason
