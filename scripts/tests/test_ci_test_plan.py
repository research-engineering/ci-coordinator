from __future__ import annotations

import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from scripts import ci_test_execution as execution
from scripts.ci_test_execution import ShardReceipt, admit_outcomes, admit_shards
from scripts.ci_test_plan import (
    Epoch,
    artifact_bytes,
    build_plan,
    plan_digest,
    write_artifact,
)
from scripts.ci_test_plan import (
    TestNode as NativeNode,
)
from scripts.ci_test_plan import (
    TestPlan as NativePlan,
)
from scripts.ci_test_report import ModuleResult, NativeReport, PhaseResult


def _epoch() -> Epoch:
    return Epoch(
        source_sha="a" * 40, run_id="1", attempt="1", python_version="3.13.15", lock_digest="b" * 64
    )


@pytest.mark.parametrize(
    "owner",
    [
        "backend/pyproject.toml",
        "backend/uv.lock",
        "backend/requirements-dev.lock",
        "mise.toml",
        "mise.lock",
        "pnpm-lock.yaml",
    ],
)
def test_epoch_binds_each_existing_tracked_toolchain_owner(
    monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    tracked = subprocess.run(
        ("git", "ls-files", "-z"), cwd=root, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    assert owner in tracked
    monkeypatch.setenv("GITHUB_SHA", head)
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    baseline = execution.current_epoch(root)

    def changed(path: Path) -> bytes:
        content = artifact_bytes(path)
        return content + b"\n" if path == root / owner else content

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(execution, "artifact_bytes", changed)
    assert execution.current_epoch(root).lock_digest != baseline.lock_digest


def _plan() -> NativePlan:
    nodes = tuple(
        NativeNode(
            node_id=f"{file}::test_case[{index}]",
            file=file,
            markers=markers,
            fixtures=("tmp_path",),
        )
        for file, markers in (
            *((f"backend/tests/unit/test_{index}.py", ()) for index in range(4)),
            *(
                (f"backend/tests/integration/test_{index}.py", ("persistence",))
                for index in range(4)
            ),
            *((f"scripts/tests/test_tool_{index}.py", ()) for index in range(3)),
        )
        for index in range(2)
    )
    return build_plan(
        nodes,
        _epoch(),
        {"backend/tests/unit/test_0.py": 90.0},
        expected_files=tuple(sorted({node.file for node in nodes})),
    )


def _native(nodes: tuple[NativeNode, ...]) -> NativeReport:
    return NativeReport(
        nodes=nodes,
        collected_nodes=nodes,
        modules=tuple(
            ModuleResult(file=file, outcome="passed")
            for file in sorted({node.file for node in nodes})
        ),
        deselected_node_ids=(),
        collection_issues=(),
        collection_finished=True,
        exit_status=0,
        elapsed_seconds=1.0,
        phases=tuple(
            PhaseResult(
                node_id=node.node_id,
                phase=phase,
                outcome="passed",
                expected_failure=False,
                skip_reason=None,
                elapsed_seconds=0.01,
            )
            for node in nodes
            for phase in ("setup", "call", "teardown")
        ),
    )


def _shards(plan: NativePlan, root: Path) -> None:
    for assignment in plan.assignments:
        folder = root / f"shard-{assignment.shard}"
        folder.mkdir(parents=True)
        (folder / ".coverage").write_bytes(b"coverage fixture")
        write_artifact(
            folder / "receipt.json",
            ShardReceipt(
                epoch=plan.epoch,
                plan_digest=plan_digest(plan),
                shard=assignment.shard,
                coverage_sha256=hashlib.sha256(b"coverage fixture").hexdigest(),
                native=_native(
                    tuple(node for node in plan.nodes if node.node_id in assignment.node_ids)
                ),
            ),
        )


def test_lpt_is_deterministic_file_cohesive_and_exhaustive() -> None:
    plan = _plan()
    assert plan == build_plan(
        tuple(reversed(plan.nodes)),
        _epoch(),
        {"backend/tests/unit/test_0.py": 90.0},
        expected_files=plan.candidate_files,
    )
    assigned = [node for item in plan.assignments for node in item.node_ids]
    assert len(assigned) == len(set(assigned)) == len(plan.nodes)
    assert {item.shard for item in plan.assignments} == {
        *(f"tooling-{i}" for i in range(1, 4)),
        *(f"{cohort}-{i}" for cohort in ("backend", "postgres") for i in range(1, 5)),
    }
    assert all(item.estimated_seconds > 0 for item in plan.assignments)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-shard",
        "extra-shard",
        "duplicate-node",
        "extra-node",
        "missing-node",
        "foreign-file",
        "empty-shard",
        "candidate-file",
    ],
)
def test_partition_admission_rejects_population_drift(mutation: str) -> None:
    value = _plan().model_dump(mode="json")
    assignments = value["assignments"]
    if mutation == "missing-shard":
        assignments.pop()
    elif mutation == "extra-shard":
        assignments.append(assignments[0])
    elif mutation == "duplicate-node":
        value["nodes"].append(value["nodes"][0])
    elif mutation == "extra-node":
        assignments[0]["node_ids"].append("foreign::test")
    elif mutation == "missing-node":
        assignments[0]["node_ids"].pop()
    elif mutation == "foreign-file":
        assignments[0]["files"][0] = "../../foreign.py"
    elif mutation == "candidate-file":
        value["candidate_files"].pop()
    else:
        assignments[0]["files"] = []
        assignments[0]["node_ids"] = []
    with pytest.raises(ValueError):
        NativePlan.model_validate_json(json.dumps(value))


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "extra", "failed", "skipped", "xfail", "teardown", "exit", "empty"],
)
def test_outcome_admission_rejects_incomplete_or_weaker_oracles(mutation: str) -> None:
    report = _native(_plan().nodes)
    phases = list(report.phases)
    if mutation == "missing":
        phases.pop()
    elif mutation == "duplicate":
        phases.append(phases[0])
    elif mutation == "extra":
        phases[0] = phases[0].model_copy(update={"node_id": "foreign"})
    elif mutation in {"failed", "skipped"}:
        phases[1] = phases[1].model_copy(update={"outcome": mutation})
    elif mutation == "xfail":
        phases[1] = phases[1].model_copy(update={"expected_failure": True})
    elif mutation == "teardown":
        phases[2] = phases[2].model_copy(update={"outcome": "failed"})
    elif mutation == "exit":
        report = report.model_copy(update={"exit_status": 1})
    else:
        report = report.model_copy(update={"nodes": ()})
    with pytest.raises(ValueError):
        admit_outcomes(report.model_copy(update={"phases": tuple(phases)}))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "symlink",
        "coverage",
        "source",
        "run",
        "attempt",
        "plan",
        "shard",
        "collection",
        "markers",
        "fixtures",
    ],
)
def test_artifact_join_rejects_each_independent_binding_substitution(
    tmp_path: Path, mutation: str
) -> None:
    plan = _plan()
    _shards(plan, tmp_path)
    assert len(admit_shards(plan, tmp_path)) == len(plan.assignments)
    folder = tmp_path / f"shard-{plan.assignments[0].shard}"
    path = folder / "receipt.json"
    value = json.loads(path.read_bytes())
    if mutation == "missing":
        path.unlink()
    elif mutation == "extra":
        (folder / "unexpected").touch()
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(folder / ".coverage")
    elif mutation == "coverage":
        (folder / ".coverage").write_bytes(b"substituted")
    else:
        if mutation in {"source", "run", "attempt"}:
            field = {"source": "source_sha", "run": "run_id", "attempt": "attempt"}[mutation]
            value["epoch"][field] = "c" * 40 if mutation == "source" else "2"
        elif mutation == "plan":
            value["plan_digest"] = "f" * 64
        elif mutation == "shard":
            value["shard"] = "postgres-4"
        elif mutation in {"markers", "fixtures"}:
            metadata = value["native"]["nodes"][0][mutation]
            value["native"]["nodes"][0][mutation] = sorted([*metadata, "late_substitution"])
        else:
            value["native"]["nodes"].pop()
        path.write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        admit_shards(plan, tmp_path)


def test_bounded_json_artifact_does_not_follow_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"{}")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        artifact_bytes(link)


@pytest.mark.parametrize(
    "argv",
    [
        ["plan", "--plan", "unused", "--shard", "backend-1"],
        ["run", "--plan", "unused", "--output", "unused"],
        [
            "combine",
            "--plan",
            "unused",
            "--shard",
            "backend-1",
            "--artifacts",
            "unused",
            "--output",
            "unused",
        ],
    ],
)
def test_cli_rejects_irrelevant_or_incomplete_arguments_before_io(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    def unexpected(*_args: object) -> bytes:
        raise AssertionError("inadmissible arguments reached artifact I/O")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(execution, "artifact_bytes", unexpected)
    monkeypatch.setattr(sys, "argv", ["ci-test", *argv])
    assert execution.main() == 1


@pytest.mark.parametrize(
    "case", ["passed", "long-id", "skipped", "failed", "teardown", "approved-skips"]
)
def test_real_pytest_plugin_preserves_collection_and_every_phase(tmp_path: Path, case: str) -> None:
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / (
        "scripts/tests/test_target_control_source_admission.py"
        if case == "approved-skips"
        else "backend/tests/test_example.py"
    )
    source.parent.mkdir(parents=True)
    bodies = {
        "passed": "assert True",
        "long-id": "assert len(value) == 70_000",
        "skipped": 'pytest.skip("unexpected")',
        "failed": "assert False",
        "teardown": "assert True",
    }
    fixture = (
        "@pytest.fixture\ndef resource():\n    yield\n    assert False\n"
        if case == "teardown"
        else ""
    )
    if case == "approved-skips":
        source.write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('loader,reason', [\n"
            "    (b'arguments[1](\"node:http\");', 'ambient loader or dynamic-code'),\n"
            "    (b'arg\\\\u0075ments[1](\"node:http\");', 'escaped identifier'),\n"
            "])\n"
            "def test_real_esbuild_omits_commonjs_loader_alias_but_source_admission_rejects_it"
            "(loader, reason):\n"
            "    pytest.skip('repository-quality owns the installed real-esbuild falsifier')\n"
        )
    elif case == "long-id":
        source.write_text(
            "import pytest\n@pytest.mark.parametrize('value', ['x' * 70_000])\n"
            f"def test_example(value):\n    {bodies[case]}\n"
        )
    else:
        source.write_text(
            f"import pytest\n{fixture}\ndef test_example({'resource' if fixture else ''}):\n"
            f"    {bodies[case]}\n"
        )
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    output = tmp_path / "report.json"
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(config),
            f"--rootdir={tmp_path}",
            "-p",
            "scripts.ci_test_report",
            f"--ci-report={output}",
            str(source),
        ),
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    report = NativeReport.model_validate_json(output.read_bytes())
    assert completed.returncode == report.exit_status
    if case == "approved-skips":
        assert admit_outcomes(report) == dict.fromkeys(execution._ALLOWED_SKIPS, "skipped")
        return
    expected_id = "backend/tests/test_example.py::test_example"
    if case == "long-id":
        expected_id += "[" + "x" * 70_000 + "]"
    assert [node.node_id for node in report.nodes] == [expected_id]
    if case in {"passed", "long-id"}:
        assert admit_outcomes(report) == {expected_id: "passed"}
    else:
        with pytest.raises(ValueError):
            admit_outcomes(report)


@pytest.mark.parametrize("collect", [True, False])
def test_native_runner_preserves_backend_execution_and_config_import_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collect: bool
) -> None:
    root = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for plugin in ("ci_test_plan.py", "ci_test_report.py", "python_coverage_diagnostics.py"):
        shutil.copyfile(root / "scripts" / plugin, scripts / plugin)
    integration = tmp_path / "backend/tests/integration"
    integration.mkdir(parents=True)
    unit = tmp_path / "backend/tests/unit"
    unit.mkdir()
    (tmp_path / "backend/pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src", "tests/unit", ".."]\n'
    )
    (integration / "__init__.py").write_text("")
    (integration / "_support.py").write_text("SENTINEL = 'integration-helper'\n")
    (unit / "unit_support.py").write_text("SENTINEL = 'unit-helper'\n")
    (integration / "test_imports.py").write_text(
        "from tests.integration._support import SENTINEL as integration\n"
        "from unit_support import SENTINEL as unit\n"
        "def test_helpers():\n"
        "    assert (integration, unit) == ('integration-helper', 'unit-helper')\n"
    )
    monkeypatch.setattr(execution, "ROOT", tmp_path)
    output = tmp_path / "report.json"
    execution._pytest(("backend/tests/integration/test_imports.py",), output, collect=collect)
    report = NativeReport.model_validate_json(artifact_bytes(output))
    expected_id = "backend/tests/integration/test_imports.py::test_helpers"
    assert report.exit_status == 0
    assert tuple(node.node_id for node in report.nodes) == (expected_id,)
    if collect:
        assert report.phases == ()
    else:
        assert admit_outcomes(report) == {expected_id: "passed"}


def test_workflow_matrix_and_required_join_match_the_partition_exactly() -> None:
    root = Path(__file__).resolve().parents[2]
    jobs = YAML(typ="safe").load(root / ".github/workflows/python-persistence.yml")["jobs"]
    assert sorted(jobs["native-test-shards"]["strategy"]["matrix"]["shard"]) == [
        item.shard for item in _plan().assignments
    ]
    assert jobs["native-test-shards"]["strategy"]["fail-fast"] is False
    scanner_steps = [
        step
        for step in jobs["native-test-shards"]["steps"]
        if step.get("name") == "Install the admitted scanner for native falsifiers"
    ]
    assert len(scanner_steps) == 1
    assert scanner_steps[0]["if"] == "startsWith(matrix.shard, 'tooling-')"
    assert jobs["postgres-witness"]["needs"] == ["native-test-plan", "native-test-shards"]
    assert any(
        "ci_test_execution combine" in step.get("run", "")
        for step in jobs["postgres-witness"]["steps"]
    )
    assert "postgres-witness" in jobs["pull-request-gate"]["needs"]
    assert jobs["pull-request-gate"]["if"] == "${{ always() }}"


@pytest.mark.parametrize(
    "failure", [None, "aggregate", "owner", "changed-critical", "malformed-coverage"]
)
def test_real_coverage_join_preserves_all_independent_floor_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    backend = tmp_path / "backend"
    source = backend / "src/example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        'import sys\nif sys.argv[1] == "left":\n    result = 1\nelse:\n    result = 0\n'
    )
    (backend / "pyproject.toml").write_text(
        "[tool.coverage.run]\nbranch = true\n[tool.coverage.report]\n"
        f"fail_under = {100 if failure in {None, 'aggregate', 'malformed-coverage'} else 0}\n"
    )
    (backend / "coverage-policy.v1.json").write_text(
        json.dumps(
            {
                "schemaVersion": "ci-coordinator-python-coverage-policy/v1",
                "policyId": "fixture",
                "sourceRoot": "backend",
                "nonClaims": ["bounded coverage-join fixture"],
                "changedCriticalMinimum": {
                    "branchPercent": 100 if failure == "changed-critical" else 0,
                    "statementPercent": 0,
                },
                "ownerGroups": [
                    {
                        "ownerId": "example",
                        "paths": ["src/example.py"],
                        "statementPercent": 0,
                        "branchPercent": 100 if failure == "owner" else 0,
                    }
                ],
            }
        )
    )
    coverage_files: list[Path] = []
    for side in ("left", "right"):
        path = backend / f".coverage.{side}"
        subprocess.run(
            (sys.executable, "-m", "coverage", "run", "--rcfile=pyproject.toml", str(source), side),
            cwd=backend,
            env={**os.environ, "COVERAGE_FILE": str(path)},
            check=True,
            capture_output=True,
            timeout=30,
        )
        coverage_files.append(path)
    plan = _plan()
    artifacts = tmp_path / "artifacts"
    _shards(plan, artifacts)
    for index, assignment in enumerate(plan.assignments):
        folder = artifacts / f"shard-{assignment.shard}"
        content = coverage_files[
            index % 2 if failure in {None, "malformed-coverage"} else 0
        ].read_bytes()
        if failure == "malformed-coverage" and index == 0:
            content = b"corrupt dataset with a matching receipt hash"
        (folder / ".coverage").write_bytes(content)
        receipt_path = folder / "receipt.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["coverage_sha256"] = hashlib.sha256(content).hexdigest()
        receipt_path.write_text(json.dumps(receipt))

    def epoch() -> Epoch:
        return plan.epoch

    def changed(_root: Path, _environment: Mapping[str, str]) -> tuple[str, ...]:
        return ("backend/src/example.py",)

    monkeypatch.setattr(execution, "ROOT", tmp_path)
    monkeypatch.setattr(execution, "current_epoch", epoch)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(execution, "changed_paths_for_coverage", changed)
    output = tmp_path / "aggregate"
    if failure:
        with pytest.raises((ValueError, RuntimeError)):
            execution.combine(plan, artifacts, output)
        assert not (output / "timings.json").exists()
    else:
        execution.combine(plan, artifacts, output)
        report = json.loads((output / "timings.json").read_bytes())
        assert {row["nodeId"] for row in report["tests"]} == {node.node_id for node in plan.nodes}
        assert all(
            [event["phase"] for event in row["phases"]] == ["setup", "call", "teardown"]
            for row in report["tests"]
        )
    gc.collect()
