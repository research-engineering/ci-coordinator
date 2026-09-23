from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import sys
from collections import defaultdict
from pathlib import Path
from typing import Literal

from coverage import CoverageData
from coverage.exceptions import CoverageException

from scripts.bounded_process import spawn
from scripts.ci_test_plan import (
    EXTERNALLY_QUALIFIED_MODULES,
    Artifact,
    Epoch,
    TestPlan,
    artifact_bytes,
    build_plan,
    candidate_files,
    load_costs,
    plan_digest,
    workflow_inventory,
    write_artifact,
)
from scripts.ci_test_report import NativeReport, PhaseResult
from scripts.python_coverage_policy import (
    changed_paths_for_coverage,
    evaluate_coverage_report,
)
from scripts.python_witness import PYTHON_TEST_PROCESS_TIMEOUT_SECONDS
from scripts.repository_paths import read_repository_regular_file

ROOT = Path(__file__).resolve().parent.parent
_OUTPUT_TAIL_BYTES = 65_536
UNIVERSE = (
    "backend/tests",
    "scripts/tests",
    "scripts/conformance/audit_persistence_byte_contract_test.py",
    "scripts/conformance/native_test_timeout_test.py",
)
_SKIP_PREFIX = (
    "scripts/tests/test_target_control_source_admission.py::"
    "test_real_esbuild_omits_commonjs_loader_alias_but_source_admission_rejects_it"
)
_ALLOWED_SKIPS = frozenset(
    {
        _SKIP_PREFIX + '[arguments[1]("node:http");-ambient loader or dynamic-code]',
        _SKIP_PREFIX + r'[arg\u0075ments[1]("node:http");-escaped identifier]',
    }
)
_SKIP_REASON = "Skipped: repository-quality owns the installed real-esbuild falsifier"


class ShardReceipt(Artifact):
    schema_version: Literal["ci-coordinator-native-test-shard/v2"] = (
        "ci-coordinator-native-test-shard/v2"
    )
    epoch: Epoch
    plan_digest: str
    shard: str
    coverage_sha256: str
    native: NativeReport


class ProcessDiagnostic(Artifact):
    schema_version: Literal["ci-coordinator-native-test-diagnostic/v1"] = (
        "ci-coordinator-native-test-diagnostic/v1"
    )
    evidence_class: Literal["untrusted-diagnostic"] = "untrusted-diagnostic"
    exit_status: int | None
    failure_kind: str | None
    error: str | None
    stdout_utf8_bytes: int
    stderr_utf8_bytes: int
    stdout_tail: str
    stderr_tail: str
    output_truncated: bool


def _run(
    arguments: tuple[str, ...],
    *,
    environment: dict[str, str] | None = None,
    timeout: float = 2_400,
    cwd: Path = ROOT,
    diagnostics: Path | None = None,
    inherited_fds: tuple[int, ...] = (),
) -> None:
    result = spawn(
        sys.executable,
        arguments,
        cwd=cwd,
        env=environment or dict(os.environ),
        max_buffer=64 * 1024 * 1024,
        timeout_seconds=timeout,
        inherited_fds=inherited_fds,
    )
    stdout_tail, stdout_bytes = _diagnostic_tail(result.stdout)
    stderr_tail, stderr_bytes = _diagnostic_tail(result.stderr)
    diagnostic = ProcessDiagnostic(
        exit_status=result.status,
        failure_kind=result.failure_kind,
        error=result.error,
        stdout_utf8_bytes=stdout_bytes,
        stderr_utf8_bytes=stderr_bytes,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        output_truncated=stdout_bytes > _OUTPUT_TAIL_BYTES or stderr_bytes > _OUTPUT_TAIL_BYTES,
    )
    if diagnostics is not None:
        write_artifact(diagnostics / "process.json", diagnostic)
    if diagnostic.output_truncated:
        sys.stderr.write(
            f"[native output truncated: stdout={stdout_bytes} bytes, stderr={stderr_bytes} bytes; "
            f"showing at most {_OUTPUT_TAIL_BYTES} UTF-8 bytes per stream]\n"
        )
    sys.stdout.write(stdout_tail)
    sys.stderr.write(stderr_tail)
    if result.status != 0 or result.error is not None:
        raise RuntimeError(f"native process failed: {result.status}, {result.failure_kind}")


def _diagnostic_tail(text: str) -> tuple[str, int]:
    total_bytes = sum(
        len(text[offset : offset + _OUTPUT_TAIL_BYTES].encode("utf-8"))
        for offset in range(0, len(text), _OUTPUT_TAIL_BYTES)
    )
    tail = text[-_OUTPUT_TAIL_BYTES:].encode("utf-8")[-_OUTPUT_TAIL_BYTES:]
    return tail.decode("utf-8", errors="ignore"), total_bytes


def current_epoch(root: Path = ROOT) -> Epoch:
    result = spawn("git", ("rev-parse", "HEAD"), cwd=root, max_buffer=1_024, timeout_seconds=10)
    if result.status != 0 or result.error:
        raise ValueError("source SHA is unavailable")
    sha = result.stdout.strip()
    if sha != os.environ.get("GITHUB_SHA"):
        raise ValueError("checked-out source differs from the workflow epoch")
    locked = hashlib.sha256()
    for name in (
        "backend/pyproject.toml",
        "backend/uv.lock",
        "backend/requirements-dev.lock",
        "mise.toml",
        "mise.lock",
        "pnpm-lock.yaml",
    ):
        locked.update(name.encode() + b"\0" + artifact_bytes(root / name) + b"\0")
    return Epoch(
        source_sha=sha,
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        python_version=platform.python_version(),
        lock_digest=locked.hexdigest(),
    )


def _pytest(
    files: tuple[str, ...],
    report: Path,
    *,
    coverage: Path | None = None,
    collect: bool = False,
    timeout: float = 2_400,
) -> None:
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + str(ROOT / "backend/src")
    arguments: tuple[str, ...] = (
        "-m",
        "pytest",
        "-c",
        "pyproject.toml",
        f"--rootdir={ROOT}",
        "-p",
        "scripts.ci_test_report",
        "-p",
        "scripts.python_coverage_diagnostics",
        f"--ci-report={report}",
        "--tb=short",
        "--durations=25",
        "--durations-min=0",
    )
    if collect:
        arguments += ("--collect-only", "-qq")
    else:
        arguments += ("-o", "faulthandler_timeout=120")
    if coverage is not None:
        environment["COVERAGE_FILE"] = str(coverage)
        arguments += (
            "--cov=ci_coordinator",
            "--cov-branch",
            "--cov-config=pyproject.toml",
            "--cov-report=",
            "--cov-fail-under=0",
        )
    descriptor = fcntl.fcntl(1, fcntl.F_DUPFD_CLOEXEC, 3)
    try:
        _run(
            (*arguments, f"--ci-progress-fd={descriptor}", *(str(ROOT / file) for file in files)),
            environment=environment,
            cwd=ROOT / "backend",
            diagnostics=report.parent,
            timeout=timeout,
            inherited_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)


def create_plan(path: Path, diagnostics: Path) -> None:
    epoch = current_epoch()
    expected_files = candidate_files(ROOT, UNIVERSE)
    diagnostics.mkdir(parents=True, exist_ok=False)
    report_path = diagnostics / "native.json"
    _pytest(UNIVERSE, report_path, collect=True)
    report = NativeReport.model_validate_json(artifact_bytes(report_path))
    empty_files = admit_collection(report, expected_files)
    if report.exit_status != 0 or report.phases or current_epoch() != epoch:
        raise ValueError("native collection did not close on the exact epoch")
    if candidate_files(ROOT, UNIVERSE) != expected_files:
        raise ValueError("native candidate files changed during collection")
    plan = build_plan(
        report.nodes,
        epoch,
        load_costs(ROOT / "proofkit/ci-test-costs.v1.json"),
        expected_files=expected_files,
        externally_qualified_empty_files=empty_files,
    )
    write_artifact(path, plan)
    print(
        json.dumps(
            {
                "nodes": len(plan.nodes),
                "shards": [item.shard for item in plan.assignments],
                "jobs": workflow_inventory(ROOT),
            }
        )
    )


def admit_collection(report: NativeReport, expected_files: tuple[str, ...]) -> tuple[str, ...]:
    node_files = {node.file for node in report.nodes}
    empty_files = set(expected_files) - node_files
    if (
        report.exit_status != 0
        or not report.collection_finished
        or report.collection_issues
        or report.deselected_node_ids
        or not report.nodes
        or tuple((node.node_id, node.file) for node in report.collected_nodes)
        != tuple((node.node_id, node.file) for node in report.nodes)
        or tuple(module.file for module in report.modules) != expected_files
        or any(module.outcome != "passed" for module in report.modules)
        or expected_files != tuple(sorted(node_files | empty_files))
        or not empty_files <= EXTERNALLY_QUALIFIED_MODULES.keys()
        or tuple(node.node_id for node in report.nodes)
        != tuple(sorted({node.node_id for node in report.nodes}))
    ):
        raise ValueError("native collection does not cover the independent candidate population")
    for file in sorted(empty_files):
        source = read_repository_regular_file(
            ROOT, Path(file), "externally qualified empty module", maximum_bytes=1024 * 1024
        )
        if hashlib.sha256(source).hexdigest() != EXTERNALLY_QUALIFIED_MODULES[file].source_sha256:
            raise ValueError("externally qualified empty module source changed")
    return tuple(sorted(empty_files))


def admit_outcomes(report: NativeReport) -> dict[str, str]:
    if report.exit_status != 0 or not report.nodes:
        raise ValueError("native witness did not succeed")
    nodes = {item.node_id for item in report.nodes}
    if len(nodes) != len(report.nodes):
        raise ValueError("native witness repeats a node")
    phases: dict[str, list[PhaseResult]] = defaultdict(list)
    for phase in report.phases:
        if phase.node_id not in nodes or phase.expected_failure or phase.outcome == "failed":
            raise ValueError("native phase failed or belongs to another collection")
        phases[phase.node_id].append(phase)
    if set(phases) != nodes:
        raise ValueError("native witness lacks terminal outcomes")
    outcomes: dict[str, str] = {}
    for node, events in phases.items():
        names = tuple(event.phase for event in events)
        if names != ("setup", "call", "teardown"):
            raise ValueError("native node lacks complete setup/call/teardown evidence")
        if any(event.outcome != "passed" for event in (events[0], events[2])):
            raise ValueError("native setup or teardown did not pass")
        call = events[1]
        if call.outcome == "skipped" and (
            node not in _ALLOWED_SKIPS or call.skip_reason != _SKIP_REASON
        ):
            raise ValueError("native witness introduced an unapproved skip")
        outcomes[node] = call.outcome
    return outcomes


def run_shard(plan: TestPlan, shard: str, output: Path, diagnostics: Path) -> None:
    if plan.epoch != current_epoch():
        raise ValueError("shard epoch is stale")
    if shard == "serial":
        files: tuple[str, ...] = UNIVERSE
        expected = plan.nodes
    else:
        assignment = next((item for item in plan.assignments if item.shard == shard), None)
        if assignment is None:
            raise ValueError("unknown shard")
        files = assignment.files
        assigned = set(assignment.node_ids)
        expected = tuple(node for node in plan.nodes if node.node_id in assigned)
    if diagnostics == output or output in diagnostics.parents or diagnostics in output.parents:
        raise ValueError("native diagnostics must be separate from successful shard evidence")
    output.mkdir(parents=True, exist_ok=False)
    diagnostics.mkdir(parents=True, exist_ok=False)
    coverage_path = output / ".coverage"
    report_path = diagnostics / "native.json"
    _pytest(
        files,
        report_path,
        coverage=coverage_path,
        timeout=2_400 if shard == "serial" else PYTHON_TEST_PROCESS_TIMEOUT_SECONDS,
    )
    native = NativeReport.model_validate_json(artifact_bytes(report_path))
    empty_files = admit_collection(native, plan.candidate_files if shard == "serial" else files)
    if empty_files != (plan.externally_qualified_empty_files if shard == "serial" else ()):
        raise ValueError("external module disposition differs from the frozen plan")
    if native.nodes != expected or current_epoch() != plan.epoch:
        raise ValueError("execution collection or epoch differs from the frozen plan")
    admit_outcomes(native)
    write_artifact(
        output / "receipt.json",
        ShardReceipt(
            epoch=plan.epoch,
            plan_digest=plan_digest(plan),
            shard=shard,
            coverage_sha256=hashlib.sha256(artifact_bytes(coverage_path)).hexdigest(),
            native=native,
        ),
    )


def admit_shards(plan: TestPlan, directory: Path) -> tuple[ShardReceipt, ...]:
    expected_files = {
        f"shard-{item.shard}/{name}"
        for item in plan.assignments
        for name in ("receipt.json", ".coverage")
    }
    actual_files = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("shard inventory contains a symlink")
        if path.is_file():
            actual_files.add(path.relative_to(directory).as_posix())
    if actual_files != expected_files:
        raise ValueError("shard artifact inventory is missing, duplicated or foreign")
    receipts: list[ShardReceipt] = []
    for assignment in plan.assignments:
        folder = directory / f"shard-{assignment.shard}"
        receipt = ShardReceipt.model_validate_json(artifact_bytes(folder / "receipt.json"))
        expected_nodes = tuple(
            node for node in plan.nodes if node.node_id in set(assignment.node_ids)
        )
        if (
            receipt.epoch != plan.epoch
            or receipt.plan_digest != plan_digest(plan)
            or receipt.shard != assignment.shard
            or receipt.native.nodes != expected_nodes
            or receipt.coverage_sha256
            != hashlib.sha256(artifact_bytes(folder / ".coverage")).hexdigest()
        ):
            raise ValueError("shard evidence differs from its source, plan or coverage binding")
        admit_outcomes(receipt.native)
        admit_collection(receipt.native, assignment.files)
        receipts.append(receipt)
    return tuple(receipts)


def combine(plan: TestPlan, directory: Path, output: Path) -> None:
    if plan.epoch != current_epoch():
        raise ValueError("coverage aggregator epoch is stale")
    receipts = admit_shards(plan, directory)
    paths = tuple(str(directory / f"shard-{item.shard}" / ".coverage") for item in receipts)
    for path in paths:
        dataset = CoverageData(basename=path)
        admitted = CoverageData(no_disk=True)
        try:
            dataset.read()
            if not dataset or not dataset.has_arcs():
                raise ValueError("shard coverage lacks branch measurements")
            admitted.update(dataset)
        except CoverageException as error:
            raise ValueError("shard coverage dataset is invalid") from error
        finally:
            admitted.close(force=True)
            dataset.close(force=True)
    output.mkdir(parents=True, exist_ok=False)
    environment = {**os.environ, "COVERAGE_FILE": str(output / ".coverage")}
    _run(
        ("-m", "coverage", "combine", "--keep", "--rcfile=pyproject.toml", *paths),
        environment=environment,
        cwd=ROOT / "backend",
    )
    coverage_json = output / "coverage.json"
    _run(
        ("-m", "coverage", "json", "--rcfile=pyproject.toml", "-o", str(coverage_json)),
        environment=environment,
        cwd=ROOT / "backend",
    )
    evaluation = evaluate_coverage_report(
        coverage_json,
        repo_root=ROOT,
        changed_paths=changed_paths_for_coverage(ROOT, os.environ),
    )
    if evaluation["state"] != "passed":
        raise ValueError("aggregate owner/critical coverage floors failed")
    print(json.dumps(evaluation))
    (output / "timings.json").write_text(
        json.dumps(timing_summary(plan, receipts), indent=2) + "\n"
    )


def timing_summary(plan: TestPlan, receipts: tuple[ShardReceipt, ...]) -> dict[str, object]:
    phases: dict[str, list[dict[str, object]]] = defaultdict(list)
    for receipt in receipts:
        for event in receipt.native.phases:
            phases[event.node_id].append(event.model_dump())
    return {
        "planDigest": plan_digest(plan),
        "jobs": workflow_inventory(ROOT),
        "shards": [
            {
                "shard": item.shard,
                "elapsedSeconds": item.native.elapsed_seconds,
                "nodes": len(item.native.nodes),
            }
            for item in receipts
        ],
        "tests": [
            {
                "nodeId": node.node_id,
                "file": node.file,
                "shard": receipt.shard,
                "markers": node.markers,
                "fixtures": node.fixtures,
                "phases": phases[node.node_id],
                "disposition": "retained_native_oracle; phase_cost_is_not_cpu_cost",
            }
            for receipt in receipts
            for node in receipt.native.nodes
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("plan", "run", "combine", "compare"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--shard")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    arguments = parser.parse_args()
    try:
        expected = {
            "plan": {"plan", "diagnostics"},
            "run": {"plan", "shard", "output", "diagnostics"},
            "combine": {"plan", "artifacts", "output"},
            "compare": {"plan", "artifacts", "output"},
        }[arguments.operation]
        provided = {
            key
            for key, value in vars(arguments).items()
            if key != "operation" and value is not None
        }
        if provided != expected:
            raise ValueError("operation arguments do not match the exact command contract")
        if arguments.operation == "plan":
            create_plan(arguments.plan.resolve(), arguments.diagnostics.resolve())
            return 0
        plan = TestPlan.model_validate_json(artifact_bytes(arguments.plan))
        if arguments.operation == "run" and arguments.shard and arguments.output:
            run_shard(
                plan, arguments.shard, arguments.output.resolve(), arguments.diagnostics.resolve()
            )
        elif arguments.operation == "combine" and arguments.artifacts and arguments.output:
            combine(plan, arguments.artifacts.resolve(), arguments.output.resolve())
        elif arguments.operation == "compare" and arguments.artifacts and arguments.output:
            receipts = admit_shards(plan, arguments.artifacts.resolve())
            serial = ShardReceipt.model_validate_json(
                artifact_bytes(arguments.output / "receipt.json")
            )
            if (
                plan.epoch != current_epoch()
                or serial.epoch != plan.epoch
                or serial.plan_digest != plan_digest(plan)
                or serial.shard != "serial"
                or serial.native.nodes != plan.nodes
                or serial.coverage_sha256
                != hashlib.sha256(artifact_bytes(arguments.output / ".coverage")).hexdigest()
            ):
                raise ValueError("serial qualification differs from the frozen plan")
            outcomes = {
                node: outcome
                for receipt in receipts
                for node, outcome in admit_outcomes(receipt.native).items()
            }
            if outcomes != admit_outcomes(serial.native):
                raise ValueError("serial and partitioned outcomes differ")
            if (
                admit_collection(serial.native, plan.candidate_files)
                != plan.externally_qualified_empty_files
            ):
                raise ValueError("serial external module disposition differs from the frozen plan")
            print(json.dumps({"sameHeadPopulationAndOutcomes": "passed", "nodes": len(outcomes)}))
        else:
            raise ValueError("operation arguments are incomplete")
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
