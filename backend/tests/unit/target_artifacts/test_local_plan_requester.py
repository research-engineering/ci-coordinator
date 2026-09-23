from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    TargetAdapterFileBinding,
    digest_adapter_file,
    parse_target_execution_registry,
)
from ci_coordinator.target_artifacts import (
    TargetArtifactsRenderError,
    parse_target_artifacts_source,
    render_target_artifacts,
)
from ci_coordinator.target_artifacts.cli import main
from ci_coordinator.target_artifacts.requester import render_plan_requester

_ROOT = Path(__file__).resolve().parents[4]


def test_requester_source_package_and_explicit_target_output_are_identical(tmp_path: Path) -> None:
    expected = (_ROOT / LOCAL_PLAN_REQUEST_WORKFLOW_PATH).read_bytes()
    assert render_plan_requester() == expected
    output = tmp_path / LOCAL_PLAN_REQUEST_WORKFLOW_PATH
    assert main(["check-requester", "--output", str(output)]) == 1
    assert main(["render-requester", "--output", str(output)]) == 0
    assert output.read_bytes() == expected
    assert main(["check-requester", "--output", str(output)]) == 0
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [output]
    output.write_bytes(expected + b"# drift\n")
    assert main(["check-requester", "--output", str(output)]) == 1


def test_requester_render_rejects_an_invalid_output_without_partial_write(tmp_path: Path) -> None:
    output = tmp_path / "directory"
    output.mkdir()
    assert main(["render-requester", "--output", str(output)]) == 2
    assert output.is_dir()
    assert tuple(output.iterdir()) == ()


@pytest.mark.parametrize(
    "invalid",
    [b"", b"missing-line-ending", b"null\x00\n", b"x" * 131_072 + b"\n"],
    ids=("empty", "missing-line-ending", "nul-byte", "oversized-resource"),
)
def test_packaged_requester_admission_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: bytes,
) -> None:
    (tmp_path / "trusted-plan-request.yml").write_bytes(invalid)
    monkeypatch.setattr("ci_coordinator.target_artifacts.requester.files", lambda _: tmp_path)
    with pytest.raises(ValueError, match="packaged plan requester"):
        render_plan_requester()
    assert main(["render-requester", "--output", str(tmp_path / "result.yml")]) == 2
    assert not (tmp_path / "result.yml").exists()


def test_local_artifact_render_binds_the_canonical_requester_digest() -> None:
    path = _ROOT / "fixtures/target-repository/.ci-coordinator/target-artifacts-source.v1.json"
    document = json.loads(path.read_bytes())
    document["executionWorkflows"][0]["planRequestWorkflowRef"] = LOCAL_PLAN_REQUEST_WORKFLOW_REF
    source = parse_target_artifacts_source(json.dumps(document).encode())
    with pytest.raises(TargetArtifactsRenderError, match="local requester"):
        render_target_artifacts(source)
    requester = TargetAdapterFileBinding(
        path=LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
        sha256=digest_adapter_file(render_plan_requester()),
    )
    source = replace(
        source,
        adapter_workflow_files=tuple(
            sorted((*source.adapter_workflow_files, requester), key=lambda row: row.path)
        ),
    )
    registry = parse_target_execution_registry(render_target_artifacts(source).execution_registry)
    assert registry is not None
    assert registry.workflows[0].plan_request_workflow_ref == LOCAL_PLAN_REQUEST_WORKFLOW_REF
    assert requester in registry.adapter_files
    changed = replace(
        source,
        adapter_workflow_files=tuple(
            replace(row, sha256="f" * 64) if row == requester else row
            for row in source.adapter_workflow_files
        ),
    )
    with pytest.raises(TargetArtifactsRenderError, match="local requester"):
        render_target_artifacts(changed)
