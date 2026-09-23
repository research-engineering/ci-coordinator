from __future__ import annotations

import json
import shutil
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import ci_test_execution as execution
from scripts.bounded_git import run_git
from scripts.bounded_process import CommandResult
from scripts.ci_test_plan import candidate_files
from scripts.ci_test_report import ModuleResult, NativeReport
from scripts.tests.test_ci_test_plan import _epoch, _native, _plan


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for plugin in ("ci_test_plan.py", "ci_test_report.py", "python_coverage_diagnostics.py"):
        shutil.copyfile(root / "scripts" / plugin, scripts / plugin)
    tests = tmp_path / "backend/tests"
    tests.mkdir(parents=True)
    (tmp_path / "backend/pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nmarkers = ["control"]\n'
    )
    (tests / "test_control.py").write_text(
        "import pytest\n@pytest.mark.control\ndef test_control():\n    assert True\n"
    )
    (tests / "test_subject.py").write_text("def test_subject():\n    assert True\n")
    run_git(tmp_path, ("init", "--quiet"))
    monkeypatch.setattr(execution, "ROOT", tmp_path)
    return tests


@pytest.mark.parametrize(
    "case",
    [
        "pass",
        "module-skip",
        "importorskip",
        "keyword",
        "marker",
        "deselect",
        "silent",
        "ignore",
        "ignore-hook",
        "filename",
        "empty",
        "collection-error",
    ],
)
def test_native_collection_reconciles_independent_files_before_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    tests = _project(tmp_path, monkeypatch)
    subject = tests / "test_subject.py"
    config = tmp_path / "backend/pyproject.toml"
    if case == "module-skip":
        subject.write_text(
            'import pytest\npytest.skip("missing witness", allow_module_level=True)\n'
        )
    elif case == "importorskip":
        subject.write_text('import pytest\npytest.importorskip("ci_missing_dependency_615dab")\n')
    elif case in {"keyword", "marker"}:
        config.write_text(
            config.read_text() + f'addopts = "-{"k" if case == "keyword" else "m"} control"\n'
        )
    elif case in {"deselect", "silent"}:
        callback = (
            "    config.hook.pytest_deselected(items=removed)\n" if case == "deselect" else ""
        )
        (tests / "conftest.py").write_text(
            "def pytest_collection_modifyitems(config, items):\n"
            "    removed = [item for item in items if item.path.name == 'test_subject.py']\n"
            + callback
            + "    items[:] = [item for item in items if item not in removed]\n"
        )
    elif case == "ignore":
        (tests / "conftest.py").write_text("collect_ignore = ['test_subject.py']\n")
    elif case == "ignore-hook":
        (tests / "conftest.py").write_text(
            "def pytest_ignore_collect(collection_path):\n"
            "    return collection_path.name == 'test_subject.py'\n"
        )
    elif case == "filename":
        config.write_text(config.read_text() + 'python_files = ["test_control.py"]\n')
    elif case == "empty":
        subject.write_text("SENTINEL = True\n")
    elif case == "collection-error":
        subject.write_text('raise RuntimeError("collection canary")\n')
    files = candidate_files(tmp_path, ("backend/tests",))
    assert files == ("backend/tests/test_control.py", "backend/tests/test_subject.py")
    output = tmp_path / "diagnostics/native.json"
    if case == "collection-error":
        with pytest.raises(RuntimeError, match="native process failed"):
            execution._pytest(("backend/tests",), output, collect=True)
    else:
        execution._pytest(("backend/tests",), output, collect=True)
    report = NativeReport.model_validate_json(output.read_bytes())
    assert "backend/tests/test_control.py::test_control" in [node.node_id for node in report.nodes]
    if case == "pass":
        execution.admit_collection(report, files)
    else:
        with pytest.raises(ValueError, match="independent candidate population"):
            execution.admit_collection(report, files)


@pytest.mark.parametrize(
    "change",
    [
        "unfinished",
        "duplicate-module",
        "duplicate-node",
        "foreign-module",
        "pre-missing-node",
        "pre-duplicate-node",
        "pre-foreign-node",
        "pre-wrong-file",
    ],
)
def test_collection_admission_rejects_independent_report_substitutions(change: str) -> None:
    plan = _plan()
    report = _native(plan.nodes)
    if change == "unfinished":
        report = report.model_copy(update={"collection_finished": False})
    elif change == "duplicate-module":
        report = report.model_copy(update={"modules": (*report.modules, report.modules[0])})
    elif change == "duplicate-node":
        nodes = (*report.nodes, report.nodes[0])
        report = report.model_copy(update={"nodes": nodes, "collected_nodes": nodes})
    elif change.startswith("pre-"):
        before_nodes = list(report.collected_nodes)
        if change == "pre-missing-node":
            before_nodes.pop()
        elif change == "pre-duplicate-node":
            before_nodes.append(before_nodes[0])
        elif change == "pre-foreign-node":
            before_nodes[0] = before_nodes[0].model_copy(
                update={"node_id": before_nodes[0].node_id + "_other"}
            )
        else:
            before_nodes[0] = before_nodes[0].model_copy(update={"file": "backend/tests/other.py"})
        report = report.model_copy(update={"collected_nodes": tuple(before_nodes)})
    else:
        report = report.model_copy(
            update={"modules": (ModuleResult(file="backend/tests/foreign.py", outcome="passed"),)}
        )
    with pytest.raises(ValueError, match="independent candidate population"):
        execution.admit_collection(report, plan.candidate_files)


@pytest.mark.parametrize("mode", ["collect", "execute", "deselect"])
def test_native_hypothesis_marker_is_final_metadata_not_a_selection_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    tests = _project(tmp_path, monkeypatch)
    (tests / "test_subject.py").write_text(
        "from hypothesis import given, strategies as st\n"
        "@given(st.integers())\n"
        "def test_subject(value):\n"
        "    assert isinstance(value, int)\n"
    )
    if mode == "deselect":
        config = tmp_path / "backend/pyproject.toml"
        config.write_text(config.read_text() + 'addopts = "-m control"\n')
    files = candidate_files(tmp_path, ("backend/tests",))
    output = tmp_path / "diagnostics/native.json"
    execution._pytest(("backend/tests",), output, collect=mode != "execute")
    report = NativeReport.model_validate_json(output.read_bytes())
    identity = "backend/tests/test_subject.py::test_subject"
    before = next(node for node in report.collected_nodes if node.node_id == identity)
    assert before.markers == ()
    if mode == "deselect":
        assert report.deselected_node_ids == (identity,)
        assert identity not in {node.node_id for node in report.nodes}
        with pytest.raises(ValueError, match="independent candidate population"):
            execution.admit_collection(report, files)
        return
    after = next(node for node in report.nodes if node.node_id == identity)
    assert after.markers == ("hypothesis",)
    assert before.node_id == after.node_id
    assert before.file == after.file
    assert execution.admit_collection(report, files) == ()
    if mode == "execute":
        assert execution.admit_outcomes(report)[identity] == "passed"


def test_failed_native_execution_keeps_raw_report_without_success_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests = _project(tmp_path, monkeypatch)
    (tests / "test_subject.py").write_text(
        'def test_subject():\n    assert False, "failure canary"\n'
    )
    output = tmp_path / "diagnostics/native.json"
    with pytest.raises(RuntimeError, match="native process failed"):
        execution._pytest(("backend/tests",), output)
    report = NativeReport.model_validate_json(output.read_bytes())
    assert report.exit_status == 1
    assert any(phase.phase == "call" and phase.outcome == "failed" for phase in report.phases)
    diagnostic = json.loads(output.with_name("process.json").read_bytes())
    assert diagnostic["evidence_class"] == "untrusted-diagnostic"
    assert diagnostic["exit_status"] == 1
    assert "failure canary" in diagnostic["stdout_tail"]
    assert not list(tmp_path.rglob("receipt.json"))


@pytest.mark.parametrize("outcome", ["passed", "failed", "timeout"])
def test_process_diagnostic_precedes_byte_bounded_multibyte_console_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    stdout = "\u2603" * 30_000 + "stdout suffix"
    stderr = "\U0001f680" * 20_000 + "stderr suffix"
    result = (
        CommandResult(None, stdout, stderr, "timeout canary", "timeout")
        if outcome == "timeout"
        else CommandResult(0 if outcome == "passed" else 1, stdout, stderr)
    )
    path = tmp_path / "process.json"

    class ReceiptCheckingSink(StringIO):
        def write(self, text: str) -> int:
            receipt = execution.ProcessDiagnostic.model_validate_json(path.read_bytes())
            assert receipt.exit_status == result.status
            assert receipt.failure_kind == result.failure_kind
            assert receipt.error == result.error
            return super().write(text)

    stdout_sink, stderr_sink = ReceiptCheckingSink(), ReceiptCheckingSink()
    monkeypatch.setattr(execution, "spawn", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        execution,
        "sys",
        SimpleNamespace(executable="owned-python", stdout=stdout_sink, stderr=stderr_sink),
    )
    if outcome == "passed":
        execution._run(("probe",), diagnostics=tmp_path)
    else:
        with pytest.raises(RuntimeError, match="native process failed"):
            execution._run(("probe",), diagnostics=tmp_path)
    receipt = execution.ProcessDiagnostic.model_validate_json(path.read_bytes())
    assert receipt.stdout_utf8_bytes == len(stdout.encode("utf-8"))
    assert receipt.stderr_utf8_bytes == len(stderr.encode("utf-8"))
    assert receipt.output_truncated
    assert 65_533 <= len(receipt.stdout_tail.encode("utf-8")) <= 65_536
    assert 65_533 <= len(receipt.stderr_tail.encode("utf-8")) <= 65_536
    assert stdout.endswith(receipt.stdout_tail)
    assert stderr.endswith(receipt.stderr_tail)
    assert stdout_sink.getvalue() == receipt.stdout_tail
    assert stderr_sink.getvalue().endswith(receipt.stderr_tail)
    marker = stderr_sink.getvalue().removesuffix(receipt.stderr_tail)
    assert marker.startswith("[native output truncated:")
    assert len(marker.encode("utf-8")) < 256


def test_console_failure_cannot_erase_the_returned_process_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = CommandResult(1, "failure details", "")
    monkeypatch.setattr(execution, "spawn", lambda *args, **kwargs: result)

    class UnavailableSink(StringIO):
        def write(self, text: str) -> int:
            raise OSError("console unavailable")

    monkeypatch.setattr(
        execution,
        "sys",
        SimpleNamespace(executable="owned-python", stdout=UnavailableSink(), stderr=StringIO()),
    )
    with pytest.raises(OSError, match="console unavailable"):
        execution._run(("probe",), diagnostics=tmp_path)
    receipt = execution.ProcessDiagnostic.model_validate_json(
        (tmp_path / "process.json").read_bytes()
    )
    assert receipt.exit_status == 1
    assert receipt.stdout_tail == result.stdout
    assert receipt.stderr_tail == ""
    assert receipt.stdout_utf8_bytes == len(result.stdout)
    assert receipt.stderr_utf8_bytes == 0
    assert not receipt.output_truncated


@pytest.mark.parametrize("serial", [False, True])
def test_failed_shard_never_writes_a_success_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, serial: bool
) -> None:
    plan = _plan()
    monkeypatch.setattr(execution, "current_epoch", _epoch)
    observed: dict[str, object] = {}

    def timeout(*args: object, **kwargs: object) -> CommandResult:
        observed.update(kwargs)
        observed["arguments"] = args[1]
        return CommandResult(None, "", "timeout canary", "timeout", "timeout")

    monkeypatch.setattr(execution, "spawn", timeout)
    output = tmp_path / "shard"
    diagnostic = tmp_path / "diagnostics"
    with pytest.raises(RuntimeError, match="native process failed"):
        execution.run_shard(
            plan, "serial" if serial else plan.assignments[0].shard, output, diagnostic
        )
    assert observed["timeout_seconds"] == (2_400 if serial else 900)
    arguments = observed["arguments"]
    assert isinstance(arguments, tuple)
    assert "faulthandler_timeout=120" in arguments
    assert "--durations=25" in arguments
    assert "--durations=0" not in arguments
    assert not (output / "receipt.json").exists()
    assert not (diagnostic / "native.json").exists()
    result = json.loads((diagnostic / "process.json").read_bytes())
    assert result["failure_kind"] == "timeout"
    assert result["exit_status"] is None
    assert list(output.iterdir()) == []


def test_diagnostic_cannot_be_nested_in_admitted_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(execution, "current_epoch", _epoch)
    plan = _plan()
    output = tmp_path / "shard"
    with pytest.raises(ValueError, match="separate"):
        execution.run_shard(plan, plan.assignments[0].shard, output, output / "diagnostics")


def test_candidate_inventory_uses_declared_grammar_and_keeps_untracked_tests(
    tmp_path: Path,
) -> None:
    files = (
        "backend/tests/test_first.py",
        "backend/tests/nested/last_test.py",
        "backend/tests/support.py",
        "scripts/tests/test_tool.py",
        "scripts/conformance/explicit_case.py",
        "scripts/conformance/outside_test.py",
    )
    for file in files:
        path = tmp_path / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    run_git(tmp_path, ("init", "--quiet"))
    run_git(tmp_path, ("add", "backend/tests/test_first.py"))
    assert candidate_files(
        tmp_path, ("backend/tests", "scripts/tests", "scripts/conformance/explicit_case.py")
    ) == (
        "backend/tests/nested/last_test.py",
        "backend/tests/test_first.py",
        "scripts/conformance/explicit_case.py",
        "scripts/tests/test_tool.py",
    )


@pytest.mark.parametrize(
    "case", ["owned-empty", "added-test", "changed-empty", "second-empty", "renamed"]
)
def test_only_exact_external_qualification_may_leave_an_empty_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    source_root = Path(__file__).resolve().parents[2]
    _project(tmp_path, monkeypatch)
    original = "scripts/tests/test_diagram_process.py"
    path = tmp_path / original
    path.parent.mkdir(parents=True)
    shutil.copyfile(source_root / original, path)
    if case == "added-test":
        with path.open("a") as output:
            output.write("\ndef test_new_native_case():\n    assert True\n")
    elif case == "changed-empty":
        with path.open("a") as output:
            output.write("\nNEW_LAUNCHER_POLICY = True\n")
    elif case == "second-empty":
        path.with_name("test_other.py").write_text("SENTINEL = True\n")
    elif case == "renamed":
        path.rename(path.with_name("test_other.py"))
    files = candidate_files(tmp_path, ("backend/tests", "scripts/tests"))
    report_path = tmp_path / "diagnostics/native.json"
    execution._pytest(("backend/tests", "scripts/tests"), report_path)
    report = NativeReport.model_validate_json(report_path.read_bytes())
    if case == "owned-empty":
        assert execution.admit_collection(report, files) == (original,)
        assert original in [module.file for module in report.modules]
        assert original not in {node.file for node in report.nodes}
        with pytest.raises(ValueError, match="independent candidate population"):
            execution.admit_collection(
                report.model_copy(
                    update={
                        "modules": tuple(
                            module for module in report.modules if module.file != original
                        )
                    }
                ),
                files,
            )
    elif case == "added-test":
        assert execution.admit_collection(report, files) == ()
        assert execution.admit_outcomes(report)[original + "::test_new_native_case"] == "passed"
    else:
        with pytest.raises(ValueError, match=r"candidate population|source changed"):
            execution.admit_collection(report, files)
