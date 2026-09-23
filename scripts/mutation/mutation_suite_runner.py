"""Shared detached-worktree runner for finite mutation suites."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NotRequired, TypedDict

from scripts.mutation.detached_worktree_lifecycle import (
    DEFAULT_MAX_BUFFER_BYTES,
    CleanupResult,
    DetachedWorktreeLifecycle,
)
from scripts.mutation.mutation_manifest import (
    Mutant,
    MutationCommand,
    MutationManifest,
    assert_manifest_applicable,
    decode_mutation_manifest,
)
from scripts.mutation.pytest_report import (
    PytestEvidence,
    is_pytest_command,
    prepare_pytest_command,
    read_pytest_evidence,
)
from scripts.mutation.vitest_report import (
    VitestEvidence,
    is_vitest_command,
    prepare_vitest_command,
    read_vitest_evidence,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SHARED_AUTHORITY_RELATIVE_PATHS: Final = (
    "scripts/__init__.py",
    "scripts/bounded_git.py",
    "scripts/bounded_process.py",
    "scripts/mutation/__init__.py",
    "scripts/mutation/detached_worktree_lifecycle.py",
    "scripts/mutation/mutation_manifest.py",
    "scripts/mutation/mutation_suite_runner.py",
    "scripts/mutation/pytest_report.py",
    "scripts/mutation/vitest_report.py",
    "scripts/repository_paths.py",
)
NON_CLAIMS: Final = (
    "This finite mutation suite proves only that the named witnesses reject the named patches.",
    (
        "This report does not prove mutation completeness, provider execution, "
        "or absence of other defects."
    ),
)


@dataclass(frozen=True, slots=True)
class MutationSuiteConfig:
    dependencies: tuple[str, ...]
    manifest_relative_path: str
    report_id: str
    temp_prefix: str
    authority_relative_paths: tuple[str, ...] = ()


class ExecutionClassificationInput(TypedDict):
    executable: bool
    exitCode: int | None
    vitestEvidence: NotRequired[VitestEvidence]
    pytestEvidence: NotRequired[PytestEvidence]


class WitnessExecution(ExecutionClassificationInput):
    durationMs: int
    outputDigest: str
    timedOut: bool
    executionError: NotRequired[str]


class Classification(TypedDict):
    status: Literal["invalid", "killed", "survived"]
    reason: NotRequired[str]


type MutationResult = dict[str, object]
type MutationReport = dict[str, object]
type MonotonicClock = Callable[[], int]


def run_mutation_suite(
    config: MutationSuiteConfig,
    *,
    repo_root: Path = REPO_ROOT,
) -> MutationReport:
    root = repo_root.resolve()
    lifecycle = DetachedWorktreeLifecycle(repo_root=root, temp_prefix=config.temp_prefix)
    results: list[MutationResult] = []
    manifest: MutationManifest | None = None
    manifest_bytes: bytes | None = None
    run_error: str | None = None
    source_revision: str | None = None

    try:
        lifecycle.install_signal_handlers()
        source_revision = lifecycle.git(("rev-parse", "HEAD")).stdout.strip()
        _assert_authority_matches_head(lifecycle, config, source_revision, root)
        lifecycle.add_detached_worktree(source_revision)
        manifest_path = lifecycle.worktree / config.manifest_relative_path
        manifest_bytes = manifest_path.read_bytes()
        manifest = decode_mutation_manifest(manifest_bytes)
        assert_manifest_applicable(manifest, source_root=lifecycle.worktree)
        for relative_path in config.dependencies:
            _link_dependency(root, lifecycle.worktree, relative_path)

        for mutant in manifest["mutants"]:
            lifecycle.assert_running()
            results.append(_run_mutant(lifecycle, manifest, mutant))
    except Exception as error:
        run_error = str(error)
    finally:
        try:
            cleanup = lifecycle.cleanup()
        finally:
            lifecycle.dispose_signal_handlers()

    if lifecycle.received_signal is not None:
        lifecycle.rethrow_signal_if_needed()
        raise AssertionError("signal rethrow unexpectedly returned")
    killed_count = sum(result["status"] == "killed" for result in results)
    survived_count = sum(result["status"] == "survived" for result in results)
    invalid_count = sum(result["status"] == "invalid" for result in results)
    expected_killed = manifest["expectedKilled"] if manifest is not None else 0
    state = (
        "passed"
        if run_error is None
        and cleanup.state == "passed"
        and killed_count == expected_killed
        and survived_count == 0
        and invalid_count == 0
        else "failed"
    )

    cleanup_report = _cleanup_report(cleanup)
    report: MutationReport = {
        "schemaVersion": 1,
        "reportId": config.report_id,
        "reportKind": config.report_id,
        "state": state,
        "sourceRevision": source_revision,
        "manifestPath": config.manifest_relative_path,
        "manifestDigest": _digest(manifest_bytes) if manifest_bytes is not None else None,
        "cleanup": cleanup_report,
        "summary": {
            "expectedKilled": expected_killed,
            "invalidCount": invalid_count,
            "killedCount": killed_count,
            "survivedCount": survived_count,
            "totalCount": len(results),
        },
        "results": results,
        "nonClaims": list(NON_CLAIMS),
    }
    if run_error:
        report["runError"] = run_error
    return report


def write_mutation_report(report: MutationReport) -> None:
    sys.stdout.write(f"{json.dumps(report, indent=2, ensure_ascii=False)}\n")


def mutation_report_exit_code(report: MutationReport) -> int:
    return 0 if report.get("state") == "passed" else 1


def run_mutation_suite_main(
    config: MutationSuiteConfig,
    *,
    repo_root: Path = REPO_ROOT,
) -> int:
    report = run_mutation_suite(config, repo_root=repo_root)
    write_mutation_report(report)
    return mutation_report_exit_code(report)


def classify_mutation_execution(
    mutant: MutationCommand,
    execution: ExecutionClassificationInput,
    *,
    baseline: ExecutionClassificationInput | None = None,
) -> Classification:
    if not execution["executable"]:
        return {"status": "invalid", "reason": "mutated witness did not execute"}
    exit_code = execution["exitCode"]
    if is_pytest_command(mutant["command"]):
        pytest_evidence = execution.get("pytestEvidence")
        prior = baseline.get("pytestEvidence") if baseline is not None else None
        if (
            pytest_evidence is not None
            and pytest_evidence["state"] != "invalid"
            and prior is not None
            and prior["state"] == "passed_tests"
            and baseline is not None
            and baseline["executable"]
            and baseline["exitCode"] == 0
            and pytest_evidence["executedIdentityDigest"] == prior["executedIdentityDigest"]
            and pytest_evidence["skippedIdentityDigest"] == prior["skippedIdentityDigest"]
        ):
            if exit_code == 0 and pytest_evidence["state"] == "passed_tests":
                return {"status": "survived"}
            if exit_code == 1 and pytest_evidence["state"] == "failed_tests":
                return {"status": "killed"}
        return {
            "status": "invalid",
            "reason": "pytest machine evidence did not prove the same test selection and outcome",
        }
    if is_vitest_command(mutant["command"]):
        evidence = execution.get("vitestEvidence")
        previous = baseline.get("vitestEvidence") if baseline is not None else None
        if (
            evidence is not None
            and evidence["state"] != "invalid"
            and previous is not None
            and previous["state"] == "passed_tests"
            and baseline is not None
            and baseline["executable"]
            and baseline["exitCode"] == 0
            and evidence.get("executedIdentityDigest") is not None
            and evidence.get("skippedIdentityDigest") is not None
            and evidence.get("executedIdentityDigest") == previous.get("executedIdentityDigest")
            and evidence.get("skippedIdentityDigest") == previous.get("skippedIdentityDigest")
        ):
            if exit_code == 0 and evidence["state"] == "passed_tests":
                return {"status": "survived"}
            if exit_code == 1 and evidence["state"] == "failed_tests":
                return {"status": "killed"}
        rendered_exit_code = "null" if exit_code is None else str(exit_code)
        return {
            "status": "invalid",
            "reason": (
                "vitest machine evidence did not prove a consistent test outcome "
                f"(exit {rendered_exit_code})"
            ),
        }
    if exit_code == 0:
        return {"status": "survived"}
    return {"status": "killed"}


def _run_mutant(
    lifecycle: DetachedWorktreeLifecycle,
    manifest: MutationManifest,
    mutant: Mutant,
) -> MutationResult:
    target_path = lifecycle.worktree / mutant["file"]
    source = target_path.read_text(encoding="utf-8")
    occurrence_count = _count_occurrences(source, mutant["original"])
    patch_digest = _digest(
        _compact_json({"original": mutant["original"], "replacement": mutant["replacement"]})
    )
    if occurrence_count != 1:
        return {
            "id": mutant["id"],
            "status": "invalid",
            "file": mutant["file"],
            "operator": mutant["operator"],
            "witnessId": mutant["witnessId"],
            "patchDigest": patch_digest,
            "reason": f"expected one mutation target, found {occurrence_count}",
        }

    baseline = _execute_witness(lifecycle, manifest, mutant)
    if not _baseline_is_valid(mutant, baseline):
        return {
            "id": mutant["id"],
            "status": "invalid",
            "file": mutant["file"],
            "operator": mutant["operator"],
            "witnessId": mutant["witnessId"],
            "patchDigest": patch_digest,
            "baseline": baseline,
            "reason": "witness baseline did not prove a passing test outcome",
        }

    try:
        target_path.write_text(
            source.replace(mutant["original"], mutant["replacement"], 1),
            encoding="utf-8",
        )
        execution = _execute_witness(lifecycle, manifest, mutant)
    finally:
        if target_path.exists():
            target_path.write_text(source, encoding="utf-8")

    classification = classify_mutation_execution(mutant, execution, baseline=baseline)
    result: MutationResult = {
        "id": mutant["id"],
        "status": classification["status"],
        "file": mutant["file"],
        "operator": mutant["operator"],
        "witnessId": mutant["witnessId"],
        "command": mutant["command"],
        "baseline": baseline,
        "durationMs": execution["durationMs"],
        "exitCode": execution["exitCode"],
        "outputDigest": execution["outputDigest"],
        "patchDigest": patch_digest,
        "timedOut": execution["timedOut"],
    }
    if "reason" in classification:
        result["reason"] = classification["reason"]
    if "executionError" in execution:
        result["executionError"] = execution["executionError"]
    if "vitestEvidence" in execution:
        result["vitestEvidence"] = execution["vitestEvidence"]
    if "pytestEvidence" in execution:
        result["pytestEvidence"] = execution["pytestEvidence"]
    return result


def _execute_witness(
    lifecycle: DetachedWorktreeLifecycle,
    manifest: MutationManifest,
    mutant: Mutant,
    *,
    monotonic_ns: MonotonicClock = time.monotonic_ns,
) -> WitnessExecution:
    started_at_ns = monotonic_ns()
    environment = _witness_environment(mutant)
    command = prepare_pytest_command(
        prepare_vitest_command(mutant["command"], lifecycle.worktree), lifecycle.worktree
    )
    execution = lifecycle.run(
        command[0],
        command[1:],
        cwd=lifecycle.worktree,
        env=environment,
        max_buffer=DEFAULT_MAX_BUFFER_BYTES,
        timeout_ms=manifest["timeoutMs"],
    )
    lifecycle.assert_running()
    completed_at_ns = monotonic_ns()
    result: WitnessExecution = {
        "durationMs": _elapsed_milliseconds(started_at_ns, completed_at_ns),
        "executable": (
            not execution.timed_out and execution.error is None and execution.status is not None
        ),
        "exitCode": execution.status,
        "outputDigest": _digest(f"{execution.stdout}\n{execution.stderr}"),
        "timedOut": execution.timed_out,
    }
    if execution.error is not None:
        result["executionError"] = str(execution.error)
    if is_vitest_command(mutant["command"]):
        result["vitestEvidence"] = read_vitest_evidence(lifecycle.worktree)
    if is_pytest_command(mutant["command"]):
        result["pytestEvidence"] = read_pytest_evidence(lifecycle.worktree)
    return result


def _elapsed_milliseconds(started_at_ns: int, completed_at_ns: int) -> int:
    if completed_at_ns < started_at_ns:
        raise RuntimeError("monotonic clock regressed")
    return (completed_at_ns - started_at_ns) // 1_000_000


def _baseline_is_valid(mutant: MutationCommand, execution: WitnessExecution) -> bool:
    if not execution["executable"] or execution["exitCode"] != 0:
        return False
    if is_pytest_command(mutant["command"]):
        pytest_evidence = execution.get("pytestEvidence")
        return pytest_evidence is not None and pytest_evidence["state"] == "passed_tests"
    if not is_vitest_command(mutant["command"]):
        return True
    evidence = execution.get("vitestEvidence")
    return evidence is not None and evidence["state"] == "passed_tests"


def _assert_authority_matches_head(
    lifecycle: DetachedWorktreeLifecycle,
    config: MutationSuiteConfig,
    source_revision: str,
    repo_root: Path,
) -> None:
    runner_paths = dict.fromkeys(
        (
            *SHARED_AUTHORITY_RELATIVE_PATHS,
            *config.authority_relative_paths,
        )
    )
    for relative_path in runner_paths:
        current_authority = (repo_root / relative_path).read_bytes()
        committed_authority = lifecycle.git(
            ("show", f"{source_revision}:{relative_path}")
        ).stdout.encode("utf-8")
        if current_authority != committed_authority:
            raise RuntimeError(f"mutation authority must match HEAD: {relative_path}")


def _link_dependency(source_root: Path, worktree: Path, relative_path: str) -> None:
    source = source_root / relative_path
    if not source.exists():
        raise RuntimeError(f"mutation dependency is missing: {relative_path}")
    target = worktree / relative_path
    _remove_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)


def _witness_environment(mutant: Mutant) -> dict[str, str]:
    environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        **({"PATH": path} if (path := os.environ.get("PATH")) else {}),
        **mutant.get("environment", {}),
    }
    if not environment.get("PATH"):
        raise RuntimeError("mutation witness environment requires PATH")
    if environment["PYTHONDONTWRITEBYTECODE"] != "1":
        raise RuntimeError("mutation witness environment must disable bytecode writes")
    return environment


def _count_occurrences(source: str, target: str) -> int:
    if not target:
        return 0
    count = 0
    offset = 0
    while True:
        index = source.find(target, offset)
        if index < 0:
            return count
        count += 1
        offset = index + len(target)


def _cleanup_report(cleanup: CleanupResult) -> dict[str, object]:
    report = cleanup.to_report()
    output = report.pop("output", None)
    if isinstance(output, str):
        report["outputDigest"] = _digest(output)
    return report


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _digest(value: str | bytes) -> str:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(encoded).hexdigest()
